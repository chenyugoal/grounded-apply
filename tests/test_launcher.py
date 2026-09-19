from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class LauncherTests(unittest.TestCase):
    def test_override_and_local_environment_survive_spaces_and_forward_arguments(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gapply launcher ") as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts" / "python", root / "scripts" / "python")
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python3").symlink_to(sys.executable)
            environment = dict(os.environ)
            environment.pop("GAPPLY_PYTHON", None)
            code = "import json,sys; print(json.dumps(sys.argv[1:]))"
            literal = "literal spaces ; $(not-a-command)"
            command = ["/bin/sh", str(root / "scripts" / "python"), "-c", code, literal]
            process = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), [literal])
            # An explicit invalid override must fail, not silently use .venv.
            environment["GAPPLY_PYTHON"] = str(root / "missing-python")
            process = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(process.stdout, "")
            self.assertIn("Python 3.12+", process.stderr)
            self.assertNotIn("Traceback", process.stderr)
            environment["GAPPLY_PYTHON"] = sys.executable
            (root / ".venv" / "bin" / "python3").unlink()
            process = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), [literal])

    def test_repository_help_needs_no_profile_or_shell_activation(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "never-created"
            environment = dict(os.environ, GAPPLY_PYTHON=sys.executable, GROUNDED_APPLY_HOME=str(runtime))
            process = subprocess.run([str(repository / "scripts" / "gapply"), "brief", "--help"],
                cwd=directory, env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("--follow-up-days", process.stdout)
            self.assertFalse(runtime.exists())
