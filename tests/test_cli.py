from __future__ import annotations

import io
import json
import os
import stat
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grounded_apply import __version__
from grounded_apply.cli import main


class CliTests(unittest.TestCase):
    def invoke(self, *arguments: str, home: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {"GROUNDED_APPLY_HOME": home}, clear=False),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_doctor_reports_uninitialized_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "not-created")

            result, stdout, stderr = self.invoke("doctor", "--json", home=home)
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["data"]["checks"]["database"]["initialized"])
            self.assertFalse(Path(home).exists())

    def test_paths_json_has_a_versioned_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, stdout, _ = self.invoke("paths", "--json", home=directory)
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(payload["version"], __version__)
            self.assertTrue(payload["data"]["database"].endswith("grounded_apply.db"))

    def test_profile_init_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "not-created")

            result, stdout, stderr = self.invoke(
                "profile", "init", "--dry-run", "--json", home=home
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertTrue(payload["data"]["dry_run"])
            self.assertFalse(Path(home).exists())

    def test_profile_init_is_private_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "profile-home")

            first_result, first_stdout, first_stderr = self.invoke(
                "profile", "init", "--json", home=home
            )
            second_result, second_stdout, second_stderr = self.invoke(
                "profile", "init", "--json", home=home
            )
            first = json.loads(first_stdout)
            second = json.loads(second_stdout)
            database = Path(str(first["data"]["database"]))
            config = Path(home).resolve() / "config" / "config.toml"

            self.assertEqual((first_result, second_result), (0, 0))
            self.assertEqual((first_stderr, second_stderr), ("", ""))
            self.assertTrue(first["data"]["config_created"])
            self.assertFalse(second["data"]["config_created"])
            self.assertEqual(first["data"]["schema_version"], 1)
            self.assertEqual(second["data"]["schema_version"], 1)
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)

    def test_doctor_rejects_runtime_paths_inside_checkout(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        checkout = Path(__file__).resolve().parents[1]
        environ = {
            "GROUNDED_APPLY_HOME": str(checkout / ".grounded-apply"),
        }
        with (
            patch.dict(os.environ, environ, clear=False),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = main(("doctor", "--json"))
        payload = json.loads(stdout.getvalue())

        self.assertEqual(result, 2)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["data"]["checks"]["runtime_home"]["ok"])
        self.assertFalse((checkout / ".grounded-apply").exists())

    def test_doctor_rejects_a_future_database_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "profile-home"
            database = home / "data" / "grounded_apply.db"
            database.parent.mkdir(parents=True)
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA user_version = 999")
            connection.close()

            result, stdout, _ = self.invoke("doctor", "--json", home=str(home))
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertFalse(payload["ok"])
            self.assertFalse(payload["data"]["checks"]["database"]["ok"])

    def test_doctor_rejects_an_unversioned_unknown_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "profile-home"
            database = home / "data" / "grounded_apply.db"
            database.parent.mkdir(parents=True)
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE unknown_personal_data (value TEXT)")
            connection.commit()
            connection.close()

            result, stdout, _ = self.invoke("doctor", "--json", home=str(home))
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertFalse(payload["ok"])
            self.assertIn("unversioned schema", payload["data"]["checks"]["database"]["error"])

    def test_nested_command_error_keeps_the_stable_json_envelope(self) -> None:
        checkout = Path(__file__).resolve().parents[1]

        result, stdout, stderr = self.invoke(
            "profile", "init", "--json", home=str(checkout / ".grounded-apply")
        )
        payload = json.loads(stdout)

        self.assertEqual(result, 2)
        self.assertEqual(stderr, "")
        self.assertEqual(payload["command"], "profile.init")
        self.assertIsNone(payload["data"])
        self.assertEqual(payload["error"]["type"], "UnsafeRuntimePathError")
        self.assertEqual(payload["warnings"], [])

    def test_doctor_rejects_a_broad_portable_root_without_mutating_it(self) -> None:
        shared_temp = Path(tempfile.gettempdir()).resolve()
        before_mode = stat.S_IMODE(shared_temp.stat().st_mode)

        result, stdout, stderr = self.invoke(
            "doctor", "--json", home=str(shared_temp)
        )
        payload = json.loads(stdout)

        self.assertEqual(result, 2)
        self.assertEqual(stderr, "")
        self.assertFalse(payload["data"]["checks"]["runtime_home"]["ok"])
        self.assertEqual(stat.S_IMODE(shared_temp.stat().st_mode), before_mode)


if __name__ == "__main__":
    unittest.main()
