"""Synthetic deletion policy, refusal, race, and lifecycle tests."""

from __future__ import annotations

import json
import os
import tempfile
import tracemalloc
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.config import DEFAULT_CONFIG, resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.deletion_files import LocalDeletionStorage, _file_digest
from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES
from grounded_apply.services.deletion import DeletionError, DeletionOutcomeUnknownError, DeletionService
from grounded_apply.services.jobs import JobService


class DeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.target = self.parent / "synthetic-vault"
        self.receipt = self.parent / "receipt.jsonl"
        self.paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(self.target)})
        self.paths.ensure_private_directories()
        self.paths.config_file.write_text(DEFAULT_CONFIG)
        self.paths.config_file.chmod(0o600)
        with SQLiteRepository(self.paths.database):
            pass
        self.adapter = LocalDeletionStorage()
        self.service = DeletionService(self.adapter)

    def test_preview_confirmation_and_exact_replay(self) -> None:
        original = self.paths.database.read_bytes()
        preview = self.service.delete(self.target, self.receipt)
        self.assertTrue(preview.dry_run)
        self.assertFalse(preview.deleted)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(original, self.paths.database.read_bytes())
        self.assertEqual(preview.files, 2)
        result = self.service.delete(self.target, self.receipt, confirm=True, preview_token=preview.preview_token)
        self.assertTrue(result.deleted)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        records = [json.loads(line) for line in self.receipt.read_text().splitlines()]
        self.assertEqual([r["phase"] for r in records], ["started", "completed"])
        self.assertNotIn(str(self.target), self.receipt.read_text())
        self.assertNotIn("config.toml", self.receipt.read_text())
        replay = self.service.delete(self.target, self.receipt, confirm=True, preview_token=preview.preview_token)
        self.assertTrue(replay.replayed)

    def test_missing_wrong_token_and_direct_adapter_fail_without_writes(self) -> None:
        inventory = self.adapter.inventory(self.target, self.receipt)
        for action in (
            lambda: self.service.delete(self.target, self.receipt, confirm=True),
            lambda: self.service.delete(self.target, self.receipt, confirm=True, preview_token="0" * 64),
            lambda: self.adapter.remove(inventory, confirm=False, token=inventory.token),
            lambda: self.adapter.remove(inventory, confirm=True, token="0" * 64),
        ):
            with self.assertRaises(DeletionError):
                action()
            self.assertTrue(self.paths.database.exists())
            self.assertFalse(self.receipt.exists())

    def test_changed_content_invalidates_preview(self) -> None:
        preview = self.service.delete(self.target, self.receipt)
        self.paths.config_file.write_text(DEFAULT_CONFIG + "# changed\n")
        with self.assertRaises(DeletionError):
            self.service.delete(self.target, self.receipt, confirm=True, preview_token=preview.preview_token)
        self.assertTrue(self.paths.database.exists())
        self.assertFalse(self.receipt.exists())

    def test_unknown_file_refused_even_in_known_directory(self) -> None:
        unknown = self.paths.generated / "unregistered.txt"
        unknown.write_text("Fictional data")
        unknown.chmod(0o600)
        with self.assertRaises(DeletionError):
            self.service.delete(self.target, self.receipt)
        self.assertTrue(unknown.exists())

    def test_link_permission_or_sqlite_sidecar_refused(self) -> None:
        original = self.paths.config_file.read_bytes()
        outside = self.parent / "outside.txt"
        outside.write_bytes(original)
        outside.chmod(0o600)
        self.paths.config_file.unlink()
        self.paths.config_file.symlink_to(outside)
        with self.assertRaises(DeletionError):
            self.service.delete(self.target, self.receipt)
        self.paths.config_file.unlink()
        os.link(outside, self.paths.config_file)
        with self.assertRaises(DeletionError):
            self.service.delete(self.target, self.receipt)
        self.paths.config_file.unlink()
        self.paths.config_file.write_bytes(original)
        self.paths.config_file.chmod(0o644)
        with self.assertRaises(DeletionError):
            self.service.delete(self.target, self.receipt)
        self.paths.config_file.chmod(0o600)
        sidecar = Path(str(self.paths.database) + "-wal")
        sidecar.write_bytes(b"synthetic")
        sidecar.chmod(0o600)
        with self.assertRaises(DeletionError):
            self.service.delete(self.target, self.receipt)
        self.assertEqual(outside.read_bytes(), original)

    def test_target_symlink_and_internal_receipt_refused(self) -> None:
        alias = self.parent / "alias"
        alias.symlink_to(self.target, target_is_directory=True)
        for target, receipt in ((alias, self.receipt), (self.target, self.target / "receipt.jsonl")):
            with self.assertRaises(DeletionError):
                self.service.delete(target, receipt)
        self.assertTrue(self.paths.database.exists())

    def test_partial_removal_is_audited_and_never_resumed(self) -> None:
        preview = self.service.delete(self.target, self.receipt)
        with patch("grounded_apply.repositories.deletion_files.os.unlink", side_effect=OSError("private path")):
            with self.assertRaisesRegex(DeletionOutcomeUnknownError, "Inspect the external receipt"):
                self.service.delete(self.target, self.receipt, confirm=True, preview_token=preview.preview_token)
        self.assertTrue(self.receipt.exists())
        self.assertEqual(len(self.receipt.read_text().splitlines()), 1)
        with self.assertRaises(DeletionError):
            self.service.delete(self.target, self.receipt, confirm=True, preview_token=preview.preview_token)
        self.assertTrue(self.paths.database.exists())

    def test_cli_does_not_accept_abbreviated_confirmation(self) -> None:
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["delete", "--target-home", str(self.target), "--receipt", str(self.receipt), "--conf", "--json"])
        self.assertEqual(code, 2)
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.paths.database.exists())

    def test_cli_roundtrip_and_content_free_events(self) -> None:
        import contextlib
        import io
        args = ["--log-events", "delete", "--target-home", str(self.target), "--receipt", str(self.receipt), "--json"]
        output, events = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(events):
            self.assertEqual(main(args), 0)
        token = json.loads(output.getvalue())["data"]["preview_token"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(events):
            self.assertEqual(main([*args, "--preview-token", token, "--confirm"]), 0)
        self.assertFalse(self.target.exists())
        self.assertNotIn(str(self.target), events.getvalue())
        self.assertNotIn(token, events.getvalue())

    def test_change_after_descriptor_open_is_refused_before_receipt(self) -> None:
        preview = self.service.delete(self.target, self.receipt)
        original = self.adapter.inventory
        calls = 0
        def changing(target: Path, receipt: Path):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.paths.config_file.write_text(DEFAULT_CONFIG + "# change\n")
            return original(target, receipt)
        with patch.object(self.adapter, "inventory", side_effect=changing):
            with self.assertRaises(DeletionError):
                self.service.delete(self.target, self.receipt, confirm=True, preview_token=preview.preview_token)
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.paths.database.exists())

    def test_database_above_former_cap_supports_preview_delete_and_replay(self) -> None:
        line = "Fictional public role background; capacity fixture only. ".ljust(1023, "x") + "\n"
        source = "Fictional Example Robotics LLC.\n" + line * 1023
        self.assertLessEqual(len(source.encode()), 1024 * 1024)
        with SQLiteRepository(self.paths.database) as repository:
            service = JobService(repository)
            for index in range(18):
                service.add(
                    f"https://example.com/fictional-capacity-jobs/{index}", source,
                    idempotency_key=f"fictional-capacity-job-{index}",
                )
        self.assertGreater(self.paths.database.stat().st_size, 16 * 1024 * 1024)
        self.assertLessEqual(self.paths.database.stat().st_size, MAX_SNAPSHOT_BYTES)
        original_digest = sha256(self.paths.database.read_bytes()).hexdigest()
        preview = self.service.delete(self.target, self.receipt)
        self.assertTrue(preview.dry_run)
        self.assertEqual(sha256(self.paths.database.read_bytes()).hexdigest(), original_digest)
        result = self.service.delete(
            self.target, self.receipt, confirm=True, preview_token=preview.preview_token,
        )
        self.assertTrue(result.deleted)
        self.assertFalse(self.target.exists())
        replay = self.service.delete(
            self.target, self.receipt, confirm=True, preview_token=preview.preview_token,
        )
        self.assertTrue(replay.replayed)

    def test_large_file_hash_uses_bounded_reads_and_memory(self) -> None:
        content = b"fictional-hash-fixture\n" * 900000
        self.paths.config_file.write_bytes(content)
        expected = sha256(content).hexdigest()
        del content
        original_read = os.read
        tracemalloc.start()
        try:
            with patch("grounded_apply.repositories.deletion_files.os.read", wraps=original_read) as reads:
                digest, metadata = _file_digest(self.paths.config_file, limit=MAX_SNAPSHOT_BYTES)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(digest, expected)
        self.assertGreater(metadata.st_size, 16 * 1024 * 1024)
        self.assertGreater(reads.call_count, 1)
        self.assertTrue(all(0 < call.args[1] <= 64 * 1024 for call in reads.call_args_list))
        self.assertLess(peak, 2 * 1024 * 1024)

    def test_database_and_auxiliary_limits_refuse_without_writes(self) -> None:
        restore_receipt = self.target / "restore-receipt.json"
        for path, limit in (
            (self.paths.database, MAX_SNAPSHOT_BYTES),
            (self.paths.config_file, 16 * 1024 * 1024),
            (restore_receipt, 16 * 1024 * 1024),
        ):
            with self.subTest(file=path.name):
                existed = path.exists()
                original = path.read_bytes() if existed else b""
                with path.open("wb") as output:
                    output.truncate(limit + 1)
                path.chmod(0o600)
                with self.assertRaises(DeletionError):
                    self.service.delete(self.target, self.receipt)
                self.assertFalse(self.receipt.exists())
                self.assertEqual(path.stat().st_size, limit + 1)
                if existed:
                    path.write_bytes(original)
                else:
                    path.unlink()

    def test_hash_at_exact_bound_and_one_byte_over(self) -> None:
        self.paths.config_file.write_bytes(b"fictional")
        digest, metadata = _file_digest(self.paths.config_file, limit=9)
        self.assertEqual(digest, sha256(b"fictional").hexdigest())
        self.assertEqual(metadata.st_size, 9)
        with patch("grounded_apply.repositories.deletion_files.os.read") as reads:
            with self.assertRaises(DeletionError):
                _file_digest(self.paths.config_file, limit=8)
        reads.assert_not_called()

    def test_aggregate_inventory_limit_fails_before_receipt(self) -> None:
        with patch("grounded_apply.repositories.deletion_files._MAX_INVENTORY_BYTES", 1):
            with self.assertRaisesRegex(DeletionError, "inventory exceeds"):
                self.service.delete(self.target, self.receipt)
        self.assertFalse(self.receipt.exists())
        self.assertTrue(self.paths.database.exists())

    def test_changed_file_during_streaming_hash_fails_before_receipt(self) -> None:
        for change in ("content", "growth", "replacement", "permissions", "hardlink"):
            with self.subTest(change=change):
                path = self.paths.config_file
                path.write_bytes(b"f" * (2 * 64 * 1024))
                path.chmod(0o600)
                original_read = os.read
                changed = False
                outside = self.parent / "changed-file"
                subject = path.stat()

                def changing_read(descriptor: int, size: int) -> bytes:
                    nonlocal changed
                    block = original_read(descriptor, size)
                    opened = os.fstat(descriptor)
                    if not changed and (opened.st_dev, opened.st_ino) == (subject.st_dev, subject.st_ino):
                        changed = True
                        if change == "content":
                            with path.open("r+b") as output:
                                output.write(b"z")
                        elif change == "growth":
                            with path.open("ab") as output:
                                output.write(b"z")
                        elif change == "replacement":
                            path.rename(outside)
                            path.write_bytes(b"f" * (2 * 64 * 1024))
                            path.chmod(0o600)
                        elif change == "permissions":
                            path.chmod(0o644)
                        else:
                            os.link(path, outside)
                    return block

                with patch("grounded_apply.repositories.deletion_files.os.read", side_effect=changing_read):
                    with self.assertRaises(DeletionError):
                        self.service.delete(self.target, self.receipt)
                self.assertTrue(changed)
                self.assertFalse(self.receipt.exists())
                self.assertTrue(self.paths.database.exists())
                if outside.exists():
                    outside.unlink()


if __name__ == "__main__":
    unittest.main()
