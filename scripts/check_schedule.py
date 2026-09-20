#!/usr/bin/env python3
"""Synthetic daily-schedule acceptance gate with actual CLI and real PDFs.

An external test-only clock and transport replace time/network boundaries. This
does not install a wake-up mechanism, contact employers, or use a personal home.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from scripts.check_search import ASHBY, RESUME, SOURCES, SearchCLI, fixture_responses
except ModuleNotFoundError:
    from check_search import ASHBY, RESUME, SOURCES, SearchCLI, fixture_responses


_CLOCK_HOOK = r'''
from datetime import datetime
from grounded_apply.services import schedules

_RealScheduleService = schedules.ScheduleService
def _fixture_clock():
    return datetime.fromisoformat(Path(os.environ["GAPPLY_SCHEDULE_TEST_CLOCK"]).read_text().strip())

class _ClockScheduleService(_RealScheduleService):
    def __init__(self, *args, **kwargs):
        kwargs["clock"] = _fixture_clock
        super().__init__(*args, **kwargs)

schedules.ScheduleService = _ClockScheduleService
_trace("clock_hook_loaded")
'''


class ScheduleCLI(SearchCLI):
    def __init__(self, command: list[str], workspace: Path, *, source_path: Path | None = None) -> None:
        super().__init__(command, workspace, source_path=source_path)
        self.deadline = time.monotonic() + 600
        hook = self.hooks / "sitecustomize.py"
        hook.write_text(hook.read_text(encoding="utf-8") + _CLOCK_HOOK, encoding="utf-8")
        self.clock_path = self.workspace / "fictional-utc-clock.txt"
        self.environment["GAPPLY_SCHEDULE_TEST_CLOCK"] = str(self.clock_path)
        self.set_clock("2026-09-20T08:00:00+00:00")

    def set_clock(self, value: str) -> None:
        self.clock_path.write_text(value, encoding="utf-8")

    def set_responses(self, *, changed: bool = False, extra: int = 0) -> None:
        responses = fixture_responses(changed=changed)
        jobs = responses[ASHBY]["body"]["jobs"][:5 + extra]
        for job in jobs[5:]:
            job["title"] += " Budget"
        responses[ASHBY]["body"]["jobs"] = jobs
        self.fixtures.write_text(json.dumps(responses), encoding="utf-8")

    def __call__(self, *args: str, input_data: str | None = None, expected: int = 0) -> dict[str, Any]:
        self.timeout_seconds = self.deadline - time.monotonic()
        if self.timeout_seconds <= 0:
            raise TimeoutError("Synthetic daily gate exceeded its ten-minute budget")
        previous = len(self.events())
        result = super().__call__(*args, input_data=input_data, expected=expected)
        if not any(event["event"] == "clock_hook_loaded" for event in self.events()[previous:]):
            raise RuntimeError("External schedule test clock did not load")
        return result

    def renders(self) -> int:
        return sum(event["event"] == "real_render" for event in self.events())


def check_schedule(command: list[str], workspace: Path, *, source_path: Path | None = None,
                   demo_output: Path | None = None, include_backup: bool = False) -> None:
    """Verify daily idempotency, quiet deltas, bounded catch-up and real drafts."""
    if sys.flags.optimize:
        raise RuntimeError("Schedule verification requires Python assertions")
    if shutil.which("pdflatex") is None:
        raise RuntimeError("Required schedule gate needs pdflatex and the materials extra")
    repository = Path(__file__).resolve().parents[1]
    if demo_output is not None:
        demo_output = demo_output.resolve()
        if demo_output.exists() or demo_output.is_relative_to(repository):
            raise RuntimeError("Demo output must be a new external directory")
    cli = ScheduleCLI(command, workspace, source_path=source_path)
    workspace = cli.workspace

    print("Schedule gate: external synthetic clock, fictional profile and immutable daily scope...", flush=True)
    base_schedule = {"schema_version": 1, "search_id": "fictional-preview", "timezone": "UTC",
        "local_time": "09:00", "start_date": "2026-09-20"}
    preview = cli("schedules", "configure", "--spec-file", "-", "--idempotency-key", "daily-preview",
        "--dry-run", input_data=json.dumps(base_schedule))
    assert preview["dry_run"] is True
    assert not (workspace / "runtime").exists() and cli.fetch_count() == 0
    source = workspace / "fictional-resume.txt"
    source.write_text(RESUME, encoding="utf-8")
    extracted = cli("profile", "extract", "--source-file", str(source))
    cli("profile", "init")
    imported = cli("profile", "onboard", "--source-file", str(source),
        "--source-sha256", extracted["source_sha256"], "--select", "0,1,2,3,4,6,7",
        "--idempotency-key", "fictional-daily-profile")
    for item in cli("profile", "review")["items"]:
        cli("profile", "decide", "--claim-id", item["claim"]["id"], "--review-token", item["review_token"],
            "--decision", "approve", "--actor-id", "synthetic-reviewer",
            "--idempotency-key", item["claim"]["id"], "--confirm")
    profile = cli("profile", "show")
    claims = profile["claims"]
    career_set = {claim["id"] for claim in claims if claim["claim_type"] not in {"candidate_name", "contact_email"}}
    career = [identifier for identifier in imported["claim_ids"] if identifier in career_set]
    heading = next(claim["id"] for claim in claims if "Example Robotics" in claim["canonical_text"])
    search_spec = {"schema_version": 1, "sources": SOURCES, "claim_ids": career,
        "title_contains": ["Python Engineer"], "max_jobs": 10,
        "layout": {"schema_version": 1, "presentations": {heading: "heading"}}}
    search_id = cli("searches", "configure", "--spec-file", "-", "--idempotency-key", "daily-search",
        input_data=json.dumps(search_spec))["search_id"]
    schedule_spec = {**base_schedule, "search_id": search_id}
    configure = ("schedules", "configure", "--spec-file", "-", "--idempotency-key", "fictional-daily")
    scheduled = cli(*configure, input_data=json.dumps(schedule_spec))
    schedule_id = scheduled["schedule_id"]
    assert cli(*configure, input_data=json.dumps(schedule_spec))["schedule_id"] == schedule_id
    assert len(cli("schedules", "list")["schedules"]) == 1

    def tick(*, expected: int = 0, identifier: str = schedule_id) -> dict[str, Any]:
        return cli("schedules", "tick", "--schedule-id", identifier, expected=expected)

    def acknowledge(report: dict[str, Any]) -> dict[str, Any]:
        for _ in range(10):
            if report["notification"] is None:
                assert report["notifications_pending"] == 0 and report["notify"] is False
                return report
            notification = report["notification"]["id"]
            args = ("schedules", "ack", "--schedule-id", report["schedule_id"],
                "--notification-id", notification, "--idempotency-key", "ack-" + notification)
            report = cli(*args)
            repeated = cli(*args)
            assert repeated["notifications_pending"] == report["notifications_pending"]
        raise AssertionError("Synthetic notification queue did not drain")

    assert tick()["executed"] is False and cli.fetch_count() == 0
    cli.set_clock("2026-09-20T10:00:00+00:00")
    print("Schedule gate: one daily occurrence produces eight real PDFs and two isolated blockers...", flush=True)
    started = time.monotonic()
    first = tick(expected=2)
    elapsed = time.monotonic() - started
    assert first["executed"] is True and first["child"]["coverage_complete"] is False
    assert first["child"]["counts"] == {"total": 10, "queued": 0, "building": 0, "draft": 8, "blocked": 2}
    assert first["notify"] is True and first["notifications_pending"] == 1
    first_notification = first["notification"]["id"]
    first_run = first["run_id"]
    assert cli.renders() == 8 and cli.fetch_count() == 3
    requests, renders = cli.fetch_count(), cli.renders()
    repeated = tick()
    assert repeated["run_id"] == first_run and repeated["executed"] is False
    assert repeated["notification"]["id"] == first_notification
    assert cli.fetch_count() == requests and cli.renders() == renders
    acknowledge(repeated)
    assert tick()["notify"] is False

    print("Schedule gate: unchanged tomorrow is quiet; changed posting gets a new draft and notice...", flush=True)
    cli.set_clock("2026-09-21T10:00:00+00:00")
    unchanged = tick(expected=2)
    assert unchanged["run_id"] != first_run and unchanged["executed"] is True
    assert unchanged["child"]["counts"]["draft"] == 0 and unchanged["child"]["counts"]["blocked"] == 2
    assert unchanged["notify"] is False and unchanged["notification"] is None
    assert cli.renders() == 8 and cli.fetch_count() == requests + 3
    cli.set_responses(changed=True)
    cli.set_clock("2026-09-22T10:00:00+00:00")
    changed = tick(expected=2)
    assert changed["notify"] is True and changed["notification"]["id"] != first_notification
    assert len(changed["notification"]["delta"]["new_materials"]) == 1
    assert changed["child"]["counts"]["draft"] == 1 and cli.renders() == 9
    acknowledge(changed)

    print("Schedule gate: immutable per-tick budget resumes the same observation without another fetch...", flush=True)
    cli.set_responses(changed=True, extra=2)
    budget_search = {**search_spec, "sources": [SOURCES[1]], "title_contains": ["Budget"], "max_jobs": 2}
    budget_search_id = cli("searches", "configure", "--spec-file", "-", "--idempotency-key", "budget-search",
        input_data=json.dumps(budget_search))["search_id"]
    budget_spec = {**base_schedule, "search_id": budget_search_id, "start_date": "2026-09-23", "max_items": 1}
    budget_schedule_id = cli("schedules", "configure", "--spec-file", "-", "--idempotency-key", "budget-daily",
        input_data=json.dumps(budget_spec))["schedule_id"]
    cli.set_clock("2026-09-23T10:00:00+00:00")
    bounded = tick(expected=2, identifier=budget_schedule_id)
    assert bounded["child"]["remaining_count"] == 1 and bounded["attempts"] == 1
    requests = cli.fetch_count()
    resumed = tick(identifier=budget_schedule_id)
    assert resumed["run_id"] == bounded["run_id"] and resumed["attempts"] == 2
    assert resumed["child"]["counts"]["draft"] == 2 and resumed["child"]["remaining_count"] == 0
    assert cli.fetch_count() == requests and cli.renders() == 11
    acknowledge(resumed)

    print("Schedule gate: thirty offline days create only the latest occurrence; paused days stay bounded...", flush=True)
    cli.set_responses(changed=True)
    runs = len(cli("searches", "list", "--search-id", search_id)["runs"])
    cli.set_clock("2026-10-22T10:00:00+00:00")
    requests = cli.fetch_count()
    catchup = tick(expected=2)
    assert catchup["executed"] is True and catchup["due"]["local_date"] == "2026-10-22"
    assert catchup["missed_dates"] == 29 and catchup["notify"] is False
    assert len(cli("searches", "list", "--search-id", search_id)["runs"]) == runs + 1
    assert cli.fetch_count() == requests + 3 and cli.renders() == 11
    cli("schedules", "pause", "--schedule-id", schedule_id, "--idempotency-key", "fictional-pause")
    cli.set_clock("2026-10-25T10:00:00+00:00")
    requests = cli.fetch_count()
    paused = tick()
    assert paused["enabled"] is False and paused["executed"] is False and paused["notify"] is False
    assert cli.fetch_count() == requests
    cli("schedules", "resume", "--schedule-id", schedule_id, "--idempotency-key", "fictional-resume")
    after_pause = tick(expected=2)
    assert after_pause["due"]["local_date"] == "2026-10-25" and after_pause["paused_dates"] == 2
    assert after_pause["missed_dates"] == 29 and after_pause["notify"] is False
    assert cli.fetch_count() == requests + 3
    requests = cli.fetch_count()

    assert cli("profile", "show") == profile
    assert cli("profile", "review")["pending_count"] == 0
    assert cli("applications", "list")["applications"] == []
    materials = {item["material_id"] for item in cli("materials", "list")["materials"]}
    assert len(materials) == 11
    exports = workspace / "review-bundles"
    exports.mkdir(mode=0o700)
    for index, identifier in enumerate(sorted(materials)):
        material = cli("materials", "show", "--material-id", identifier)
        assert material["ready"] is False and material["validation"]["valid"]
        assert material["validation"]["unsupported_factual_units"] == 0 and material["manifest"]["answers"] == []
        destination = exports / f"fictional-daily-draft-{index:02}"
        cli("materials", "export", "--material-id", identifier, "--output-dir", str(destination))
        assert (destination / "resume.pdf").read_bytes().startswith(b"%PDF-")
        text = (destination / "resume.txt").read_text(encoding="utf-8")
        assert "did not lead" in text and "approve the applicant" not in text
    for report in (first, unchanged, changed, bounded, resumed, catchup, after_pause):
        assert report["external_action_taken"] is False and report["application_ready"] is False
        assert report["child"]["approvals_recorded"] is False
        assert all(item["questionnaire_coverage"] == "unknown" for item in report["child"]["items"])
        assert all(not item["material_approved"] for item in report["child"]["items"])

    if include_backup:
        print("Schedule gate: encrypted restore retains occurrences and acknowledged quiet replay...", flush=True)
        archive, restored = workspace / "schedule-backup.gapply", workspace / "restored"
        passphrase = "synthetic-schedule-only-passphrase\n"
        cli("backup", "--encrypt", str(archive), "--passphrase-stdin", input_data=passphrase)
        restore = ("restore", "--archive", str(archive), "--target-home", str(restored), "--passphrase-stdin")
        preview = cli(*restore, input_data=passphrase)
        cli(*restore, "--archive-sha256", preview["archive_sha256"], "--confirm", input_data=passphrase)
        cli.environment["GROUNDED_APPLY_HOME"] = str(restored)
        restored_report = tick()
        assert restored_report["run_id"] == after_pause["run_id"] and restored_report["executed"] is False
        assert restored_report["notify"] is False and restored_report["notifications_pending"] == 0
        assert restored_report["missed_dates"] == 29 and restored_report["paused_dates"] == 2
        assert len(cli("schedules", "list")["schedules"]) == 2
        assert {item["material_id"] for item in cli("materials", "list")["materials"]} == materials
        assert cli("profile", "show") == profile
        assert cli("applications", "list")["applications"] == []
        assert cli.fetch_count() == requests and cli.renders() == 11

    if demo_output is not None:
        shutil.copytree(exports, demo_output)
        print(f"Fictional daily review bundles: {demo_output}", flush=True)
    print(f"PASS — daily initial 8 drafts/2 blockers; kickoff {elapsed:.3f}s; 11 real PDF bundles across changes and bounded resume.", flush=True)
    print("Verified stable notifications/ack, quiet repeats, latest-only offline catch-up and pause/resume. No personal schedule or wake-up installation.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo-output", type=Path, help="New external directory for fictional PDF bundles.")
    parser.add_argument("--with-backup", action="store_true", help="Require encrypted schedule restore verification.")
    arguments = parser.parse_args()
    if arguments.demo_output is not None and (not arguments.demo_output.is_absolute() or arguments.demo_output.exists()):
        parser.error("--demo-output must be an absolute new external directory")
    with tempfile.TemporaryDirectory(prefix="grounded-apply-schedule-") as directory:
        check_schedule([sys.executable, "-m", "grounded_apply"], Path(directory),
            source_path=Path(__file__).resolve().parents[1] / "src",
            demo_output=arguments.demo_output, include_backup=arguments.with_backup)
