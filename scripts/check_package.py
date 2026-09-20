#!/usr/bin/env python3
"""Build and exercise a wheel outside the checkout with synthetic data only.

Run using a Python environment containing build and Hatchling. The build uses
those installed tools without downloading dependencies. The test installation
is a fresh virtual environment; source PYTHONPATH and user site imports are
disabled. All generated files are disposable and outside the checkout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[1]


def run(
    command: list[str], *, cwd: Path, environ: dict[str, str], input_data: str | None = None,
) -> str:
    result = subprocess.run(
        command, cwd=cwd, env=environ, text=True, capture_output=True,
        timeout=120, check=False, input=input_data,
    )
    if result.returncode:
        # Every payload in this script is synthetic; never run this on a user's
        # runtime database or reuse this helper for private support artifacts.
        raise RuntimeError(
            f"Package check command failed ({result.returncode}):\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout


def check(*, backup_wheelhouse: Path | None = None, pilot_wheelhouse: Path | None = None) -> None:
    metadata = tomllib.loads((REPOSITORY / "pyproject.toml").read_text())
    version = metadata["project"]["version"]
    with tempfile.TemporaryDirectory(prefix="grounded-apply-package-") as directory:
        workspace = Path(directory).resolve()
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        environment.update({
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "GROUNDED_APPLY_HOME": str(workspace / "profile"),
        })
        print("Building source archive and wheel from source archive...", flush=True)
        run(
            [sys.executable, "-m", "build", "--no-isolation", "--outdir",
             str(workspace / "dist"), str(REPOSITORY)],
            cwd=workspace, environ=environment,
        )
        wheels = list((workspace / "dist").glob("*.whl"))
        if len(wheels) != 1 or len(list((workspace / "dist").glob("*.tar.gz"))) != 1:
            raise RuntimeError("Expected exactly one wheel and source distribution")
        with zipfile.ZipFile(wheels[0]) as wheel:
            names = set(wheel.namelist())
            required = {
                "grounded_apply/py.typed",
                "grounded_apply/migrations/001_initial.sql",
                "grounded_apply/migrations/002_profile_import_review_items.sql",
                "grounded_apply/migrations/003_claim_retirements.sql",
                "grounded_apply/migrations/004_application_pilot.sql",
                "grounded_apply/migrations/005_preparation_batches.sql",
                "grounded_apply/migrations/006_saved_searches.sql",
                "grounded_apply/migrations/007_daily_schedules.sql",
                "grounded_apply/diagnostics.py",
                "grounded_apply/services/backup.py",
                "grounded_apply/services/discovery.py",
                "grounded_apply/services/batches.py",
                "grounded_apply/services/searches.py",
                "grounded_apply/services/review_exports.py",
                "grounded_apply/repositories/review_files.py",
                "grounded_apply/services/search_policy.py",
                "grounded_apply/services/schedules.py",
                "grounded_apply/services/schedule_policy.py",
                "grounded_apply/services/schedule_notifications.py",
                "grounded_apply/services/source_windows.py",
                "grounded_apply/services/search_filters.py",
                "grounded_apply/services/source_rotation.py",
                "grounded_apply/services/storage_capacity.py",
                "grounded_apply/services/storage_limits.py",
                "grounded_apply/repositories/netflix_source.py",
                "grounded_apply/repositories/discovery_http.py",
                "grounded_apply/repositories/job_sources.py",
                "grounded_apply/repositories/backup_crypto.py",
                "grounded_apply/repositories/backup_files.py",
                "grounded_apply/repositories/snapshots.py",
            }
            if not required <= names:
                raise RuntimeError("Wheel is missing required package data")
            for name in names:
                parts = Path(name).parts
                if (name.endswith((".db", ".pyc", ".log"))
                    or any(part in {"tests", ".git", "__pycache__"} for part in parts)):
                    raise RuntimeError("Wheel contains development or runtime artifacts")

        installation = workspace / "installation"
        venv.EnvBuilder(with_pip=True).create(installation)
        executable = installation / "bin" / "python"
        command = installation / "bin" / "gapply"
        run(
            [str(executable), "-m", "pip", "--isolated", "install", "--no-index",
             "--no-deps", "--no-cache-dir", str(wheels[0])],
            cwd=workspace, environ=environment,
        )
        dependencies = pilot_wheelhouse or backup_wheelhouse
        if dependencies is not None:
            run(
                [str(executable), "-m", "pip", "--isolated", "install", "--no-index",
                 "--no-cache-dir", "--find-links", str(dependencies),
                 "grounded-apply[backup,materials]" if pilot_wheelhouse else "grounded-apply[backup]"],
                cwd=workspace, environ=environment,
            )
        location = json.loads(run(
            [str(executable), "-I", "-c",
             "import json,grounded_apply; from grounded_apply.repositories._schema "
             "import default_migrations_directory; print(json.dumps([grounded_apply.__file__, "
             "str(default_migrations_directory())]))"],
            cwd=workspace, environ=environment,
        ))
        if any(not Path(path).is_relative_to(installation) for path in location):
            raise RuntimeError("Installed check imported source checkout content")
        if run([str(command), "--version"], cwd=workspace, environ=environment).strip() != f"gapply {version}":
            raise RuntimeError("Installed CLI version disagrees with package metadata")
        for args in (
            [], ["profile", "import"], ["profile", "review"], ["profile", "decide"],
            ["backup"], ["restore"], ["brief"], ["jobs", "discover"], ["batches", "prepare"],
            ["searches", "configure"], ["searches", "run"], ["searches", "export"],
            ["schedules", "configure"], ["schedules", "tick"],
        ):
            run([str(command), *args, "--help"], cwd=workspace, environ=environment)

        def cli(*args: str, input_data: str | None = None) -> dict[str, Any]:
            result = json.loads(run(
                [str(command), *args, "--json"], cwd=workspace, environ=environment,
                input_data=input_data,
            ))
            if result["ok"] is not True:
                raise RuntimeError("Installed CLI returned a failed envelope")
            return result["data"]

        print("Checking isolated installed CLI, migrations, decisions, and replay...", flush=True)
        initial = cli("doctor")
        if initial["checks"]["database"]["initialized"] is not False:
            raise RuntimeError("Fresh package runtime was unexpectedly initialized")
        if (workspace / "profile").exists():
            raise RuntimeError("Doctor created runtime files")
        first_init = cli("profile", "init")
        if cli("profile", "init")["schema_version"] != first_init["schema_version"]:
            raise RuntimeError("Initialization was not idempotent")
        discovery_smoke = r'''
import io, json
from contextlib import redirect_stdout
from unittest.mock import patch
from grounded_apply.cli import main
manifest = {"schema_version": 1, "sources": [
    {"id": "fictional-source", "provider": "greenhouse", "board": "example"}]}
payload = json.dumps({"jobs": [{"id": 1, "title": "Fictional Engineer",
    "location": {"name": "Fictional City"}, "content": "<h2>Requirements</h2><p>Python.</p>"}],
    "meta": {"total": 1}}).encode()
def fetch(self, url, *, max_bytes, timeout):
    assert url == "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"
    return payload
results = []
for extra in (["--dry-run"], [], []):
    output = io.StringIO()
    with patch("grounded_apply.repositories.discovery_http.PublicJobHTTPTransport.get", fetch), \
         patch("sys.stdin", io.StringIO(json.dumps(manifest))), redirect_stdout(output):
        code = main(["jobs", "discover", "--sources-file", "-", *extra, "--json"])
    result = json.loads(output.getvalue())
    assert code == 0 and result["ok"], result
    results.append(result["data"])
assert not results[0]["storage_checked"] and results[0]["captures"] == []
first, retry = results[1]["captures"][0], results[2]["captures"][0]
assert first["job_id"] == retry["job_id"] and retry["replayed"]
print(json.dumps({"job_id": first["job_id"]}))
'''
        discovered = json.loads(run([str(executable), "-I", "-c", discovery_smoke],
                                    cwd=workspace, environ=environment))
        if cli("jobs", "show", "--job-id", discovered["job_id"])["capture_method"] != "public_ats_feed":
            raise RuntimeError("Installed discovery capture lost source provenance")
        fixtures = REPOSITORY / "tests" / "fixtures" / "synthetic_profile"
        import_args = (
            "profile", "import", "--source-file", str(fixtures / "resume.txt"),
            "--proposals-file", str(fixtures / "import_proposals.json"),
            "--idempotency-key", "synthetic-package-import",
        )
        cli(*import_args, "--dry-run")
        imported = cli(*import_args)
        if cli(*import_args) != imported:
            raise RuntimeError("Import replay changed its result")
        review = cli("profile", "review")
        item = review["items"][0]
        decision_args = (
            "profile", "decide", "--claim-id", item["claim"]["id"],
            "--review-token", item["review_token"], "--decision", "approve",
            "--actor-id", "synthetic-package-reviewer",
            "--idempotency-key", "synthetic-package-decision",
        )
        preview = cli(*decision_args)
        if preview["decision_recorded"] is not False:
            raise RuntimeError("Decision preview wrote a decision")
        decision = cli(*decision_args, "--confirm")
        if decision["decision_recorded"] is not True or decision["external_action_taken"] is not False:
            raise RuntimeError("Confirmed decision violated its contract")
        if cli(*decision_args, "--confirm") != decision:
            raise RuntimeError("Decision replay changed its result")
        if cli(*import_args) != imported:
            raise RuntimeError("Import replay broke after approval")
        if len(cli("profile", "review")["items"]) != len(review["items"]) - 1:
            raise RuntimeError("Approved item remained in pending review")
        cli("--log-events", "doctor")
        if dependencies is not None:
            print("Checking installed optional encryption, restore preview, and replay...", flush=True)
            passphrase = "synthetic-package-only-passphrase\n"
            archive = workspace / "synthetic-profile.gapply"
            backup_args = ("backup", "--encrypt", str(archive), "--passphrase-stdin")
            backup = cli(*backup_args, input_data=passphrase)
            replay = cli(*backup_args, input_data=passphrase)
            if replay["replayed"] is not True or backup["archive_sha256"] != replay["archive_sha256"]:
                raise RuntimeError("Installed backup replay did not preserve its archive")
            target = workspace / "restored"
            restore_args = (
                "restore", "--archive", str(archive), "--target-home", str(target),
                "--passphrase-stdin",
            )
            preview = cli(*restore_args, input_data=passphrase)
            if target.exists() or preview["requires_confirmation"] is not True:
                raise RuntimeError("Installed restore preview wrote runtime data")
            confirm_args = (*restore_args, "--archive-sha256", preview["archive_sha256"], "--confirm")
            restored = cli(*confirm_args, input_data=passphrase)
            if restored["dry_run"] is not False:
                raise RuntimeError("Installed confirmed restore did not complete")
            if cli(*confirm_args, input_data=passphrase)["replayed"] is not True:
                raise RuntimeError("Installed restore replay was not stable")
            environment["GROUNDED_APPLY_HOME"] = str(target)
            if cli(*import_args) != imported or cli(*decision_args, "--confirm") != decision:
                raise RuntimeError("Restored profile lost import or decision provenance")
            if len(cli("profile", "review")["items"]) != len(review["items"]) - 1:
                raise RuntimeError("Restored review queue changed")
            if cli("jobs", "show", "--job-id", discovered["job_id"])["capture_method"] != "public_ats_feed":
                raise RuntimeError("Restored discovery capture lost source provenance")
        if pilot_wheelhouse is not None:
            from check_pilot import check_pilot
            from check_batch import check_batch
            from check_search import check_search
            from check_schedule import check_schedule
            from check_source_window import check_source_window
            from check_search_filters import check_search_filters
            from check_source_rotation import check_source_rotation
            pilot = workspace / "pilot"
            pilot.mkdir(mode=0o700)
            check_pilot([str(command)], pilot)
            batch_workspace = workspace / "batch-pilot"
            batch_workspace.mkdir(mode=0o700)
            check_batch([str(command)], batch_workspace, include_backup=True)
            search_workspace = workspace / "search-pilot"
            search_workspace.mkdir(mode=0o700)
            check_search([str(command)], search_workspace, include_backup=True)
            schedule_workspace = workspace / "schedule-pilot"
            schedule_workspace.mkdir(mode=0o700)
            check_schedule([str(command)], schedule_workspace, include_backup=True)
            window_workspace = workspace / "source-window-pilot"
            window_workspace.mkdir(mode=0o700)
            check_source_window([str(command)], window_workspace, include_backup=True)
            filter_workspace = workspace / "search-filter-pilot"
            filter_workspace.mkdir(mode=0o700)
            check_search_filters([str(command)], filter_workspace, include_backup=True)
            rotation_workspace = workspace / "source-rotation-pilot"
            rotation_workspace.mkdir(mode=0o700)
            check_source_rotation([str(command)], rotation_workspace, include_backup=True)
    print("PASS — source archive, wheel contents, isolated install, CLI, migrations, and synthetic workflow")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backup-wheelhouse", type=Path,
        help="Local dependency wheels for an offline installed backup/restore gate.",
    )
    parser.add_argument("--pilot-wheelhouse", type=Path,
        help="Offline wheels for backup and materials extras; runs the full installed pilot (requires pdflatex).")
    options = parser.parse_args()
    check(backup_wheelhouse=options.backup_wheelhouse, pilot_wheelhouse=options.pilot_wheelhouse)
