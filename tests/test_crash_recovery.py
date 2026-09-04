"""Real abrupt-process-exit rollback recovery, using disposable synthetic data."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.repositories import SQLiteRepository


FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_profile"
CRASH_EXIT_CODE = 73
CRASH_SCRIPT = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1], isolation_level=None)
assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
connection.execute("PRAGMA synchronous=FULL")
connection.execute("PRAGMA cache_size=5")
connection.execute("PRAGMA cache_spill=ON")
connection.execute("BEGIN IMMEDIATE")
connection.execute(
    "UPDATE artifacts SET metadata_json = ? WHERE id = ?",
    ('{"synthetic_padding":"' + 'Z' * 524288 + '"}', sys.argv[2]),
)
# Deliberately bypass rollback, close, finally, and Python shutdown. Dirty pages
# have spilled out of the five-page cache while their original images remain
# in the rollback journal. The parent validates that evidence before using CLI.
os._exit(73)
"""


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.database = self.root / "data" / "grounded_apply.db"
        self.journal = Path(f"{self.database}-journal")
        self.assertEqual(self.invoke("profile", "init", "--json")[0], 0)
        self.metadata = {"synthetic_padding": "A" * 524288}
        with SQLiteRepository(self.database, existing_only=True) as repository:
            artifact = repository.add_artifact(
                artifact_type="synthetic_crash_fixture",
                uri="https://example.com/synthetic-crash-fixture",
                metadata=self.metadata,
            )
        self.artifact_id = str(artifact["id"])
        before = self.database.read_bytes()
        crashed = subprocess.run(
            [sys.executable, "-I", "-c", CRASH_SCRIPT, str(self.database), self.artifact_id],
            capture_output=True, timeout=15, check=False,
        )
        self.assertEqual(crashed.returncode, CRASH_EXIT_CODE, crashed.stderr.decode())
        self.assertEqual(crashed.stdout, b"")
        self.assertGreater(self.journal.stat().st_size, 512)
        self.assertEqual(self.journal.read_bytes()[:8], bytes.fromhex("d9d505f920a163d7"))
        self.assertNotEqual(self.database.read_bytes(), before, "Fixture did not spill dirty pages")
        self.assertEqual(stat.S_IMODE(self.journal.stat().st_mode), 0o600)

    def invoke(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(args)
        return result, stdout.getvalue(), stderr.getvalue()

    def snapshot(self) -> dict[str, tuple[bytes, int, int, int]]:
        return {
            path.name: (path.read_bytes(), path.stat().st_ino,
                        path.stat().st_mtime_ns, stat.S_IMODE(path.stat().st_mode))
            for path in self.database.parent.iterdir() if path.is_file()
        }

    def import_args(self) -> tuple[str, ...]:
        return (
            "profile", "import", "--source-file", str(FIXTURES / "resume.txt"),
            "--proposals-file", str(FIXTURES / "import_proposals.json"),
            "--idempotency-key", "synthetic-crash-recovery-import", "--json",
        )

    def test_read_only_commands_refuse_a_real_hot_journal_unchanged(self) -> None:
        before = self.snapshot()
        for args in (("doctor", "--json"), ("profile", "review", "--json")):
            with self.subTest(command=args):
                code, stdout, stderr = self.invoke(*args)
                self.assertEqual(code, 2)
                self.assertFalse(json.loads(stdout)["ok"])
                self.assertIn("sidecar", stdout)
                self.assertEqual(stderr, "")
                self.assertEqual(self.snapshot(), before)

    def test_mutating_import_recovers_then_imports_and_replays_once(self) -> None:
        args = self.import_args()
        code, stdout, stderr = self.invoke(*args)
        self.assertEqual(code, 0, stdout + stderr)
        result = json.loads(stdout)
        self.assertFalse(self.journal.exists())
        with SQLiteRepository(self.database, read_only=True) as repository:
            restored = repository.get_artifact(self.artifact_id)
            self.assertIsNotNone(restored)
            self.assertEqual(json.loads(restored["metadata_json"]), self.metadata)
            self.assertEqual(len(repository.list_workflow_runs()), 1)
        retry_code, retry, retry_stderr = self.invoke(*args)
        self.assertEqual(retry_code, 0, retry + retry_stderr)
        self.assertEqual(json.loads(retry), result)
        with SQLiteRepository(self.database, read_only=True) as repository:
            self.assertEqual(len(repository.list_workflow_runs()), 1)
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)

    def test_unsafe_hot_journal_is_refused_before_recovery(self) -> None:
        self.journal.chmod(0o640)
        before = self.snapshot()
        code, stdout, stderr = self.invoke(*self.import_args())
        self.assertEqual(code, 2)
        self.assertIn("group or other access", stdout)
        self.assertEqual(stderr, "")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
