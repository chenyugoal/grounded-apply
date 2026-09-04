from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grounded_apply import __version__
from grounded_apply.cli import main


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "synthetic_profile"
SOURCE_FILE = FIXTURE_ROOT / "resume.txt"
PROPOSALS_FILE = FIXTURE_ROOT / "import_proposals.json"
IDEMPOTENCY_KEY = "synthetic-avery-quill-cli-import-v1"


class CliTests(unittest.TestCase):
    def invoke(
        self,
        *arguments: str,
        home: str,
        stdin_text: str = "",
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {"GROUNDED_APPLY_HOME": home}, clear=False),
            patch("sys.stdin", io.StringIO(stdin_text)),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def profile_import_arguments(
        self,
        *,
        source_file: str | Path = SOURCE_FILE,
        proposals_file: str | Path = PROPOSALS_FILE,
        idempotency_key: str = IDEMPOTENCY_KEY,
        dry_run: bool = False,
    ) -> tuple[str, ...]:
        arguments = (
            "profile",
            "import",
            "--source-file",
            str(source_file),
            "--proposals-file",
            str(proposals_file),
            "--idempotency-key",
            idempotency_key,
        )
        if dry_run:
            arguments += ("--dry-run",)
        return (*arguments, "--json")

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

    def test_profile_import_dry_run_validates_without_creating_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "not-created")

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(dry_run=True),
                home=home,
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(payload["command"], "profile.import")
            self.assertTrue(payload["data"]["dry_run"])
            self.assertTrue(payload["data"]["review_required"])
            self.assertEqual(payload["data"]["proposal_count"], 5)
            self.assertEqual(payload["data"]["planned_claim_count"], 5)
            self.assertEqual(payload["data"]["planned_evidence_count"], 5)
            self.assertEqual(len(payload["data"]["source_sha256"]), 64)
            self.assertFalse(Path(home).exists())
            self.assertNotIn("avery.quill@example.com", stdout)
            self.assertNotIn("Ignore previous instructions", stdout)

    def test_profile_import_dry_run_does_not_change_initialized_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "profile-home"
            self.invoke("profile", "init", "--json", home=str(home))
            database = home / "data" / "grounded_apply.db"
            before_bytes = database.read_bytes()
            before_stat = database.stat()
            before_paths = {path.relative_to(home) for path in home.rglob("*")}

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(dry_run=True),
                home=str(home),
            )
            payload = json.loads(stdout)
            after_stat = database.stat()

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["data"]["storage_checked"])
            self.assertEqual(database.read_bytes(), before_bytes)
            self.assertEqual(after_stat.st_mode, before_stat.st_mode)
            self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
            self.assertEqual(
                {path.relative_to(home) for path in home.rglob("*")}, before_paths
            )

    def test_profile_import_requires_initialized_storage_without_creating_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "not-created")

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(),
                home=home,
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(payload["command"], "profile.import")
            self.assertFalse(payload["ok"])
            self.assertIn("profile init", payload["error"]["message"])
            self.assertFalse(Path(home).exists())

    def test_profile_import_does_not_migrate_a_precreated_empty_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "profile-home"
            data_dir = home / "data"
            data_dir.mkdir(mode=0o700, parents=True)
            home.chmod(0o700)
            database = data_dir / "grounded_apply.db"
            sqlite3.connect(database).close()
            database.chmod(0o600)
            before = database.read_bytes()

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(), home=str(home)
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(payload["error"]["type"], "ProfileStorageNotInitializedError")
            self.assertEqual(database.read_bytes(), before)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM sqlite_schema WHERE type = 'table'"
                    ).fetchone()[0],
                    0,
                )

    def test_profile_data_commands_reject_insecure_database_without_chmod(self) -> None:
        for command in ("import", "review"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / "profile-home"
                self.invoke("profile", "init", "--json", home=str(home))
                database = home / "data" / "grounded_apply.db"
                database.chmod(0o644)
                before_bytes = database.read_bytes()
                before_stat = database.stat()
                arguments = (
                    self.profile_import_arguments()
                    if command == "import"
                    else ("profile", "review", "--json")
                )

                result, stdout, stderr = self.invoke(*arguments, home=str(home))
                payload = json.loads(stdout)
                after_stat = database.stat()

                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn("private", payload["error"]["message"])
                self.assertNotIn("Avery Quill", stdout)
                self.assertEqual(database.read_bytes(), before_bytes)
                self.assertEqual(stat.S_IMODE(after_stat.st_mode), 0o644)
                self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)

    def test_profile_import_rejects_a_database_symlink_outside_private_data(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "profile-home"
            self.invoke("profile", "init", "--json", home=str(home))
            database = home / "data" / "grounded_apply.db"
            outside_database = root / "outside-profile.db"
            database.replace(outside_database)
            database.symlink_to(outside_database)
            before_bytes = outside_database.read_bytes()
            before_stat = outside_database.stat()

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(),
                home=str(home),
            )
            payload = json.loads(stdout)
            after_stat = outside_database.stat()

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("data directory", payload["error"]["message"])
            self.assertNotIn("Avery Quill", stdout)
            self.assertTrue(database.is_symlink())
            self.assertEqual(outside_database.read_bytes(), before_bytes)
            self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)

    def test_profile_import_does_not_create_or_migrate_a_database_replaced_during_input(
        self,
    ) -> None:
        from grounded_apply import cli as cli_module

        original_read = cli_module._read_utf8_input

        for replacement in ("deleted", "empty"):
            with self.subTest(replacement=replacement):
                with tempfile.TemporaryDirectory() as directory:
                    home = Path(directory) / "profile-home"
                    self.invoke("profile", "init", "--json", home=str(home))
                    database = home / "data" / "grounded_apply.db"
                    input_started = False

                    def read_then_replace(
                        location: str,
                        *,
                        label: str,
                        max_bytes: int,
                    ) -> str:
                        nonlocal input_started
                        text = original_read(location, label=label, max_bytes=max_bytes)
                        if not input_started:
                            input_started = True
                            database.unlink()
                            if replacement == "empty":
                                sqlite3.connect(database).close()
                                database.chmod(0o600)
                        return text

                    with patch.object(
                        cli_module,
                        "_read_utf8_input",
                        side_effect=read_then_replace,
                    ):
                        result, stdout, stderr = self.invoke(
                            *self.profile_import_arguments(),
                            home=str(home),
                        )
                    payload = json.loads(stdout)

                    self.assertTrue(input_started)
                    self.assertEqual(result, 2)
                    self.assertEqual(stderr, "")
                    self.assertEqual(payload["command"], "profile.import")
                    self.assertFalse(payload["ok"])
                    self.assertNotIn("Avery Quill", stdout)
                    self.assertNotIn(IDEMPOTENCY_KEY, stdout)
                    if replacement == "deleted":
                        self.assertFalse(database.exists())
                    else:
                        self.assertEqual(database.read_bytes(), b"")
                        with closing(sqlite3.connect(database)) as connection:
                            self.assertEqual(
                                connection.execute("PRAGMA user_version").fetchone()[0],
                                0,
                            )
                            self.assertEqual(
                                connection.execute(
                                    "SELECT count(*) FROM sqlite_schema "
                                    "WHERE type = 'table'"
                                ).fetchone()[0],
                                0,
                            )

    def test_profile_import_and_review_keep_every_claim_pending(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "profile-home")
            init_result, _, _ = self.invoke(
                "profile", "init", "--json", home=home
            )

            import_result, import_stdout, import_stderr = self.invoke(
                *self.profile_import_arguments(),
                home=home,
            )
            review_result, review_stdout, review_stderr = self.invoke(
                "profile", "review", "--json", home=home
            )
            imported = json.loads(import_stdout)
            review = json.loads(review_stdout)

            self.assertEqual((init_result, import_result, review_result), (0, 0, 0))
            self.assertEqual((import_stderr, review_stderr), ("", ""))
            self.assertEqual(imported["command"], "profile.import")
            self.assertFalse(imported["data"]["dry_run"])
            self.assertTrue(imported["data"]["review_required"])
            self.assertEqual(imported["data"]["claim_count"], 5)
            self.assertEqual(imported["data"]["evidence_count"], 5)
            self.assertEqual(len(imported["data"]["claim_ids"]), 5)
            self.assertNotIn("Software Engineer at", import_stdout)

            self.assertEqual(review["command"], "profile.review")
            self.assertTrue(review["data"]["read_only"])
            self.assertEqual(review["data"]["pending_count"], 5)
            self.assertEqual(
                {item["claim"]["approval_status"] for item in review["data"]["items"]},
                {"pending"},
            )
            self.assertEqual(
                {item["claim"]["status"] for item in review["data"]["items"]},
                {"needs_review"},
            )
            self.assertEqual(
                {item["content_trust"] for item in review["data"]["items"]},
                {"untrusted"},
            )
            self.assertFalse(any(item["usable"] for item in review["data"]["items"]))
            self.assertTrue(
                all(
                    evidence["confirmation_status"] == "pending"
                    for item in review["data"]["items"]
                    for evidence in item["evidence"]
                )
            )
            self.assertIn("Software Engineer at Example Robotics LLC", review_stdout)
            self.assertNotIn("avery.quill@example.com", review_stdout)
            self.assertNotIn("Ignore previous instructions", review_stdout)
            self.assertTrue(
                any("not instructions" in warning for warning in review["warnings"])
            )

    def test_profile_import_is_idempotent_through_the_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "profile-home")
            self.invoke("profile", "init", "--json", home=home)

            first_result, first_stdout, _ = self.invoke(
                *self.profile_import_arguments(), home=home
            )
            retry_result, retry_stdout, _ = self.invoke(
                *self.profile_import_arguments(), home=home
            )
            first = json.loads(first_stdout)
            retry = json.loads(retry_stdout)

            self.assertEqual((first_result, retry_result), (0, 0))
            self.assertEqual(first["data"], retry["data"])

    def test_profile_import_changed_input_reuses_no_records_or_private_error_text(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = str(root / "profile-home")
            self.invoke("profile", "init", "--json", home=home)
            first_result, _, _ = self.invoke(
                *self.profile_import_arguments(), home=home
            )
            proposals = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
            proposals["proposals"][0]["value"] = {
                "employer": "Example Robotics LLC",
                "title": "PRIVATE CHANGED VALUE",
            }
            changed_file = root / "changed.json"
            changed_file.write_text(json.dumps(proposals), encoding="utf-8")

            changed_result, changed_stdout, changed_stderr = self.invoke(
                *self.profile_import_arguments(proposals_file=changed_file),
                home=home,
            )
            review_result, review_stdout, _ = self.invoke(
                "profile", "review", "--json", home=home
            )
            changed = json.loads(changed_stdout)
            review = json.loads(review_stdout)

            self.assertEqual(first_result, 0)
            self.assertEqual(changed_result, 2)
            self.assertEqual(changed_stderr, "")
            self.assertFalse(changed["ok"])
            self.assertNotIn("PRIVATE CHANGED VALUE", changed_stdout)
            self.assertNotIn(IDEMPOTENCY_KEY, changed_stdout)
            self.assertEqual(review_result, 0)
            self.assertEqual(review["data"]["pending_count"], 5)

    def test_profile_import_accepts_source_text_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "profile-home")
            self.invoke("profile", "init", "--json", home=home)
            source_text = SOURCE_FILE.read_text(encoding="utf-8")

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(source_file="-"),
                home=home,
                stdin_text=source_text,
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(payload["data"]["claim_count"], 5)

    def test_profile_import_accepts_proposals_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "profile-home")
            self.invoke("profile", "init", "--json", home=home)
            proposal_text = PROPOSALS_FILE.read_text(encoding="utf-8")

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(proposals_file="-"),
                home=home,
                stdin_text=proposal_text,
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(payload["data"]["claim_count"], 5)

    def test_profile_import_rejects_two_stdin_inputs_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "not-created")

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(
                    source_file="-", proposals_file="-", dry_run=True
                ),
                home=home,
                stdin_text="private text that must not be parsed ambiguously",
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("only one", payload["error"]["message"].lower())
            self.assertFalse(Path(home).exists())
            self.assertNotIn("private text", stdout)

    def test_profile_import_rejects_trust_fields_without_writing_or_echoing_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = str(root / "not-created")
            proposals = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
            proposals["proposals"][0]["status"] = "verified"
            proposals["proposals"][0]["canonical_text"] = "DO NOT ECHO PRIVATE VALUE"
            proposals_file = root / "authority-injection.json"
            proposals_file.write_text(json.dumps(proposals), encoding="utf-8")

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(
                    proposals_file=proposals_file, dry_run=True
                ),
                home=home,
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("unexpected field", payload["error"]["message"])
            self.assertNotIn("DO NOT ECHO PRIVATE VALUE", stdout)
            self.assertFalse(Path(home).exists())

    def test_profile_import_strict_json_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid_text = PROPOSALS_FILE.read_text(encoding="utf-8")
            valid = json.loads(valid_text)
            wrong_schema = {**valid, "schema_version": 2}
            wrong_span_unit = {**valid, "span_unit": "utf8_byte"}
            unknown_root = {**valid, "approval_status": "approved"}
            nested_unknown = json.loads(valid_text)
            nested_unknown["proposals"][0]["scope"] = {"type": "global"}
            inputs = (
                valid_text.replace(
                    '"schema_version": 1,',
                    '"schema_version": 1, "schema_version": 1,',
                    1,
                ),
                valid_text.replace("0.99", "NaN", 1),
                json.dumps(wrong_schema),
                json.dumps(wrong_span_unit),
                json.dumps(unknown_root),
                json.dumps(nested_unknown),
                "[]",
            )

            for index, proposal_text in enumerate(inputs):
                with self.subTest(index=index):
                    proposals_file = root / f"invalid-{index}.json"
                    proposals_file.write_text(proposal_text, encoding="utf-8")
                    home = root / f"not-created-{index}"

                    result, stdout, stderr = self.invoke(
                        *self.profile_import_arguments(
                            proposals_file=proposals_file,
                            dry_run=True,
                        ),
                        home=str(home),
                    )
                    payload = json.loads(stdout)

                    self.assertEqual(result, 2)
                    self.assertEqual(stderr, "")
                    self.assertFalse(payload["ok"])
                    self.assertFalse(home.exists())
                    self.assertNotIn("avery.quill@example.com", stdout)

    def test_profile_import_dry_run_rejects_sensitive_allowed_type_without_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "not-created"
            private_value = "api_token=SYNTHETIC_NOT_A_TOKEN_1234567890"
            proposals = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
            proposals["proposals"][0]["value"]["title"] = private_value
            proposals_file = root / "sensitive-value.json"
            proposals_file.write_text(json.dumps(proposals), encoding="utf-8")

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(
                    proposals_file=proposals_file,
                    dry_run=True,
                ),
                home=str(home),
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("disallowed sensitive", payload["error"]["message"])
            self.assertNotIn(private_value, stdout)
            self.assertFalse(home.exists())

    def test_profile_import_rejects_unsafe_content_without_changing_storage(
        self,
    ) -> None:
        source_text = SOURCE_FILE.read_text(encoding="utf-8")

        for case in ("sensitive-value", "whole-document-evidence"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                home = root / "profile-home"
                proposals = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
                private_value = "password=SYNTHETIC_NOT_A_PASSWORD_123456"
                if case == "sensitive-value":
                    proposals["proposals"][0]["value"]["title"] = private_value
                else:
                    proposals["proposals"][0]["span"] = {
                        "start": 0,
                        "end": len(source_text),
                        "text": source_text,
                    }
                proposals_file = root / f"{case}.json"
                proposals_file.write_text(json.dumps(proposals), encoding="utf-8")
                self.invoke("profile", "init", "--json", home=str(home))
                database = home / "data" / "grounded_apply.db"
                before_bytes = database.read_bytes()
                before_stat = database.stat()
                before_paths = {path.relative_to(home) for path in home.rglob("*")}

                result, stdout, stderr = self.invoke(
                    *self.profile_import_arguments(proposals_file=proposals_file),
                    home=str(home),
                )
                payload = json.loads(stdout)
                after_stat = database.stat()
                review_result, review_stdout, _ = self.invoke(
                    "profile", "review", "--json", home=str(home)
                )

                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertNotIn(private_value, stdout)
                self.assertNotIn("avery.quill@example.com", stdout)
                self.assertNotIn("Ignore previous instructions", stdout)
                self.assertEqual(database.read_bytes(), before_bytes)
                self.assertEqual(after_stat.st_mode, before_stat.st_mode)
                self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
                self.assertEqual(
                    {path.relative_to(home) for path in home.rglob("*")},
                    before_paths,
                )
                self.assertEqual(review_result, 0)
                self.assertEqual(json.loads(review_stdout)["data"]["pending_count"], 0)

    def test_profile_import_preserves_crlf_for_spans_and_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_text = SOURCE_FILE.read_text(encoding="utf-8").replace("\n", "\r\n")
            source_file = root / "resume-crlf.txt"
            source_file.write_bytes(source_text.encode("utf-8"))
            proposals = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
            for item in proposals["proposals"]:
                span_text = item["span"]["text"]
                start = source_text.index(span_text)
                item["span"]["start"] = start
                item["span"]["end"] = start + len(span_text)
            proposals_file = root / "proposals-crlf.json"
            proposals_file.write_text(json.dumps(proposals), encoding="utf-8")
            home = root / "not-created"

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(
                    source_file=source_file,
                    proposals_file=proposals_file,
                    dry_run=True,
                ),
                home=str(home),
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(
                payload["data"]["source_sha256"],
                hashlib.sha256(source_file.read_bytes()).hexdigest(),
            )
            self.assertFalse(home.exists())

    def test_profile_import_rejects_nonregular_and_oversized_inputs_without_reading(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fifo = root / "source.fifo"
            os.mkfifo(fifo)
            oversized = root / "oversized-source.txt"
            with oversized.open("wb") as stream:
                stream.truncate(17 * 1024 * 1024)

            for index, source_file in enumerate((fifo, oversized)):
                with self.subTest(source_file=source_file):
                    home = root / f"not-created-{index}"
                    result, stdout, stderr = self.invoke(
                        *self.profile_import_arguments(
                            source_file=source_file,
                            dry_run=True,
                        ),
                        home=str(home),
                    )
                    payload = json.loads(stdout)

                    self.assertEqual(result, 2)
                    self.assertEqual(stderr, "")
                    self.assertFalse(payload["ok"])
                    self.assertFalse(home.exists())
                    self.assertNotIn(str(source_file), stdout)

    def test_profile_review_requires_initialized_storage_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "not-created")

            result, stdout, stderr = self.invoke(
                "profile", "review", "--json", home=home
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("profile init", payload["error"]["message"])
            self.assertFalse(Path(home).exists())

    def test_profile_review_is_physically_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "profile-home"
            self.invoke("profile", "init", "--json", home=str(home))
            self.invoke(*self.profile_import_arguments(), home=str(home))
            database = home / "data" / "grounded_apply.db"
            before_bytes = database.read_bytes()
            before_stat = database.stat()
            before_paths = {path.relative_to(home) for path in home.rglob("*")}

            result, stdout, stderr = self.invoke(
                "profile", "review", "--json", home=str(home)
            )
            after_stat = database.stat()

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(json.loads(stdout)["data"]["pending_count"], 5)
            self.assertEqual(database.read_bytes(), before_bytes)
            self.assertEqual(after_stat.st_mode, before_stat.st_mode)
            self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
            self.assertEqual(
                {path.relative_to(home) for path in home.rglob("*")}, before_paths
            )

    def test_profile_review_empty_queue_is_a_successful_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = str(Path(directory) / "profile-home")
            self.invoke("profile", "init", "--json", home=home)

            result, stdout, stderr = self.invoke(
                "profile", "review", "--json", home=home
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(payload["data"]["pending_count"], 0)
            self.assertEqual(payload["data"]["items"], [])
            self.assertTrue(payload["data"]["read_only"])

    def test_profile_review_human_output_shows_canonical_and_structured_values(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = str(root / "profile-home")
            proposals = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
            proposals["proposals"][0]["canonical_text"] = "Claims Python"
            proposals["proposals"][0]["value"] = {
                "employer": "Example Robotics LLC",
                "title": "Rust Engineer",
            }
            proposals_file = root / "mismatch.json"
            proposals_file.write_text(json.dumps(proposals), encoding="utf-8")
            self.invoke("profile", "init", "--json", home=home)
            self.invoke(
                *self.profile_import_arguments(proposals_file=proposals_file),
                home=home,
            )

            result, stdout, stderr = self.invoke("profile", "review", home=home)

            self.assertEqual(result, 0)
            self.assertIn("Claims Python", stdout)
            self.assertIn('"title":"Rust Engineer"', stdout)
            self.assertIn("sensitivity=personal", stdout)
            self.assertNotEqual(stderr, "")

    def test_profile_review_escapes_terminal_control_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = str(root / "profile-home")
            proposals = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
            proposals["proposals"][0]["canonical_text"] = "safe\u001b[31mspoof"
            proposals_file = root / "terminal-control.json"
            proposals_file.write_text(json.dumps(proposals), encoding="utf-8")
            self.invoke("profile", "init", "--json", home=home)
            import_result, _, _ = self.invoke(
                *self.profile_import_arguments(proposals_file=proposals_file),
                home=home,
            )

            review_result, review_stdout, review_stderr = self.invoke(
                "profile", "review", home=home
            )

            self.assertEqual((import_result, review_result), (0, 0))
            self.assertNotIn("\u001b", review_stdout)
            self.assertIn("\\u001b", review_stdout)
            self.assertNotIn("\u001b", review_stderr)

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
