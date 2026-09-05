"""Synthetic deletion policy, refusal, race, and lifecycle tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.config import DEFAULT_CONFIG, resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.deletion_files import LocalDeletionStorage
from grounded_apply.services.deletion import DeletionError, DeletionOutcomeUnknownError, DeletionService


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


if __name__ == "__main__":
    unittest.main()
