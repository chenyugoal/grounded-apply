from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES
from grounded_apply.services.jobs import JobService
from grounded_apply.services.storage_capacity import storage_capacity


class StorageCapacityTests(unittest.TestCase):
    def test_snapshot_boundary_and_advisory_do_not_promise_space_for_an_operation(self) -> None:
        threshold = (MAX_SNAPSHOT_BYTES * 9 + 9) // 10
        self.assertEqual(storage_capacity(threshold - 1).status, "available")
        self.assertEqual(storage_capacity(threshold).status, "near_limit")
        at_limit = storage_capacity(MAX_SNAPSHOT_BYTES)
        self.assertEqual((at_limit.status, at_limit.remaining_bytes), ("at_limit", 0))
        exceeded = storage_capacity(MAX_SNAPSHOT_BYTES + 1)
        self.assertEqual((exceeded.status, exceeded.remaining_bytes), ("exceeded", 0))
        for invalid in (True, -1, 1.5, "12", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                storage_capacity(invalid)

    def test_doctor_reports_actual_growth_without_mutation_or_backup_claim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gapply-fictional-storage-") as directory:
            home = Path(directory) / "fictional-home"
            database = home / "data" / "grounded_apply.db"
            # Exercise reporting boundaries with a small fixture; production
            # capacity and a profile above the old cap have separate coverage.
            fixture_limit = 16 * 1024 * 1024
            with patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(home)}), patch(
                "grounded_apply.services.storage_capacity.MAX_SNAPSHOT_BYTES", fixture_limit,
            ):
                def invoke(*args: str) -> tuple[int, dict, str]:
                    output, errors = io.StringIO(), io.StringIO()
                    with redirect_stdout(output), redirect_stderr(errors):
                        code = main([*args, "--json"])
                    return code, json.loads(output.getvalue()), errors.getvalue()

                self.assertEqual(invoke("profile", "init")[0], 0)
                for jobs_to_add, status, expected_code in ((0, "available", 0), (15, "near_limit", 0), (2, "exceeded", 2)):
                    with self.subTest(status=status):
                        # Use the public manual-capture service. Unlike daily
                        # discovery, older manual writes have no global size cap.
                        if jobs_to_add:
                            with SQLiteRepository(database, existing_only=True) as repository:
                                jobs = JobService(repository)
                                text = "Fictional capacity fixture only. " * 30_000
                                for index in range(jobs_to_add):
                                    key = f"fictional-{status}-{index}"
                                    jobs.add(f"https://example.com/{key}", text, idempotency_key=key)
                        before, metadata = database.read_bytes(), database.stat()
                        code, payload, errors = invoke("doctor")
                        capacity = payload["data"]["checks"]["database"]["storage"]
                        self.assertEqual((code, capacity["status"]), (expected_code, status))
                        self.assertEqual(capacity["database_file_bytes"], len(before))
                        self.assertEqual(capacity["snapshot_limit_bytes"], fixture_limit)
                        self.assertEqual(capacity["remaining_bytes"], max(0, fixture_limit - len(before)))
                        self.assertEqual(database.read_bytes(), before)
                        self.assertEqual(database.stat().st_mtime_ns, metadata.st_mtime_ns)
                        self.assertFalse(Path(str(database) + "-journal").exists())
                        self.assertEqual(errors, "")
                        if status != "available":
                            self.assertIn("not a backup validation", payload["warnings"][0])
                            self.assertNotIn("Fictional capacity fixture", json.dumps(payload))

    def test_uninitialized_doctor_does_not_invent_capacity_or_create_storage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gapply-fictional-no-storage-") as directory:
            home = Path(directory) / "not-created"
            output = io.StringIO()
            with patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(home)}), redirect_stdout(output):
                self.assertEqual(main(["doctor", "--json"]), 0)
            report = json.loads(output.getvalue())
            self.assertNotIn("storage", report["data"]["checks"]["database"])
            self.assertFalse(home.exists())


if __name__ == "__main__":
    unittest.main()
