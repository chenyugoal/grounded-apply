#!/usr/bin/env python3
"""Actual-CLI daily source rotation with fictional feeds and real PDFs."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

try:
    from scripts.check_schedule import ScheduleCLI
    from scripts.check_search import RESUME
except ModuleNotFoundError:
    from check_schedule import ScheduleCLI
    from check_search import RESUME


GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/fictional-rotation/jobs?content=true"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/fictional-rotation"
LEVER = "https://api.lever.co/v0/postings/fictional-rotation?mode=json&skip=0&limit=100"
SOURCES = [
    {"id": "manual-first", "provider": "manual", "careers_url": "https://example.com/fictional-first"},
    {"id": "blocked-first", "provider": "greenhouse", "board": "fictional-rotation"},
    {"id": "manual-middle", "provider": "manual", "careers_url": "https://example.com/fictional-middle"},
    {"id": "healthy-second", "provider": "ashby", "board": "fictional-rotation"},
    {"id": "healthy-third", "provider": "lever", "board": "fictional-rotation"},
]


class RotationCLI(ScheduleCLI):
    def set_responses(self, *, changed: bool = False, extra: int = 0) -> None:
        if changed or extra:
            raise ValueError("Rotation fixture has fixed feeds")
        body = "Requirements\nExperience building Python services."
        responses = {
            GREENHOUSE: {"body": {"jobs": [{"id": 1000, "title": "Fictional Python Engineer Blocked",
                "location": {"name": "Fictional City"}, "content": "<h2>Requirements</h2><p>Python services.</p>"}],
                "meta": {"total": 1}}},
            ASHBY: {"body": {"apiVersion": "1", "jobs": [{"title": "Fictional Python Engineer Second", "isListed": True,
                "location": "Fictional City", "descriptionPlain": body,
                "jobUrl": "https://jobs.ashbyhq.com/fictional-rotation/fictional-second"}]}},
            LEVER: {"body": [{"id": "fictional-third", "text": "Fictional Python Engineer Third",
                "categories": {"location": "Fictional City"}, "descriptionPlain": body}]},
        }
        self.fixtures.write_text(json.dumps(responses), encoding="utf-8")


def check_source_rotation(command: list[str], workspace: Path, *, source_path: Path | None = None,
                          demo_output: Path | None = None, include_backup: bool = False) -> None:
    """One request and one preparation slot must reach each automatic source."""
    if sys.flags.optimize:
        raise RuntimeError("Rotation verification requires assertions")
    if shutil.which("pdflatex") is None:
        raise RuntimeError("Rotation gate needs pdflatex and the materials extra")
    repository = Path(__file__).resolve().parents[1]
    if demo_output is not None:
        demo_output = demo_output.resolve()
        if demo_output.exists() or demo_output.is_relative_to(repository):
            raise RuntimeError("Demo output must be a new external directory")
    cli = RotationCLI(command, workspace, source_path=source_path)
    workspace = cli.workspace
    print("Rotation gate: one request and one draft slot across three automatic boards and two manual gaps...", flush=True)
    source = workspace / "fictional-resume.txt"
    source.write_text(RESUME, encoding="utf-8")
    extracted = cli("profile", "extract", "--source-file", str(source))
    cli("profile", "init")
    imported = cli("profile", "onboard", "--extractor-version", "2", "--source-file", str(source),
        "--source-sha256", extracted["source_sha256"], "--select", "0,1,2,3,4,6,7",
        "--idempotency-key", "fictional-rotation-profile")
    for item in cli("profile", "review")["items"]:
        cli("profile", "decide", "--claim-id", item["claim"]["id"], "--review-token", item["review_token"],
            "--decision", "approve", "--actor-id", "synthetic-reviewer",
            "--idempotency-key", item["claim"]["id"], "--confirm")
    profile = cli("profile", "show")
    career = {claim["id"] for claim in profile["claims"] if claim["claim_type"] not in {"candidate_name", "contact_email"}}
    heading = next(claim["id"] for claim in profile["claims"] if "Example Robotics" in claim["canonical_text"])
    spec = {"schema_version": 2, "sources": SOURCES, "title_contains": ["Python Engineer"],
        "claim_ids": [identifier for identifier in imported["claim_ids"] if identifier in career],
        "max_jobs": 1, "max_requests": 1,
        "layout": {"schema_version": 1, "presentations": {heading: "heading"}}}
    search_id = cli("searches", "configure", "--spec-file", "-", "--idempotency-key", "rotation-search",
        input_data=json.dumps(spec))["search_id"]
    policy = {"schema_version": 1, "search_id": search_id, "timezone": "UTC", "local_time": "09:00",
        "start_date": "2026-09-20", "max_items": 1}
    schedule_id = cli("schedules", "configure", "--spec-file", "-", "--idempotency-key", "rotation-daily",
        input_data=json.dumps(policy))["schedule_id"]
    reports = []
    materials: set[str] = set()
    for index, (source_id, expected_url) in enumerate((
        ("blocked-first", GREENHOUSE), ("healthy-second", ASHBY), ("healthy-third", LEVER),
    ), 1):
        cli.set_clock(f"2026-09-{19 + index:02}T10:00:00+00:00")
        trace_start = len(cli.events())
        report = cli("schedules", "tick", "--schedule-id", schedule_id, expected=2)
        child = report["child"]
        assert report["executed"] and child["source_order"]["generation"] == index
        assert child["source_priority"][0] == source_id
        assert child["source_priority"][-2:] == ["manual-first", "manual-middle"]
        assert len(child["selection"]) == 1, "The scheduled priority source did not yield its one fictional candidate"
        assert child["selection"][0]["source_id"] == source_id
        assert child["requests_used"] == 1 and not child["coverage_complete"]
        assert child["counts"]["blocked"] == (1 if index == 1 else 0)
        assert child["counts"]["draft"] == (0 if index == 1 else 1)
        fetched = [event["url"] for event in cli.events()[trace_start:] if event["event"] == "fixture_get"]
        assert fetched == [expected_url]
        reports.append(child)
        materials.update(item["material_id"] for item in child["items"] if item["material_id"] is not None)
        requests, renders = cli.fetch_count(), cli.renders()
        repeated = cli("schedules", "tick", "--schedule-id", schedule_id)
        assert not repeated["executed"] and repeated["run_id"] == report["run_id"]
        assert repeated["child"]["source_order"] == child["source_order"]
        assert cli.fetch_count() == requests and cli.renders() == renders
        while repeated["notification"] is not None:
            notice = repeated["notification"]["id"]
            repeated = cli("schedules", "ack", "--schedule-id", schedule_id,
                "--notification-id", notice, "--idempotency-key", "ack-" + notice)
        if index == 2 and include_backup:
            print("Rotation gate: encrypted restore retains the next board's priority...", flush=True)
            archive, restored = workspace / "rotation-backup.gapply", workspace / "restored"
            phrase = "synthetic-rotation-only-passphrase\n"
            cli("backup", "--encrypt", str(archive), "--passphrase-stdin", input_data=phrase)
            args = ("restore", "--archive", str(archive), "--target-home", str(restored), "--passphrase-stdin")
            preview = cli(*args, input_data=phrase)
            cli(*args, "--archive-sha256", preview["archive_sha256"], "--confirm", input_data=phrase)
            cli.environment["GROUNDED_APPLY_HOME"] = str(restored)

    print("Rotation gate: a second unchanged cycle stays quiet after delivery acknowledgment...", flush=True)
    for index, source_id in enumerate(("blocked-first", "healthy-second", "healthy-third"), 4):
        cli.set_clock(f"2026-09-{19 + index:02}T10:00:00+00:00")
        requests, renders = cli.fetch_count(), cli.renders()
        report = cli("schedules", "tick", "--schedule-id", schedule_id, expected=2)
        assert report["executed"] and report["child"]["source_order"]["generation"] == index
        assert report["child"]["source_priority"][0] == source_id
        assert report["notification"] is None, "Budget rotation created an unchanged source-health notice"
        assert cli.fetch_count() == requests + 1 and cli.renders() == renders
        repeated = cli("schedules", "tick", "--schedule-id", schedule_id)
        assert not repeated["executed"] and repeated["notification"] is None
        assert cli.fetch_count() == requests + 1 and cli.renders() == renders
        reports.append(report["child"])

    assert len(materials) == cli.renders() == 2
    assert cli("profile", "show") == profile and cli("applications", "list")["applications"] == []
    database = Path(cli.environment["GROUNDED_APPLY_HOME"]) / "data" / "grounded_apply.db"
    before = database.read_bytes()
    for child in reports:
        reviewed = cli("searches", "show", "--run-id", child["run_id"])
        assert reviewed["source_order"] == child["source_order"] and reviewed["selection"] == child["selection"]
        assert not reviewed["application_ready"] and not reviewed["approvals_recorded"]
    cli("schedules", "show", "--schedule-id", schedule_id)
    assert database.read_bytes() == before and cli.fetch_count() == 6
    exports = workspace / "review-bundles"
    exports.mkdir(mode=0o700)
    for index, identifier in enumerate(sorted(materials)):
        material = cli("materials", "show", "--material-id", identifier)
        assert not material["ready"] and material["validation"]["unsupported_factual_units"] == 0
        destination = exports / f"fictional-rotation-draft-{index:02}"
        cli("materials", "export", "--material-id", identifier, "--output-dir", str(destination))
        assert (destination / "resume.pdf").read_bytes().startswith(b"%PDF-")
        assert "Contributed Rust parsing code; did not lead the project." in (destination / "resume.txt").read_text()
    if demo_output is not None:
        shutil.copytree(exports, demo_output)
    print("PASS — three days reach three boards under one-request/one-job limits; the next cycle stays quiet, with two real PDFs, stable replay and durable rotation.")
    print("Manual gaps and deferred sources remain explicit; rotation does not imply complete source coverage.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-backup", action="store_true")
    parser.add_argument("--demo-output", type=Path)
    args = parser.parse_args()
    if args.demo_output is not None and (not args.demo_output.is_absolute() or args.demo_output.exists()):
        parser.error("--demo-output must be an absolute new external directory")
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="gapply-synthetic-rotation-") as directory:
        check_source_rotation([sys.executable, "-m", "grounded_apply"], Path(directory),
            source_path=repository / "src", demo_output=args.demo_output, include_backup=args.with_backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
