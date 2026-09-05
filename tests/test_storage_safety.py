from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from grounded_apply.config import UnsafeRuntimePathError
from grounded_apply.repositories import SQLiteRepository, inspect_schema


class AdapterStorageSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.database = self.root / "profile.db"
        with SQLiteRepository(self.database):
            pass

    def test_all_direct_entry_points_reject_database_aliases_before_sqlite(self) -> None:
        for alias in ("symlink", "hard_link", "directory", "fifo"):
            for mode in ("initialize", "existing", "review", "inspect"):
                with self.subTest(alias=alias, mode=mode), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "unsafe.db"
                    if alias == "symlink":
                        path.symlink_to(self.database)
                    elif alias == "hard_link":
                        path.hardlink_to(self.database)
                    elif alias == "directory":
                        path.mkdir(mode=0o700)
                    else:
                        os.mkfifo(path, 0o600)
                    before = self.database.read_bytes()
                    with patch("grounded_apply.repositories.sqlite.sqlite3.connect") as connect:
                        with self.assertRaises(UnsafeRuntimePathError):
                            self.open(path, mode)
                        connect.assert_not_called()
                    self.assertEqual(self.database.read_bytes(), before)

    def open(self, path: Path, mode: str) -> None:
        if mode == "inspect":
            inspect_schema(path)
        else:
            with SQLiteRepository(path, read_only=mode == "review", existing_only=mode == "existing"):
                pass

    def test_sidecars_and_orphans_are_guarded_for_direct_callers(self) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(f"{self.database}{suffix}")
            for mode in ("initialize", "existing", "review", "inspect"):
                with self.subTest(suffix=suffix, mode=mode):
                    sidecar.symlink_to(self.root / "absent")
                    try:
                        with patch("grounded_apply.repositories.sqlite.sqlite3.connect") as connect:
                            with self.assertRaises(UnsafeRuntimePathError):
                                self.open(self.database, mode)
                            connect.assert_not_called()
                        self.assertTrue(sidecar.is_symlink())
                    finally:
                        sidecar.unlink()
            missing = self.root / "missing.db"
            orphan = Path(f"{missing}{suffix}")
            orphan.write_bytes(b"synthetic recoverable marker")
            orphan.chmod(0o600)
            try:
                for mode in ("initialize", "existing", "review", "inspect"):
                    with self.subTest(orphan=suffix, mode=mode):
                        with self.assertRaises(UnsafeRuntimePathError):
                            self.open(missing, mode)
                        self.assertFalse(missing.exists())
                        self.assertEqual(orphan.read_bytes(), b"synthetic recoverable marker")
            finally:
                orphan.unlink()

    def test_read_only_entry_points_refuse_wal_and_sidecars_without_mutation(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
        before = self.database.read_bytes()
        for mode in ("review", "inspect"):
            with self.subTest(mode=mode), self.assertRaisesRegex(UnsafeRuntimePathError, "WAL"):
                self.open(self.database, mode)
            self.assertEqual(self.database.read_bytes(), before)
            self.assertFalse(Path(f"{self.database}-wal").exists())
            self.assertFalse(Path(f"{self.database}-shm").exists())
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
        sidecar = Path(f"{self.database}-journal")
        sidecar.write_bytes(b"synthetic marker")
        sidecar.chmod(0o600)
        for mode in ("review", "inspect"):
            with self.subTest(mode=mode), self.assertRaisesRegex(UnsafeRuntimePathError, "sidecar"):
                self.open(self.database, mode)
            self.assertEqual(sidecar.read_bytes(), b"synthetic marker")

    def test_existing_access_never_repairs_permissions_but_init_can(self) -> None:
        self.database.chmod(0o640)
        before = self.database.read_bytes()
        for mode in ("existing", "review", "inspect"):
            with self.subTest(mode=mode), self.assertRaises(UnsafeRuntimePathError):
                self.open(self.database, mode)
            self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o640)
            self.assertEqual(self.database.read_bytes(), before)
        self.open(self.database, "initialize")
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)
        self.assertEqual(self.database.read_bytes(), before)

    def test_new_file_is_private_before_sqlite_receives_it(self) -> None:
        path = self.root / "fresh.db"
        connect = sqlite3.connect

        def inspect_open(*args: object, **kwargs: object) -> sqlite3.Connection:
            self.assertTrue(path.is_file())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
            return connect(*args, **kwargs)

        with patch("grounded_apply.repositories.sqlite.sqlite3.connect", side_effect=inspect_open):
            self.open(path, "initialize")

    def test_parent_git_and_uri_shortcuts_fail_closed(self) -> None:
        for target in ("", "file::memory:", "file:/tmp/synthetic.db?mode=rwc"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                SQLiteRepository(target)
        with SQLiteRepository(":memory:") as repository:
            self.assertEqual(repository.list_claims(), [])
        self.root.chmod(0o755)
        try:
            with self.assertRaises(UnsafeRuntimePathError):
                SQLiteRepository(self.database)
        finally:
            self.root.chmod(0o700)
        (self.root / ".git").mkdir()
        for mode in ("initialize", "existing", "review", "inspect"):
            with self.subTest(mode=mode), self.assertRaises(UnsafeRuntimePathError):
                self.open(self.database, mode)

    def test_open_identity_change_closes_connection_before_queries(self) -> None:
        original_connect = sqlite3.connect
        connections: list[sqlite3.Connection] = []

        def replace_after_open(*args: object, **kwargs: object) -> sqlite3.Connection:
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            self.database.rename(self.root / "original.db")
            self.database.write_bytes(b"synthetic replacement")
            self.database.chmod(0o600)
            return connection

        with patch("grounded_apply.repositories.sqlite.sqlite3.connect", side_effect=replace_after_open):
            with self.assertRaisesRegex(UnsafeRuntimePathError, "identity changed"):
                SQLiteRepository(self.database, existing_only=True)
        self.assertEqual(len(connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")
        self.assertEqual(self.database.read_bytes(), b"synthetic replacement")

    def test_context_manager_initialization_failure_closes_its_connection(self) -> None:
        from grounded_apply.repositories import SchemaError

        repository = SQLiteRepository(self.database, existing_only=True)
        with patch.object(repository, "initialize", side_effect=SchemaError("synthetic failure")):
            with self.assertRaises(SchemaError):
                with repository:
                    self.fail("Invalid schema entered a repository context")
        with self.assertRaises(sqlite3.ProgrammingError):
            repository._connection.execute("SELECT 1")

    def test_parent_symlink_dotdot_keeps_filesystem_path_meaning(self) -> None:
        # abspath/normpath would silently select root/profile.db instead of the
        # requested nested/profile.db when a symlink precedes '..'.
        nested = self.root / "nested"
        nested.mkdir(mode=0o700)
        child = nested / "child"
        child.mkdir(mode=0o700)
        link = self.root / "link"
        link.symlink_to(child, target_is_directory=True)
        intended = nested / "profile.db"
        with SQLiteRepository(intended) as repository:
            repository.add_artifact(artifact_type="synthetic", uri="https://example.com/intended")
        spelling = link / ".." / "profile.db"
        with SQLiteRepository(spelling, read_only=True) as repository:
            self.assertEqual(len(repository.list_artifacts()), 1)
        from grounded_apply.repositories import LATEST_SCHEMA_VERSION
        self.assertEqual(inspect_schema(spelling), LATEST_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
