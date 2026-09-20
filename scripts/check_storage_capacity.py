#!/usr/bin/env python3
"""Synthetic byte-capacity gate with real PDFs and encrypted CLI restoration.

Runs sequential child processes, never contacts employers, and creates only a
disposable external profile. Large fictional job texts exercise allocated bytes;
they do not establish a months-of-use or many-material-history performance claim.
Both optional extras and pdflatex are required. No production limits are patched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from scripts.check_schedule import ScheduleCLI
    from scripts.check_search import ASHBY, RESUME, SOURCES, fixture_responses
except ModuleNotFoundError:
    from check_schedule import ScheduleCLI
    from check_search import ASHBY, RESUME, SOURCES, fixture_responses


MIB = 1024 * 1024
EXPECTED_LIMIT = 256 * MIB
_SYNTHETIC_MARKER = "Grounded Apply synthetic storage-capacity acceptance fixture\n"
_RESOURCE_HOOK = r'''
import atexit
import resource
import sys
import time

_capacity_started = time.monotonic()
def _capacity_resources():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux and other supported POSIX runners report KiB.
    _trace("capacity_resources", operation=os.environ["GAPPLY_CAPACITY_OPERATION"],
           elapsed_seconds=round(time.monotonic() - _capacity_started, 6),
           peak_rss_bytes=int(peak if sys.platform == "darwin" else peak * 1024))
atexit.register(_capacity_resources)
'''


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class CapacityCLI(ScheduleCLI):
    """Existing CLI checks plus one resource observation per isolated process."""

    def __init__(self, python: str, workspace: Path, *, source_path: Path | None) -> None:
        super().__init__([python, "-m", "grounded_apply"], workspace, source_path=source_path)
        self.deadline = time.monotonic() + 900
        hook = self.hooks / "sitecustomize.py"
        hook.write_text(hook.read_text(encoding="utf-8") + _RESOURCE_HOOK, encoding="utf-8")
        (self.workspace / "synthetic-capacity-marker.txt").write_text(_SYNTHETIC_MARKER, encoding="utf-8")
        self.environment["GAPPLY_CAPACITY_OPERATION"] = "setup"
        self.measurements: list[dict[str, Any]] = []

    def measured(self, operation: str, *args: str, input_data: str | None = None,
                 expected: int = 0) -> dict[str, Any]:
        previous = len(self.events())
        self.environment["GAPPLY_CAPACITY_OPERATION"] = operation
        started = time.monotonic()
        result = self(*args, input_data=input_data, expected=expected)
        self.record_resources(operation, previous, time.monotonic() - started)
        return result

    def record_resources(self, operation: str, previous: int, elapsed: float) -> None:
        events = [event for event in self.events()[previous:]
                  if event["event"] == "capacity_resources"]
        assert len(events) == 1 and events[0]["operation"] == operation
        resource = {key: value for key, value in events[0].items() if key != "event"}
        resource["wall_seconds"] = round(elapsed, 6)
        assert resource["peak_rss_bytes"] > 0
        self.measurements.append(resource)

    def fill(self, target_bytes: int) -> dict[str, Any]:
        operation = f"fill_{target_bytes // MIB}_mib"
        self.environment["GAPPLY_CAPACITY_OPERATION"] = operation
        previous = len(self.events())
        started = time.monotonic()
        process = subprocess.run(
            [self.command[0], str(Path(__file__).resolve()), "--worker-fill",
             str(self.workspace), str(target_bytes)],
            cwd=self.workspace, env=self.environment, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=min(300, self.deadline - started),
        )
        if process.returncode or process.stderr:
            raise RuntimeError(f"Synthetic capacity filler failed: {process.stdout}{process.stderr}")
        self.record_resources(operation, previous, time.monotonic() - started)
        assert any(event["event"] == "hook_loaded" for event in self.events()[previous:])
        result = json.loads(process.stdout)
        assert target_bytes - 64 * 1024 <= result["database_bytes"] < target_bytes + 64 * 1024
        assert result["snapshot_limit_bytes"] == EXPECTED_LIMIT
        assert result["schema_version"] == 7
        return result


def _fill_worker(workspace: Path, target_bytes: int) -> None:
    """Use validated capture services; never insert rows or pad SQLite directly."""
    from grounded_apply.config import require_initialized_profile_storage, resolve_runtime_paths
    from grounded_apply.repositories import SQLiteRepository
    from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES
    from grounded_apply.services.jobs import JobService

    root = Path(__file__).resolve().parents[1]
    workspace = workspace.resolve()
    paths = resolve_runtime_paths()
    assert not workspace.is_relative_to(root)
    assert paths.portable_root == workspace / "runtime"
    assert (workspace / "synthetic-capacity-marker.txt").read_text(encoding="utf-8") == _SYNTHETIC_MARKER
    assert 16 * MIB < target_bytes <= MAX_SNAPSHOT_BYTES - MIB
    assert MAX_SNAPSHOT_BYTES == EXPECTED_LIMIT
    require_initialized_profile_storage(paths)
    inserted: list[str] = []
    with SQLiteRepository(paths.database, existing_only=True).initialize() as repository:
        service = JobService(repository)
        # These are deliberately large public-job inputs, not candidate claims.
        # Unique headers and audited workflows preserve normal source custody.
        while repository.database_size_bytes() < target_bytes - 64 * 1024:
            allocated = repository.database_size_bytes()
            source_size = min(960 * 1024, max(16 * 1024, target_bytes - allocated - 64 * 1024))
            index = f"{target_bytes}-{len(inserted)}"
            header = f"Fictional capacity posting {index}. Synthetic employer only.\n"
            line = "fictional public posting context for a storage acceptance fixture "
            source = header + (line * ((source_size - len(header)) // len(line) + 1))
            source = source[:source_size]
            with repository.transaction():
                result = service.add(f"https://example.com/capacity/{index}", source,
                    idempotency_key=f"synthetic-capacity-{index}")
                assert repository.database_size_bytes() < MAX_SNAPSHOT_BYTES
            inserted.append(str(result["job_id"]))
            assert len(inserted) <= 300
        # Revalidate every inserted source and immutable workflow after capture.
        for identifier in inserted:
            job = service.get(identifier)
            assert job.source_url.startswith("https://example.com/capacity/")
            assert job.live_page_verified is False and not job.requirements
        allocated = repository.database_size_bytes()
    header = paths.database.open("rb")
    with header:
        schema_version = int.from_bytes(header.read(64)[60:64], "big")
    print(json.dumps({"inserted_jobs": len(inserted), "database_bytes": allocated,
        "snapshot_limit_bytes": MAX_SNAPSHOT_BYTES, "schema_version": schema_version}))


def _set_posting(cli: CapacityCLI, revision: int) -> None:
    responses = fixture_responses()
    job = responses[ASHBY]["body"]["jobs"][0]
    job["descriptionPlain"] += f"\nFictional posting revision {revision}."
    responses[ASHBY]["body"]["jobs"] = [job]
    cli.fixtures.write_text(json.dumps(responses), encoding="utf-8")


def _round_trip(cli: CapacityCLI, name: str, profile: dict[str, Any],
                schedule_id: str, expected_materials: set[str]) -> dict[str, Any]:
    """Measure actual encrypted commands; prove exact snapshot and quiet custody."""
    source_home = cli.environment["GROUNDED_APPLY_HOME"]
    database = Path(source_home) / "data" / "grounded_apply.db"
    source_hash = _file_digest(database)
    archive, restored = cli.workspace / f"{name}.gapply", cli.workspace / f"{name}-restored"
    passphrase = "synthetic-capacity-only-passphrase\n"
    requests, renders = cli.fetch_count(), cli.renders()
    backup = cli.measured(f"{name}_backup", "backup", "--encrypt", str(archive),
        "--passphrase-stdin", input_data=passphrase)
    assert backup["snapshot_bytes"] == database.stat().st_size
    assert 16 * MIB < backup["snapshot_bytes"] <= EXPECTED_LIMIT
    restore_args = ("restore", "--archive", str(archive), "--target-home", str(restored),
        "--passphrase-stdin")
    preview = cli.measured(f"{name}_restore_preview", *restore_args, input_data=passphrase)
    assert preview["dry_run"] is True and not restored.exists()
    restored_result = cli.measured(f"{name}_restore", *restore_args,
        "--archive-sha256", preview["archive_sha256"], "--confirm", input_data=passphrase)
    assert restored_result["snapshot_sha256"] == backup["snapshot_sha256"]
    assert restored_result["archive_sha256"] == backup["archive_sha256"]
    assert _file_digest(restored / "data" / "grounded_apply.db") == backup["snapshot_sha256"]
    assert _file_digest(database) == source_hash
    # Exact confirmed replay exercises target receipt and identity validation too.
    replay = cli.measured(f"{name}_restore_replay", *restore_args,
        "--archive-sha256", preview["archive_sha256"], "--confirm", input_data=passphrase)
    assert replay["replayed"] is True
    cli.environment["GROUNDED_APPLY_HOME"] = str(restored)
    try:
        assert cli("profile", "show") == profile
        materials = cli("materials", "list")["materials"]
        assert {item["material_id"] for item in materials} == expected_materials
        for identifier in sorted(expected_materials):
            item = cli("materials", "show", "--material-id", identifier)
            assert item["validation"]["valid"] and item["ready"] is False
            assert item["validation"]["unsupported_factual_units"] == 0
        before = _file_digest(restored / "data" / "grounded_apply.db")
        same_day = cli("schedules", "tick", "--schedule-id", schedule_id)
        assert same_day["executed"] is False and same_day["notify"] is False
        assert _file_digest(restored / "data" / "grounded_apply.db") == before
        assert cli("applications", "list")["applications"] == []
    finally:
        cli.environment["GROUNDED_APPLY_HOME"] = source_home
    assert cli.fetch_count() == requests and cli.renders() == renders
    assert _file_digest(database) == source_hash
    return {"database_bytes": backup["snapshot_bytes"], "archive_bytes": archive.stat().st_size,
        "snapshot_sha256": backup["snapshot_sha256"], "archive_sha256": backup["archive_sha256"],
        "material_count": len(expected_materials), "exact_snapshot_restore": True,
        "unchanged_source": True, "quiet_restored_replay": True}


def check_storage_capacity(python: str, workspace: Path, *, source_path: Path | None,
                           maximum_probe: bool = True) -> dict[str, Any]:
    """Require >16 MiB daily preparation and optionally near-256 MiB round trip."""
    if sys.flags.optimize:
        raise RuntimeError("Storage capacity verification requires Python assertions")
    if shutil.which("pdflatex") is None:
        raise RuntimeError("Storage capacity gate requires pdflatex and both optional extras")
    cli = CapacityCLI(python, workspace, source_path=source_path)
    started = time.monotonic()
    source = cli.workspace / "fictional-resume.txt"
    source.write_text(RESUME, encoding="utf-8")
    extracted = cli("profile", "extract", "--source-file", str(source))
    cli("profile", "init")
    imported = cli("profile", "onboard", "--source-file", str(source),
        "--source-sha256", extracted["source_sha256"], "--select", "0,1,2,3,4,6,7",
        "--idempotency-key", "synthetic-capacity-profile")
    for item in cli("profile", "review")["items"]:
        cli("profile", "decide", "--claim-id", item["claim"]["id"],
            "--review-token", item["review_token"], "--decision", "approve",
            "--actor-id", "synthetic-reviewer", "--idempotency-key", item["claim"]["id"], "--confirm")
    profile = cli("profile", "show")
    career_set = {item["id"] for item in profile["claims"]
                  if item["claim_type"] not in {"candidate_name", "contact_email"}}
    heading = next(item["id"] for item in profile["claims"] if "Example Robotics" in item["canonical_text"])
    spec = {"schema_version": 1, "sources": [SOURCES[1]],
        "claim_ids": [identifier for identifier in imported["claim_ids"] if identifier in career_set],
        "title_contains": ["Python Engineer"], "max_jobs": 1,
        "layout": {"schema_version": 1, "presentations": {heading: "heading"}}}
    search = cli("searches", "configure", "--spec-file", "-", "--idempotency-key", "capacity-search",
        input_data=json.dumps(spec))
    schedule = cli("schedules", "configure", "--spec-file", "-", "--idempotency-key", "capacity-daily",
        input_data=json.dumps({"schema_version": 1, "search_id": search["search_id"],
            "timezone": "UTC", "local_time": "09:00", "start_date": "2026-09-20"}))
    schedule_id = schedule["schedule_id"]

    def tick(day: int, *, new_material: bool) -> dict[str, Any]:
        cli.set_clock(f"2026-09-{day:02}T10:00:00+00:00")
        report = cli.measured(f"daily_{day}", "schedules", "tick", "--schedule-id", schedule_id)
        assert report["executed"] is True and report["external_action_taken"] is False
        assert report["application_ready"] is False
        assert report["child"]["counts"]["draft"] == int(new_material)
        assert report["child"]["counts"]["blocked"] == 0
        assert report["child"]["approvals_recorded"] is False
        assert report["notify"] is new_material
        if new_material:
            notification_id = report["notification"]["id"]
            acknowledged = cli("schedules", "ack", "--schedule-id", schedule_id,
                "--notification-id", notification_id, "--idempotency-key", f"ack-{notification_id}")
            assert acknowledged["notifications_pending"] == 0
        return report

    print("Storage gate: approved fictional profile, then service-captured jobs beyond 16 MiB...", flush=True)
    fills = [cli.fill(18 * MIB)]
    _set_posting(cli, 1)
    tick(20, new_material=True)
    tick(21, new_material=False)
    _set_posting(cli, 2)
    tick(22, new_material=True)
    assert cli.renders() == 2
    materials = {item["material_id"] for item in cli("materials", "list")["materials"]}
    assert len(materials) == 2
    rounds = {"beyond_old_cap": _round_trip(cli, "beyond-old-cap", profile, schedule_id, materials)}
    if maximum_probe:
        print("Storage gate: sequential near-256 MiB daily/backup/restore resource probe...", flush=True)
        fills.append(cli.fill(255 * MIB))
        _set_posting(cli, 3)
        tick(23, new_material=True)
        assert cli.renders() == 3
        materials = {item["material_id"] for item in cli("materials", "list")["materials"]}
        assert len(materials) == 3
        rounds["near_capacity"] = _round_trip(cli, "near-capacity", profile, schedule_id, materials)
        assert rounds["near_capacity"]["database_bytes"] >= 255 * MIB - 64 * 1024
    assert all(event["event"] not in {"unexpected_url", "unexpected_network"} for event in cli.events())
    summary = {"schema_version": 1, "passed": True, "database_schema_version": 7,
        "snapshot_limit_bytes": EXPECTED_LIMIT, "maximum_probe": maximum_probe,
        "elapsed_seconds": round(time.monotonic() - started, 6), "fills": fills,
        "round_trips": rounds, "measurements": cli.measurements,
        "real_pdf_count": cli.renders(), "fixture_get_count": cli.fetch_count(),
        "real_network_requests": 0,
        "limitations": ["Byte-capacity fixture uses large synthetic job text.",
            "Does not measure thousands of material records or promise a retention duration.",
            "Peak RSS is each child process maximum; not a machine-wide memory measurement."]}
    (cli.workspace / "capacity-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    print("PASS — larger-capacity preparation and exact encrypted restoration; no personal data or network.", flush=True)
    return summary


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "--worker-fill":
        _fill_worker(Path(sys.argv[2]), int(sys.argv[3]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python with both optional extras installed.")
    parser.add_argument("--installed", action="store_true", help="Exercise that interpreter's installed package.")
    parser.add_argument("--workspace", type=Path, help="New absolute external directory to retain synthetic evidence.")
    parser.add_argument("--skip-maximum-probe", action="store_true", help="Only >16 MiB smoke gate; not full capacity validation.")
    arguments = parser.parse_args()
    source_path = None if arguments.installed else Path(__file__).resolve().parents[1] / "src"
    if arguments.workspace is not None:
        workspace = arguments.workspace
        root = Path(__file__).resolve().parents[1]
        if not workspace.is_absolute() or workspace.exists() or workspace.resolve().is_relative_to(root):
            parser.error("--workspace must be a new absolute external directory")
        workspace.mkdir(mode=0o700)
        check_storage_capacity(arguments.python, workspace, source_path=source_path,
            maximum_probe=not arguments.skip_maximum_probe)
    else:
        with tempfile.TemporaryDirectory(prefix="grounded-apply-storage-capacity-") as directory:
            check_storage_capacity(arguments.python, Path(directory), source_path=source_path,
                maximum_probe=not arguments.skip_maximum_probe)


if __name__ == "__main__":
    main()
