#!/usr/bin/env python3
"""Build and exercise a wheel outside the checkout with synthetic data only.

Run using a Python environment containing build and Hatchling. The build uses
those installed tools without downloading dependencies. The test installation
is a fresh virtual environment; source PYTHONPATH and user site imports are
disabled. All generated files are disposable and outside the checkout.
"""

from __future__ import annotations

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


def run(command: list[str], *, cwd: Path, environ: dict[str, str]) -> str:
    result = subprocess.run(
        command, cwd=cwd, env=environ, text=True, capture_output=True,
        timeout=120, check=False,
    )
    if result.returncode:
        # Every payload in this script is synthetic; never run this on a user's
        # runtime database or reuse this helper for private support artifacts.
        raise RuntimeError(
            f"Package check command failed ({result.returncode}):\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout


def check() -> None:
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
                "grounded_apply/diagnostics.py",
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
        for args in ([], ["profile", "import"], ["profile", "review"], ["profile", "decide"]):
            run([str(command), *args, "--help"], cwd=workspace, environ=environment)

        def cli(*args: str) -> dict[str, Any]:
            result = json.loads(run(
                [str(command), *args, "--json"], cwd=workspace, environ=environment,
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
    print("PASS — source archive, wheel contents, isolated install, CLI, migrations, and synthetic workflow")


if __name__ == "__main__":
    check()
