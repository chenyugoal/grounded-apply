from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from grounded_apply.config import (
    UnsafeRuntimePathError,
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
