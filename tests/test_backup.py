from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from grounded_apply.cli import main
from grounded_apply.config import resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.backup_crypto import FernetBackupCipher, _MAGIC, _SCOPE
from grounded_apply.repositories.backup_files import (
    LocalBackupStorage, read_private_file, write_private_file,
)
from grounded_apply.repositories.snapshots import validate_profile_snapshot
from grounded_apply.services.backup import BackupError, BackupService, validate_passphrase


HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
PASSPHRASE = b"synthetic-test-only-passphrase"
FIXTURES = Path(__file__).parent / "fixtures" / "synthetic_profile"


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="gapply-synthetic-backup-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.database = self.root / "profile.db"
        with SQLiteRepository(self.database):
            pass

    def image(self) -> bytes:
        with SQLiteRepository(self.database, read_only=True) as repository:
            return repository.snapshot_bytes(max_bytes=16 * 1024 * 1024)

    def changed_image(self, sql: str) -> bytes:
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as connection:
            connection.deserialize(self.image())
            connection.execute(sql)
            return connection.serialize()

    def test_snapshot_round_trip_is_read_only_and_size_bounded(self) -> None:
        before = self.database.read_bytes(), self.database.stat()
        image = self.image()
        validate_profile_snapshot(image)
        self.assertEqual(image, self.image())
        self.assertEqual(before[0], self.database.read_bytes())
        self.assertEqual(before[1].st_mtime_ns, self.database.stat().st_mtime_ns)
        self.assertEqual(set(self.root.iterdir()), {self.database})
        with SQLiteRepository(self.database, read_only=True) as repository:
            with self.assertRaisesRegex(RuntimeError, "size"):
                repository.snapshot_bytes(max_bytes=100)
            self.assertEqual(repository.snapshot_bytes(max_bytes=len(image)), image)
        with SQLiteRepository(self.database) as repository:
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                repository.snapshot_bytes(max_bytes=len(image))

    def test_snapshot_deadline_rolls_back_and_keeps_repository_usable(self) -> None:
        image = self.image()
        with SQLiteRepository(self.database, read_only=True) as repository:
            with patch("grounded_apply.repositories.sqlite.time.monotonic", side_effect=[0.0, 10.0]):
                with self.assertRaisesRegex(RuntimeError, "time budget"):
                    repository.snapshot_bytes(max_bytes=len(image))
            self.assertEqual(repository.snapshot_bytes(max_bytes=len(image)), image)

    def test_snapshot_refuses_unknown_schema_and_modified_ledger(self) -> None:
        for sql in (
            "PRAGMA user_version=999", "PRAGMA user_version=1",
            "CREATE TABLE injected (secret TEXT)",
            "CREATE VIEW injected AS SELECT * FROM claims",
            "DROP INDEX ix_artifacts_content_sha256",
            "UPDATE schema_migrations SET checksum_sha256='" + "0" * 64 + "'",
            "CREATE TRIGGER injected AFTER INSERT ON claims BEGIN DELETE FROM evidence; END",
        ):
            with self.subTest(sql=sql), self.assertRaises(BackupError):
                validate_profile_snapshot(self.changed_image(sql))

    def test_snapshot_refuses_truncated_oversized_and_wal_images(self) -> None:
        image = self.image()
        for invalid in (
            b"", image[:-1], image + b"padding", image[:18] + b"\x02\x02" + image[20:],
            image[:16] + b"\x00\x00" + image[18:],
        ):
            with self.subTest(size=len(invalid)), self.assertRaises(BackupError):
                validate_profile_snapshot(invalid)

    def test_snapshot_refuses_corruption_and_external_artifact_omission(self) -> None:
        image = bytearray(self.image())
        image[100] = 255
        with self.assertRaises(BackupError):
            validate_profile_snapshot(bytes(image))
        with SQLiteRepository(self.database) as repository:
            repository.add_artifact(
                artifact_type="synthetic_document", local_path="synthetic/resume.txt",
            )
        with self.assertRaisesRegex(BackupError, "omit"):
            validate_profile_snapshot(self.image())

    def test_source_refuses_sidecars_links_and_wal_without_recovery(self) -> None:
        paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(self.root / "profile")})
        paths.ensure_private_directories()
        with SQLiteRepository(paths.database):
            pass
        storage = LocalBackupStorage(paths)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(paths.database) + suffix)
            sidecar.write_bytes(b"synthetic journal sentinel")
            sidecar.chmod(0o600)
            before = paths.database.read_bytes()
            with self.assertRaises(ValueError):
                storage.capture_profile()
            self.assertEqual(paths.database.read_bytes(), before)
            self.assertEqual(sidecar.read_bytes(), b"synthetic journal sentinel")
            sidecar.unlink()
        link = paths.database.with_name("alias.db")
        link.hardlink_to(paths.database)
        with self.assertRaises(ValueError):
            storage.capture_profile()
        link.unlink()
        with closing(sqlite3.connect(paths.database)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        before = paths.database.read_bytes()
        with self.assertRaises(ValueError):
            storage.capture_profile()
        self.assertEqual(paths.database.read_bytes(), before)

    def test_private_file_reads_and_writes_refuse_unsafe_paths(self) -> None:
        path = self.root / "archive.gapply"
        write_private_file(path, b"synthetic encrypted bytes")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            write_private_file(path, b"replacement")
        self.assertEqual(read_private_file(path, limit=100), b"synthetic encrypted bytes")
        with self.assertRaises(BackupError):
            read_private_file(path, limit=2)
        for kind in ("symlink", "hardlink", "fifo", "directory"):
            unsafe = self.root / kind
            if kind == "symlink":
                unsafe.symlink_to(path)
            elif kind == "hardlink":
                unsafe.hardlink_to(path)
            elif kind == "fifo":
                os.mkfifo(unsafe, 0o600)
            else:
                unsafe.mkdir(mode=0o700)
            with self.subTest(kind=kind), self.assertRaises(BackupError):
                read_private_file(unsafe, limit=100)
            unsafe.rmdir() if kind == "directory" else unsafe.unlink()
        path.chmod(0o644)
        with self.assertRaises(BackupError):
            read_private_file(path, limit=100)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        broad = self.root / "broad"
        broad.mkdir(mode=0o755)
        with self.assertRaises(BackupError):
            write_private_file(broad / "archive", b"synthetic")
        git = self.root / "git"
        git.mkdir(mode=0o700)
        (git / ".git").mkdir()
        with self.assertRaises(BackupError):
            write_private_file(git / "archive", b"synthetic")

    def test_read_refuses_input_change_and_closes_descriptor(self) -> None:
        path = self.root / "archive.gapply"
        write_private_file(path, b"synthetic original")
        real_read = os.read

        def change(descriptor: int, size: int) -> bytes:
            result = real_read(descriptor, size)
            path.write_bytes(b"synthetic changed!")
            return result

        with patch("os.read", side_effect=change), self.assertRaises(BackupError):
            read_private_file(path, limit=100)

    def test_failed_exclusive_output_is_private_and_never_overwritten(self) -> None:
        path = self.root / "partial.gapply"
        with patch("os.write", return_value=0), self.assertRaises(BackupError):
            write_private_file(path, b"synthetic bytes")
        self.assertEqual(path.read_bytes(), b"")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            write_private_file(path, b"retry bytes")

    def test_restore_adapter_requires_confirmation_and_exact_unchanged_target(self) -> None:
        storage = LocalBackupStorage()
        image = self.image()
        target = self.root / "restored"
        digest = "a" * 64
        self.assertFalse(storage.restore_profile(target, image, digest, confirm=False))
        self.assertFalse(target.exists())
        self.assertFalse(storage.restore_profile(target, image, digest, confirm=True))
        self.assertTrue(storage.restore_profile(target, image, digest, confirm=True))
        self.assertEqual((target / "data" / "grounded_apply.db").read_bytes(), image)
        for file in target.rglob("*"):
            self.assertEqual(file.stat().st_mode & 0o777, 0o700 if file.is_dir() else 0o600)
        with self.assertRaises(BackupError):
            storage.restore_profile(target, image, "b" * 64, confirm=True)
        (target / "data" / "artifacts" / "extra").write_bytes(b"synthetic")
        with self.assertRaises(BackupError):
            storage.restore_profile(target, image, digest, confirm=True)

    def test_restore_refuses_symlink_empty_existing_and_partial_targets(self) -> None:
        storage, image = LocalBackupStorage(), self.image()
        for kind in ("symlink", "existing", "partial"):
            target = self.root / kind
            if kind == "symlink":
                target.symlink_to(self.root / "absent", target_is_directory=True)
            else:
                target.mkdir(mode=0o700)
                if kind == "partial":
                    (target / "data").mkdir(mode=0o700)
            with self.subTest(kind=kind), self.assertRaises((BackupError, ValueError)):
                storage.restore_profile(target, image, "a" * 64, confirm=True)
        self.assertFalse((self.root / "absent").exists())

    def test_restore_interrupt_before_receipt_does_not_adopt_partial_target(self) -> None:
        storage, image = LocalBackupStorage(), self.image()
        target = self.root / "interrupted"
        actual_write = write_private_file

        def fail_receipt(path: Path, content: bytes) -> None:
            if path.name == "restore-receipt.json":
                raise KeyboardInterrupt
            actual_write(path, content)

        with patch("grounded_apply.repositories.backup_files.write_private_file", side_effect=fail_receipt):
            with self.assertRaises(KeyboardInterrupt):
                storage.restore_profile(target, image, "a" * 64, confirm=True)
        before = (target / "data" / "grounded_apply.db").read_bytes()
        with self.assertRaises(BackupError):
            storage.restore_profile(target, image, "a" * 64, confirm=True)
        self.assertEqual(before, image)
        self.assertFalse((target / "restore-receipt.json").exists())

    def test_passphrases_are_bounded_without_normalizing_secrets(self) -> None:
        for invalid in (b"short", b" " * 12, b"x" * 1025, b"secret\npassphrase", b"\xff" * 12, "text"):
            with self.subTest(value_type=type(invalid)), self.assertRaises(BackupError):
                validate_passphrase(invalid)  # type: ignore[arg-type]
        validate_passphrase(PASSPHRASE)
        validate_passphrase(b"  spaced passphrase  ")

    def test_missing_provider_fails_without_storage_or_secret_read(self) -> None:
        output = io.StringIO()
        with patch.dict("sys.modules", {"cryptography.fernet": None}), redirect_stdout(output):
            with patch("grounded_apply.cli._read_backup_passphrase") as read:
                self.assertEqual(main(["backup", "--encrypt", str(self.root / "absent"), "--json"]), 2)
                read.assert_not_called()
        self.assertIn("optional", json.loads(output.getvalue())["error"]["message"])
        self.assertFalse((self.root / "absent").exists())

    def test_interrupted_lifecycle_event_preserves_content_free_recovery(self) -> None:
        for command in ("backup", "restore"):
            args = (
                ["--encrypt", str(self.root / "new")]
                if command == "backup" else
                ["--archive", str(self.root / "archive"), "--target-home", str(self.root / "new")]
            )
            output, events = io.StringIO(), io.StringIO()
            with (
                patch("grounded_apply.cli._command_" + command, side_effect=KeyboardInterrupt),
                redirect_stdout(output), redirect_stderr(events),
            ):
                self.assertEqual(main(["--log-events", command, *args, "--json"]), 130)
            records = [json.loads(line) for line in events.getvalue().splitlines()]
            self.assertEqual(records[-1]["outcome"], "backup_outcome_unknown")
            self.assertIn("new destination", records[-1]["recovery"])
            self.assertNotIn(str(self.root), events.getvalue())
            self.assertEqual(output.getvalue(), "")


@unittest.skipUnless(HAS_CRYPTO, "optional backup extra; run scripts/check_backup.py with it installed")
class EncryptedBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="gapply-synthetic-encryption-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "profile"
        self.paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(self.home)})
        self.cipher = FernetBackupCipher()
        self.storage = LocalBackupStorage(self.paths)
        self.service = BackupService(self.storage, self.cipher)
        self.archive = self.root / "profile.gapply"
        self.invoke("profile", "init")
        self.import_args = (
            "profile", "import", "--source-file", str(FIXTURES / "resume.txt"),
            "--proposals-file", str(FIXTURES / "import_proposals.json"),
            "--idempotency-key", "synthetic-backup-import",
        )
        self.imported = self.invoke(*self.import_args)

    def invoke(
        self, *arguments: str, home: Path | None = None, passphrase: bytes = PASSPHRASE,
        expected: int = 0,
    ) -> dict[str, object]:
        output, errors = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(home or self.home)}),
            patch("sys.stdin", io.TextIOWrapper(io.BytesIO(passphrase + b"\n"))),
            redirect_stdout(output), redirect_stderr(errors),
        ):
            code = main([*arguments, "--json"])
        self.assertEqual(code, expected, output.getvalue() + errors.getvalue())
        self.assertNotIn(PASSPHRASE.decode(), output.getvalue() + errors.getvalue())
        return json.loads(output.getvalue())

    def test_encrypted_roundtrip_preserves_review_approval_and_exact_replay(self) -> None:
        review = self.invoke("profile", "review")["data"]["items"]
        first = review[0]
        decision_args = (
            "profile", "decide", "--claim-id", first["claim"]["id"],
            "--review-token", first["review_token"], "--decision", "approve",
            "--actor-id", "synthetic-backup-reviewer", "--idempotency-key", "synthetic-approval",
            "--confirm",
        )
        decided = self.invoke(*decision_args)
        pending = self.invoke("profile", "review")
        before = self.paths.database.read_bytes()
        result = self.invoke("backup", "--encrypt", str(self.archive), "--passphrase-stdin")["data"]
        encrypted = self.archive.read_bytes()
        self.assertNotIn(b"SQLite format", encrypted)
        self.assertNotIn(b"Avery", encrypted)
        self.assertNotIn(PASSPHRASE, encrypted)
        self.assertEqual(self.paths.database.read_bytes(), before)
        target = self.root / "restored"
        restore_args = ("restore", "--archive", str(self.archive), "--target-home", str(target), "--passphrase-stdin")
        preview = self.invoke(*restore_args)["data"]
        self.assertTrue(preview["dry_run"])
        self.assertEqual(preview["archive_sha256"], result["archive_sha256"])
        self.assertFalse(target.exists())
        confirmed = (*restore_args, "--archive-sha256", preview["archive_sha256"], "--confirm")
        self.assertFalse(self.invoke(*confirmed)["data"]["replayed"])
        self.assertTrue(self.invoke(*confirmed)["data"]["replayed"])
        self.assertEqual(self.invoke("profile", "review", home=target), pending)
        self.assertEqual(self.invoke(*decision_args, home=target), decided)
        self.assertEqual(self.invoke(*self.import_args, home=target), self.imported)

    def test_creation_preview_and_named_destination_replay(self) -> None:
        preview = self.service.create(self.archive, PASSPHRASE, dry_run=True)
        self.assertTrue(preview.dry_run)
        self.assertFalse(self.archive.exists())
        first = self.service.create(self.archive, PASSPHRASE)
        bytes_before = self.archive.read_bytes()
        second = self.service.create(self.archive, PASSPHRASE)
        self.assertTrue(second.replayed)
        self.assertEqual(first.archive_sha256, second.archive_sha256)
        self.assertEqual(self.archive.read_bytes(), bytes_before)
        with self.assertRaises(BackupError):
            self.service.create(self.archive, b"synthetic-wrong-passphrase")
        self.assertEqual(self.archive.read_bytes(), bytes_before)

    def test_changed_source_cannot_overwrite_named_archive(self) -> None:
        self.service.create(self.archive, PASSPHRASE)
        before = self.archive.read_bytes()
        with SQLiteRepository(self.paths.database) as repository:
            repository.add_workflow_run(workflow_type="synthetic_test")
        with self.assertRaisesRegex(BackupError, "different snapshot"):
            self.service.create(self.archive, PASSPHRASE)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_tampering_truncation_unknown_versions_and_wrong_password_fail_closed(self) -> None:
        result = self.service.create(self.archive, PASSPHRASE)
        archive = self.archive.read_bytes()
        altered = bytearray(archive)
        altered[len(_MAGIC)] ^= 1
        raw = bytearray(base64.urlsafe_b64decode(archive[len(_MAGIC) + 16:]))
        raw[-1] ^= 1
        for invalid in (
            bytes(altered), archive[:-1], archive + b"ignored suffix", archive + b"\n",
            b"GAPPLY-BACKUP\x00\x02" + archive[len(_MAGIC):],
            archive[:len(_MAGIC) + 16] + base64.urlsafe_b64encode(raw),
        ):
            with self.subTest(size=len(invalid)), self.assertRaises(BackupError):
                self.cipher.decrypt(invalid, PASSPHRASE)
        target = self.root / "wrong-passphrase"
        with self.assertRaises(BackupError):
            self.service.restore(
                self.archive, target, b"synthetic-wrong-passphrase", confirm=True,
                expected_archive_sha256=result.archive_sha256,
            )
        self.assertFalse(target.exists())

    def test_restore_confirmation_requires_exact_preview_digest(self) -> None:
        self.service.create(self.archive, PASSPHRASE)
        target = self.root / "restored"
        for digest in (None, "", "a" * 64, "A" * 64, True):
            with self.subTest(digest_type=type(digest)), self.assertRaises(BackupError):
                self.service.restore(
                    self.archive, target, PASSPHRASE, confirm=True,
                    expected_archive_sha256=digest,  # type: ignore[arg-type]
                )
        self.assertFalse(target.exists())
        with self.assertRaises(BackupError):
            self.service.restore(self.archive, target, PASSPHRASE, confirm=1)  # type: ignore[arg-type]

    def test_authenticated_incompatible_payload_has_no_restore_side_effects(self) -> None:
        image = self.storage.capture_profile()
        corrupted = image[:60] + (999).to_bytes(4, "big") + image[64:]
        archive = self.cipher.encrypt(corrupted, PASSPHRASE)
        write_private_file(self.archive, archive)
        target = self.root / "restored"
        with self.assertRaises(BackupError):
            self.service.restore(
                self.archive, target, PASSPHRASE, confirm=True,
                expected_archive_sha256=sha256(archive).hexdigest(),
            )
        self.assertFalse(target.exists())
        # Even a key holder cannot switch archive scope or detach the outer salt.
        from cryptography.fernet import Fernet
        salt = b"s" * 16
        header = _MAGIC + salt
        token = Fernet(self.cipher._key(PASSPHRASE, salt)).encrypt(header + b"other_scope\x00" + image)
        with self.assertRaises(BackupError):
            self.cipher.decrypt(header + token, PASSPHRASE)

    def test_broken_foreign_keys_block_export_and_restore_validation(self) -> None:
        with closing(sqlite3.connect(":memory:", isolation_level=None)) as database:
            database.deserialize(self.storage.capture_profile())
            database.execute("DELETE FROM artifacts")
            broken = database.serialize()
        with self.assertRaisesRegex(BackupError, "broken record references"):
            validate_profile_snapshot(broken)
        with patch.object(self.storage, "capture_profile", return_value=broken):
            with self.assertRaises(BackupError):
                self.service.create(self.archive, PASSPHRASE)
        self.assertFalse(self.archive.exists())

    def test_randomized_encryption_does_not_reuse_salt_or_ciphertext(self) -> None:
        image = self.storage.capture_profile()
        one, two = self.cipher.encrypt(image, PASSPHRASE), self.cipher.encrypt(image, PASSPHRASE)
        self.assertNotEqual(one[:len(_MAGIC) + 16], two[:len(_MAGIC) + 16])
        self.assertEqual(self.cipher.decrypt(one, PASSPHRASE), self.cipher.decrypt(two, PASSPHRASE))

    def test_cli_forbids_inline_secrets_abbreviated_confirm_and_unbounded_stdin(self) -> None:
        self.service.create(self.archive, PASSPHRASE)
        target = self.root / "restored"
        args = ("restore", "--archive", str(self.archive), "--target-home", str(target), "--passphrase-stdin")
        for bad in (("--conf",), ("--password", PASSPHRASE.decode()), ("--passphrase", PASSPHRASE.decode())):
            self.invoke(*args, *bad, expected=2)
        for invalid in (
            b"x" * 1025, b"line1\nline2 secret", b"short",
            b"x" * 1024 + b"\r\ntrailing input",
        ):
            self.invoke(*args, passphrase=invalid, expected=2)
        self.assertFalse(target.exists())

    def test_cli_provider_and_io_errors_do_not_disclose_secrets(self) -> None:
        secret_error = "synthetic private exception: " + PASSPHRASE.decode()
        with patch.object(LocalBackupStorage, "capture_profile", side_effect=OSError(secret_error)):
            self.invoke("backup", "--encrypt", str(self.archive), "--passphrase-stdin", expected=2)
        self.assertFalse(self.archive.exists())

    def test_cli_output_failure_can_be_recovered_without_overwriting(self) -> None:
        with patch("grounded_apply.cli._emit", side_effect=OSError("synthetic output failure")):
            with redirect_stderr(io.StringIO()), patch("sys.stdin", io.StringIO(PASSPHRASE.decode())):
                with patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)}):
                    self.assertEqual(main([
                        "backup", "--encrypt", str(self.archive), "--passphrase-stdin", "--json",
                    ]), 2)
        before = self.archive.read_bytes()
        result = self.invoke("backup", "--encrypt", str(self.archive), "--passphrase-stdin")
        self.assertTrue(result["data"]["replayed"])
        self.assertEqual(before, self.archive.read_bytes())

    def test_restore_output_failure_preserves_exact_replay(self) -> None:
        result = self.service.create(self.archive, PASSPHRASE)
        target = self.root / "restored"
        args = [
            "--log-events", "restore", "--archive", str(self.archive),
            "--target-home", str(target), "--archive-sha256", result.archive_sha256,
            "--confirm", "--passphrase-stdin", "--json",
        ]
        events = io.StringIO()
        with (
            patch("grounded_apply.cli._emit", side_effect=OSError("synthetic output failure")),
            redirect_stderr(events), patch("sys.stdin", io.StringIO(PASSPHRASE.decode())),
        ):
            self.assertEqual(main(args), 2)
        final_event = json.loads(events.getvalue().splitlines()[-1])
        self.assertEqual(final_event["outcome"], "backup_outcome_unknown")
        self.assertNotIn(str(target), events.getvalue())
        restored_bytes = (target / "data" / "grounded_apply.db").read_bytes()
        replay = self.service.restore(
            self.archive, target, PASSPHRASE, confirm=True,
            expected_archive_sha256=result.archive_sha256,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual((target / "data" / "grounded_apply.db").read_bytes(), restored_bytes)

    def test_terminal_prompt_fails_closed_instead_of_echoing(self) -> None:
        from grounded_apply.cli import _read_backup_passphrase
        from types import SimpleNamespace
        import getpass

        stream = io.StringIO()
        stream.isatty = lambda: True
        with patch("sys.stdin", stream), patch("getpass.getpass", side_effect=getpass.GetPassWarning):
            with self.assertRaises(getpass.GetPassWarning):
                _read_backup_passphrase(SimpleNamespace(passphrase_stdin=False), repeat=True)
        with patch("sys.stdin", stream), patch("getpass.getpass", side_effect=["synthetic one", "synthetic two"]):
            with self.assertRaisesRegex(BackupError, "do not match"):
                _read_backup_passphrase(SimpleNamespace(passphrase_stdin=False), repeat=True)


if __name__ == "__main__":
    unittest.main()
