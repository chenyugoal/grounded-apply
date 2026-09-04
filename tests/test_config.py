from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from grounded_apply.config import (
    UnsafeRuntimePathError,
    require_initialized_profile_storage,
    require_runtime_outside_repository,
    resolve_runtime_paths,
)


class RuntimePathsTests(unittest.TestCase):
    def test_override_keeps_everything_under_private_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = (Path(directory) / "portable").resolve()
            paths = resolve_runtime_paths({"GROUNDED_APPLY_HOME": str(root)})

            self.assertEqual(paths.config_dir, root / "config")
            self.assertEqual(paths.database, root / "data" / "grounded_apply.db")
            self.assertEqual(paths.cache_dir, root / "cache")
            self.assertEqual(paths.state_dir, root / "state")

    def test_xdg_locations_are_respected(self) -> None:
        paths = resolve_runtime_paths(
            {
                "XDG_CONFIG_HOME": "/private/tmp/example-config",
                "XDG_DATA_HOME": "/private/tmp/example-data",
                "XDG_CACHE_HOME": "/private/tmp/example-cache",
                "XDG_STATE_HOME": "/private/tmp/example-state",
            }
        )

        self.assertEqual(paths.config_dir, Path("/private/tmp/example-config/grounded-apply"))
        self.assertEqual(paths.data_dir, Path("/private/tmp/example-data/grounded-apply"))

    def test_directory_creation_is_private_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            portable_root = Path(directory) / "grounded-apply-home"
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )

            paths.ensure_private_directories()
            paths.ensure_private_directories()

            for path in paths.private_directories():
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o700, path)

            self.assertEqual(stat.S_IMODE(portable_root.stat().st_mode), 0o700)

    def test_existing_insecure_override_is_rejected_without_chmod(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            portable_root = Path(directory) / "existing-root"
            portable_root.mkdir(mode=0o755)
            portable_root.chmod(0o755)
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )

            with self.assertRaisesRegex(UnsafeRuntimePathError, "already be private"):
                paths.ensure_private_directories()

            self.assertEqual(stat.S_IMODE(portable_root.stat().st_mode), 0o755)
            self.assertFalse(paths.data_dir.exists())

    def test_portable_child_symlink_escape_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portable_root = root / "private-runtime"
            portable_root.mkdir(mode=0o700)
            portable_root.chmod(0o700)
            outside_data = root / "outside-data"
            outside_data.mkdir(mode=0o755)
            outside_data.chmod(0o755)
            sentinel = "SYNTHETIC EXTERNAL DATA MUST NOT BE ECHOED"
            (outside_data / "sentinel.txt").write_text(sentinel, encoding="utf-8")
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )
            paths.data_dir.symlink_to(outside_data, target_is_directory=True)

            operations = (
                require_runtime_outside_repository,
                lambda candidate: candidate.ensure_private_directories(),
            )
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(
                        UnsafeRuntimePathError,
                        "child director.*beneath",
                    ) as raised:
                        operation(paths)
                    self.assertNotIn(sentinel, str(raised.exception))

            self.assertTrue(paths.data_dir.is_symlink())
            self.assertEqual(stat.S_IMODE(outside_data.stat().st_mode), 0o755)
            self.assertEqual(
                (outside_data / "sentinel.txt").read_text(encoding="utf-8"),
                sentinel,
            )
            self.assertFalse(paths.config_dir.exists())
            self.assertFalse(paths.cache_dir.exists())
            self.assertFalse(paths.state_dir.exists())

    def test_broad_override_roots_are_rejected(self) -> None:
        for root in (Path("/"), Path.home(), Path(tempfile.gettempdir())):
            with self.subTest(root=root):
                paths = resolve_runtime_paths(
                    {"GROUNDED_APPLY_HOME": str(root.resolve())}
                )
                with self.assertRaisesRegex(
                    UnsafeRuntimePathError, "dedicated application directory"
                ):
                    paths.ensure_private_directories()

    def test_private_data_inside_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory).resolve()
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(repository / ".grounded-apply")}
            )

            with self.assertRaises(UnsafeRuntimePathError):
                require_runtime_outside_repository(paths, repository)

            self.assertFalse(paths.data_dir.exists())

    def test_database_symlink_cannot_escape_private_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portable_root = root / "private-runtime"
            portable_root.mkdir(mode=0o700)
            portable_root.chmod(0o700)
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )
            paths.data_dir.mkdir(mode=0o700)
            outside_database = root / "synthetic-outside.db"
            sentinel = "SYNTHETIC DATABASE CONTENT MUST NOT BE ECHOED"
            outside_database.write_text(sentinel, encoding="utf-8")
            paths.database.symlink_to(outside_database)

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "database.*within.*data directory",
            ) as raised:
                require_runtime_outside_repository(paths)

            self.assertNotIn(sentinel, str(raised.exception))
            self.assertTrue(paths.database.is_symlink())
            self.assertEqual(outside_database.read_text(encoding="utf-8"), sentinel)

    def test_database_symlink_cannot_target_a_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portable_root = root / "private-runtime"
            portable_root.mkdir(mode=0o700)
            portable_root.chmod(0o700)
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )
            paths.data_dir.mkdir(mode=0o700)
            worktree = root / "synthetic-worktree"
            worktree.mkdir()
            (worktree / ".git").mkdir()
            worktree_database = worktree / "candidate-data.db"
            sentinel = "SYNTHETIC WORKTREE DATABASE CONTENT MUST NOT BE ECHOED"
            worktree_database.write_text(sentinel, encoding="utf-8")
            paths.database.symlink_to(worktree_database)

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "database.*Git worktree",
            ) as raised:
                require_runtime_outside_repository(paths)

            self.assertNotIn(sentinel, str(raised.exception))
            self.assertTrue(paths.database.is_symlink())
            self.assertEqual(worktree_database.read_text(encoding="utf-8"), sentinel)

    def test_database_symlink_is_rejected_even_within_private_data_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portable_root = root / "private-runtime"
            portable_root.mkdir(mode=0o700)
            portable_root.chmod(0o700)
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )
            paths.data_dir.mkdir(mode=0o700)
            private_database = paths.data_dir / "private-database-target.db"
            private_database.write_text("synthetic private data", encoding="utf-8")
            private_database.chmod(0o600)
            paths.database.symlink_to(private_database.name)

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "database.*regular non-symlink",
            ):
                require_initialized_profile_storage(paths)

            self.assertTrue(paths.database.is_symlink())
            self.assertEqual(paths.database.resolve(), private_database)
            self.assertEqual(
                private_database.read_text(encoding="utf-8"),
                "synthetic private data",
            )

    def test_nonregular_database_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(root / "private-runtime")}
            )
            paths.ensure_private_directories()
            paths.database.mkdir(mode=0o700)
            sentinel = paths.database / "synthetic-sentinel.txt"
            content = "SYNTHETIC NONREGULAR DATABASE CONTENT"
            sentinel.write_text(content, encoding="utf-8")
            before = paths.database.stat()

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "database.*regular non-symlink",
            ) as raised:
                require_runtime_outside_repository(paths)

            after = paths.database.stat()
            self.assertNotIn(content, str(raised.exception))
            self.assertTrue(paths.database.is_dir())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), content)
            self.assertEqual(after.st_mode, before.st_mode)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)

    def test_database_symlink_cannot_hide_a_multiply_linked_private_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portable_root = root / "private-runtime"
            portable_root.mkdir(mode=0o700)
            portable_root.chmod(0o700)
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )
            paths.data_dir.mkdir(mode=0o700)
            private_database = paths.data_dir / "private-database-target.db"
            content = b"synthetic linked private database"
            private_database.write_bytes(content)
            private_database.chmod(0o600)
            outside_alias = root / "synthetic-profile-alias.db"
            os.link(private_database, outside_alias)
            paths.database.symlink_to(private_database.name)
            before = private_database.stat()

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "database.*regular non-symlink",
            ):
                require_initialized_profile_storage(paths)

            target_after = private_database.stat()
            alias_after = outside_alias.stat()
            self.assertTrue(paths.database.is_symlink())
            self.assertEqual(private_database.read_bytes(), content)
            self.assertEqual(outside_alias.read_bytes(), content)
            self.assertEqual(target_after.st_ino, alias_after.st_ino)
            self.assertEqual(target_after.st_nlink, 2)
            self.assertEqual(target_after.st_mode, before.st_mode)
            self.assertEqual(target_after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(target_after.st_ctime_ns, before.st_ctime_ns)

    def test_initialized_profile_storage_accepts_private_paths_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            portable_root = Path(directory).resolve() / "private-runtime"
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )
            paths.ensure_private_directories()
            content = b"synthetic private database bytes"
            paths.database.write_bytes(content)
            paths.database.chmod(0o600)

            require_initialized_profile_storage(paths)

            self.assertEqual(stat.S_IMODE(paths.data_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(paths.database.stat().st_mode), 0o600)
            self.assertEqual(paths.database.read_bytes(), content)

    def test_initialized_profile_storage_rejects_a_world_readable_database(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            portable_root = Path(directory).resolve() / "private-runtime"
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )
            paths.ensure_private_directories()
            sentinel = "SYNTHETIC PRIVATE DATABASE CONTENT MUST NOT BE ECHOED"
            paths.database.write_text(sentinel, encoding="utf-8")
            paths.database.chmod(0o644)

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "profile database.*private",
            ) as raised:
                require_initialized_profile_storage(paths)

            self.assertNotIn(sentinel, str(raised.exception))
            self.assertEqual(stat.S_IMODE(paths.database.stat().st_mode), 0o644)
            self.assertEqual(paths.database.read_text(encoding="utf-8"), sentinel)

    def test_multiply_linked_database_is_rejected_without_mutating_either_alias(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            portable_root = root / "private-runtime"
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )
            paths.ensure_private_directories()
            content = b"synthetic multiply linked database bytes"
            paths.database.write_bytes(content)
            paths.database.chmod(0o600)
            outside_alias = root / "synthetic-profile-alias.db"
            os.link(paths.database, outside_alias)
            before = paths.database.stat()

            for operation in (
                require_runtime_outside_repository,
                require_initialized_profile_storage,
            ):
                with self.subTest(operation=operation.__name__):
                    with self.assertRaisesRegex(
                        UnsafeRuntimePathError,
                        "profile database.*one hard link",
                    ) as raised:
                        operation(paths)
                    self.assertNotIn(content.decode("utf-8"), str(raised.exception))

            database_after = paths.database.stat()
            alias_after = outside_alias.stat()
            self.assertEqual(paths.database.read_bytes(), content)
            self.assertEqual(outside_alias.read_bytes(), content)
            self.assertEqual(database_after.st_ino, alias_after.st_ino)
            self.assertEqual(database_after.st_nlink, 2)
            self.assertEqual(alias_after.st_nlink, 2)
            self.assertEqual(database_after.st_mode, before.st_mode)
            self.assertEqual(alias_after.st_mode, before.st_mode)
            self.assertEqual(database_after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(alias_after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(database_after.st_ctime_ns, before.st_ctime_ns)
            self.assertEqual(alias_after.st_ctime_ns, before.st_ctime_ns)

    def test_multiply_linked_sqlite_sidecars_are_rejected_without_mutation(
        self,
    ) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = resolve_runtime_paths(
                    {"GROUNDED_APPLY_HOME": str(root / "private-runtime")}
                )
                paths.ensure_private_directories()
                paths.database.write_bytes(b"synthetic private database")
                paths.database.chmod(0o600)
                sidecar = Path(f"{paths.database}{suffix}")
                content = f"synthetic {suffix} bytes".encode("utf-8")
                sidecar.write_bytes(content)
                sidecar.chmod(0o600)
                outside_alias = root / f"synthetic-profile{suffix}"
                os.link(sidecar, outside_alias)
                before = sidecar.stat()

                with self.assertRaisesRegex(
                    UnsafeRuntimePathError,
                    "profile database sidecar.*one hard link",
                ) as raised:
                    require_runtime_outside_repository(paths)

                sidecar_after = sidecar.stat()
                alias_after = outside_alias.stat()
                self.assertNotIn(content.decode("utf-8"), str(raised.exception))
                self.assertEqual(sidecar.read_bytes(), content)
                self.assertEqual(outside_alias.read_bytes(), content)
                self.assertEqual(sidecar_after.st_ino, alias_after.st_ino)
                self.assertEqual(sidecar_after.st_nlink, 2)
                self.assertEqual(sidecar_after.st_mode, before.st_mode)
                self.assertEqual(sidecar_after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(sidecar_after.st_ctime_ns, before.st_ctime_ns)

    def test_unsafe_sqlite_sidecar_types_and_permissions_fail_unchanged(self) -> None:
        cases = ("symlink", "world_readable")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = resolve_runtime_paths(
                    {"GROUNDED_APPLY_HOME": str(root / "private-runtime")}
                )
                paths.ensure_private_directories()
                paths.database.write_bytes(b"synthetic private database")
                paths.database.chmod(0o600)
                sidecar = Path(f"{paths.database}-journal")
                content = b"synthetic rollback journal bytes"
                if case == "symlink":
                    outside = root / "synthetic-external-journal"
                    outside.write_bytes(content)
                    outside.chmod(0o600)
                    sidecar.symlink_to(outside)
                    before = outside.stat()
                    pattern = "regular non-symlink"
                else:
                    sidecar.write_bytes(content)
                    sidecar.chmod(0o644)
                    outside = sidecar
                    before = sidecar.stat()
                    pattern = "sidecar.*private"

                with self.assertRaisesRegex(UnsafeRuntimePathError, pattern):
                    require_runtime_outside_repository(paths)

                after = outside.stat()
                self.assertEqual(outside.read_bytes(), content)
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)
                if case == "symlink":
                    self.assertTrue(sidecar.is_symlink())

    def test_orphan_sqlite_sidecars_are_rejected_without_mutation(self) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = resolve_runtime_paths(
                    {"GROUNDED_APPLY_HOME": str(root / "private-runtime")}
                )
                paths.ensure_private_directories()
                sidecar = Path(f"{paths.database}{suffix}")
                content = f"synthetic orphan {suffix} bytes".encode("utf-8")
                sidecar.write_bytes(content)
                sidecar.chmod(0o600)
                before = sidecar.stat()

                with self.assertRaisesRegex(
                    UnsafeRuntimePathError,
                    "sidecar.*without.*main profile database",
                ):
                    require_runtime_outside_repository(paths)

                after = sidecar.stat()
                self.assertFalse(paths.database.exists())
                self.assertEqual(sidecar.read_bytes(), content)
                self.assertEqual(after.st_nlink, 1)
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)

    def test_private_single_link_sqlite_sidecars_pass_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(root / "private-runtime")}
            )
            paths.ensure_private_directories()
            paths.database.write_bytes(b"synthetic private database")
            paths.database.chmod(0o600)
            expected: dict[Path, tuple[bytes, os.stat_result]] = {}
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(f"{paths.database}{suffix}")
                content = f"synthetic safe {suffix} bytes".encode("utf-8")
                sidecar.write_bytes(content)
                sidecar.chmod(0o600)
                expected[sidecar] = (content, sidecar.stat())

            require_runtime_outside_repository(paths)

            for sidecar, (content, before) in expected.items():
                after = sidecar.stat()
                self.assertEqual(sidecar.read_bytes(), content)
                self.assertEqual(after.st_nlink, 1)
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)

    def test_read_only_profile_access_rejects_every_sqlite_sidecar_unchanged(
        self,
    ) -> None:
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = resolve_runtime_paths(
                    {"GROUNDED_APPLY_HOME": str(root / "private-runtime")}
                )
                paths.ensure_private_directories()
                paths.database.write_bytes(b"synthetic private database")
                paths.database.chmod(0o600)
                sidecar = Path(f"{paths.database}{suffix}")
                content = f"synthetic active {suffix} bytes".encode("utf-8")
                sidecar.write_bytes(content)
                sidecar.chmod(0o600)
                before = sidecar.stat()

                with self.assertRaisesRegex(
                    UnsafeRuntimePathError,
                    "Read-only profile access.*sidecar.*absent",
                ):
                    require_initialized_profile_storage(paths, read_only=True)

                after = sidecar.stat()
                self.assertEqual(sidecar.read_bytes(), content)
                self.assertEqual(after.st_nlink, 1)
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)

    def test_database_symlink_is_rejected_before_target_sidecar_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(root / "private-runtime")}
            )
            paths.ensure_private_directories()
            private_database = paths.data_dir / "private-database-target.db"
            private_database.write_bytes(b"synthetic private database")
            private_database.chmod(0o600)
            paths.database.symlink_to(private_database.name)
            target_sidecar = Path(f"{private_database}-wal")
            content = b"synthetic linked target WAL bytes"
            target_sidecar.write_bytes(content)
            target_sidecar.chmod(0o600)
            outside_alias = root / "synthetic-profile-wal-alias"
            os.link(target_sidecar, outside_alias)
            before = target_sidecar.stat()

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "database.*regular non-symlink",
            ):
                require_runtime_outside_repository(paths)

            sidecar_after = target_sidecar.stat()
            alias_after = outside_alias.stat()
            self.assertTrue(paths.database.is_symlink())
            self.assertEqual(target_sidecar.read_bytes(), content)
            self.assertEqual(outside_alias.read_bytes(), content)
            self.assertEqual(sidecar_after.st_ino, alias_after.st_ino)
            self.assertEqual(sidecar_after.st_nlink, 2)
            self.assertEqual(sidecar_after.st_mode, before.st_mode)
            self.assertEqual(sidecar_after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(sidecar_after.st_ctime_ns, before.st_ctime_ns)

    def test_initialized_profile_storage_rechecks_default_xdg_data_permissions(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = resolve_runtime_paths(
                {
                    "XDG_CONFIG_HOME": str(root / "config"),
                    "XDG_DATA_HOME": str(root / "data"),
                    "XDG_CACHE_HOME": str(root / "cache"),
                    "XDG_STATE_HOME": str(root / "state"),
                }
            )
            paths.data_dir.mkdir(mode=0o700, parents=True)
            paths.data_dir.chmod(0o755)
            paths.database.write_bytes(b"synthetic database")
            paths.database.chmod(0o600)

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "profile data directory.*private",
            ):
                require_initialized_profile_storage(paths)

            self.assertEqual(stat.S_IMODE(paths.data_dir.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(paths.database.stat().st_mode), 0o600)

    def test_initialized_profile_storage_does_not_create_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            portable_root = Path(directory).resolve() / "not-created"
            paths = resolve_runtime_paths(
                {"GROUNDED_APPLY_HOME": str(portable_root)}
            )

            with self.assertRaisesRegex(
                UnsafeRuntimePathError,
                "profile data directory.*accessible",
            ):
                require_initialized_profile_storage(paths)

            self.assertFalse(portable_root.exists())

    def test_any_xdg_runtime_root_inside_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory).resolve()
            paths = resolve_runtime_paths(
                {
                    "XDG_CONFIG_HOME": str(repository / "private-config"),
                    "XDG_DATA_HOME": "/private/tmp/grounded-apply-safe-data",
                    "XDG_CACHE_HOME": "/private/tmp/grounded-apply-safe-cache",
                    "XDG_STATE_HOME": "/private/tmp/grounded-apply-safe-state",
                }
            )

            with self.assertRaisesRegex(UnsafeRuntimePathError, "private-config"):
                require_runtime_outside_repository(paths, repository)

    def test_relative_runtime_roots_are_rejected(self) -> None:
        for environ, setting in (
            ({"GROUNDED_APPLY_HOME": ".grounded-apply"}, "GROUNDED_APPLY_HOME"),
            ({"XDG_DATA_HOME": "relative-data"}, "XDG_DATA_HOME"),
        ):
            with self.subTest(setting=setting):
                with self.assertRaisesRegex(UnsafeRuntimePathError, setting):
                    resolve_runtime_paths(environ)

    def test_blank_xdg_value_uses_the_absolute_default(self) -> None:
        paths = resolve_runtime_paths({"XDG_DATA_HOME": "  "})

        self.assertTrue(paths.data_dir.is_absolute())


if __name__ == "__main__":
    unittest.main()
