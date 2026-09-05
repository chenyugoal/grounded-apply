from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from grounded_apply import __version__
from grounded_apply.cli import main
from grounded_apply.domain import (
    ApprovalStatus,
    ClaimStatus,
    Sensitivity,
    SourceType,
)
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.services import (
    CreateClaim,
    CreateImportProposal,
    CreateProfileReviewDecision,
    PROFILE_IMPORT_EXTRACTOR_ID,
    ProfileService,
    ProposedImportClaim,
    TextSourceSpan,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "synthetic_profile"
SOURCE_FILE = FIXTURE_ROOT / "resume.txt"
PROPOSALS_FILE = FIXTURE_ROOT / "import_proposals.json"
IDEMPOTENCY_KEY = "synthetic-avery-quill-cli-import-v1"
JSON_ENVELOPE_KEYS = {"command", "data", "error", "ok", "version", "warnings"}
IMPORT_PREVIEW_KEYS = {
    "dry_run",
    "extractor_id",
    "planned_claim_count",
    "planned_evidence_count",
    "proposal_count",
    "review_required",
    "source_artifact_id",
    "source_ref",
    "source_sha256",
    "storage_checked",
}
IMPORT_RESULT_KEYS = {
    "claim_count",
    "claim_ids",
    "dry_run",
    "evidence_count",
    "evidence_ids",
    "extractor_id",
    "review_required",
    "source_artifact_id",
    "source_ref",
    "source_sha256",
    "workflow_run_id",
}
REVIEW_CLAIM_KEYS = {
    "approval_status",
    "canonical_text",
    "claim_type",
    "confidence",
    "created_at",
    "derivation",
    "effective_from",
    "effective_to",
    "evidence_ids",
    "id",
    "scope",
    "sensitivity",
    "source_ref",
    "source_type",
    "status",
    "subject_id",
    "subject_type",
    "supersedes_id",
    "updated_at",
    "value_json",
    "verified_at",
    "verified_by",
}
REVIEW_EVIDENCE_KEYS = {
    "artifact_id",
    "captured_at",
    "checksum",
    "claim_id",
    "confirmation_status",
    "extraction_method",
    "id",
    "locator",
    "source_ref",
    "source_text",
    "source_type",
}
REVIEW_ITEM_KEYS = {
    "claim",
    "content_trust",
    "evidence",
    "import_workflow_run_id",
    "proposal_index",
    "review_token",
    "usable",
}
DECISION_PREVIEW_KEYS = {
    "actor_id",
    "claim_id",
    "decision",
    "decision_recorded",
    "dry_run",
    "requires_confirmation",
    "storage_checked",
}
DECISION_RESULT_KEYS = {
    "actor_id",
    "claim_approval_status",
    "claim_id",
    "claim_status",
    "decided_at",
    "decision",
    "decision_recorded",
    "decision_workflow_run_id",
    "dry_run",
    "evidence_confirmation_status",
    "evidence_id",
    "external_action_taken",
    "import_workflow_run_id",
    "proposal_index",
}
DECISION_CONTRADICTION_KEYS = {
    "conflicting_claim_ids",
    "decision_recorded",
    "detail",
    "external_action_taken",
    "intent",
    "kind",
    "question",
    "scope",
}


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

    def profile_decide_arguments(
        self,
        *,
        claim_id: str,
        review_token: str,
        decision: str = "approve",
        actor_id: str = "synthetic-cli-reviewer",
        idempotency_key: str = "synthetic-cli-review-decision",
        confirm: bool = False,
    ) -> tuple[str, ...]:
        arguments = (
            "profile",
            "decide",
            "--claim-id",
            claim_id,
            "--review-token",
            review_token,
            "--decision",
            decision,
            "--actor-id",
            actor_id,
            "--idempotency-key",
            idempotency_key,
        )
        if confirm:
            arguments += ("--confirm",)
        return (*arguments, "--json")

    def initialize_import_and_review(self, home: Path) -> list[dict[str, object]]:
        init_result, _, _ = self.invoke(
            "profile", "init", "--json", home=str(home)
        )
        import_result, _, _ = self.invoke(
            *self.profile_import_arguments(), home=str(home)
        )
        review_result, review_stdout, _ = self.invoke(
            "profile", "review", "--json", home=str(home)
        )
        self.assertEqual((init_result, import_result, review_result), (0, 0, 0))
        items = json.loads(review_stdout)["data"]["items"]
        self.assertIsInstance(items, list)
        return items

    @staticmethod
    def create_service_import(
        home: Path,
        *,
        sensitivity: Sensitivity = Sensitivity.PERSONAL,
        idempotency_key: str = "synthetic-cli-service-import",
    ):  # type: ignore[no-untyped-def]
        source_text = (
            "Synthetic document header with unrelated material.\n"
            "Synthetic supporting evidence.\n"
            "Synthetic document footer with unrelated material."
        )
        selected_text = "Synthetic supporting evidence."
        start = source_text.index(selected_text)
        request = CreateImportProposal(
            idempotency_key=idempotency_key,
            source_text=source_text,
            expected_source_sha256=hashlib.sha256(
                source_text.encode("utf-8")
            ).hexdigest(),
            proposals=(
                ProposedImportClaim(
                    claim_type="skill_use",
                    value="Python",
                    canonical_text="Used Python on a fictional project",
                    sensitivity=sensitivity,
                    span=TextSourceSpan(
                        start=start,
                        end=start + len(selected_text),
                        text=selected_text,
                    ),
                ),
            ),
        )
        database = home / "data" / "grounded_apply.db"
        with SQLiteRepository(database, existing_only=True) as repository:
            service = ProfileService(repository)
            imported = service.create_import_proposal(request)
            return next(
                item
                for item in service.list_review_items()
                if item.claim.id == imported.claims[0].id
            )

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
            from grounded_apply.repositories import LATEST_SCHEMA_VERSION
            self.assertEqual(first["data"]["schema_version"], LATEST_SCHEMA_VERSION)
            self.assertEqual(second["data"]["schema_version"], LATEST_SCHEMA_VERSION)
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)

    def test_profile_init_rejects_a_dangling_config_symlink_without_writing(
        self,
    ) -> None:
        commands = (
            ("dry_run", ("profile", "init", "--dry-run", "--json")),
            ("initialize", ("profile", "init", "--json")),
        )
        for command, arguments in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                home = root / "profile-home"
                config_dir = home / "config"
                config_dir.mkdir(mode=0o700, parents=True)
                home.chmod(0o700)
                config_dir.chmod(0o700)
                outside_target = root / "synthetic-external-config.toml"
                config = config_dir / "config.toml"
                config.symlink_to(outside_target)
                link_target = os.readlink(config)

                result, stdout, stderr = self.invoke(
                    *arguments,
                    home=str(home),
                )

                payload = json.loads(stdout)
                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn("regular non-symlink", payload["error"]["message"])
                self.assertTrue(config.is_symlink())
                self.assertEqual(os.readlink(config), link_target)
                self.assertFalse(outside_target.exists())
                self.assertFalse((home / "data").exists())
                self.assertFalse((home / "cache").exists())
                self.assertFalse((home / "state").exists())

    def test_profile_init_rejects_other_unsafe_existing_config_files(self) -> None:
        for case in ("hard_link", "world_readable", "directory"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                home = root / "profile-home"
                config_dir = home / "config"
                config_dir.mkdir(mode=0o700, parents=True)
                home.chmod(0o700)
                config_dir.chmod(0o700)
                config = config_dir / "config.toml"
                content = b"synthetic configuration bytes"
                outside_alias: Path | None = None
                if case == "directory":
                    config.mkdir(mode=0o700)
                    expected_error = "regular non-symlink"
                else:
                    config.write_bytes(content)
                    config.chmod(0o600 if case == "hard_link" else 0o644)
                    if case == "hard_link":
                        outside_alias = root / "synthetic-config-alias.toml"
                        os.link(config, outside_alias)
                        expected_error = "one hard link"
                    else:
                        expected_error = "group or other access"
                before = config.stat()

                result, stdout, stderr = self.invoke(
                    "profile",
                    "init",
                    "--json",
                    home=str(home),
                )

                payload = json.loads(stdout)
                after = config.stat()
                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn(expected_error, payload["error"]["message"])
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)
                self.assertFalse((home / "data").exists())
                if case != "directory":
                    self.assertEqual(config.read_bytes(), content)
                if outside_alias is not None:
                    self.assertEqual(outside_alias.read_bytes(), content)
                    self.assertEqual(outside_alias.stat().st_ino, after.st_ino)
                    self.assertEqual(after.st_nlink, 2)

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
            self.assertEqual(set(payload), JSON_ENVELOPE_KEYS)
            self.assertEqual(set(payload["data"]), IMPORT_PREVIEW_KEYS)
            self.assertEqual(payload["command"], "profile.import")
            self.assertTrue(payload["data"]["dry_run"])
            self.assertTrue(payload["data"]["review_required"])
            self.assertEqual(payload["data"]["proposal_count"], 5)
            self.assertEqual(payload["data"]["planned_claim_count"], 5)
            self.assertEqual(payload["data"]["planned_evidence_count"], 5)
            self.assertEqual(len(payload["data"]["source_sha256"]), 64)
            self.assertEqual(
                payload["data"]["source_ref"],
                f"sha256:{payload['data']['source_sha256']}",
            )
            self.assertEqual(
                payload["data"]["source_artifact_id"],
                f"profile-import-source:sha256:{payload['data']['source_sha256']}",
            )
            self.assertEqual(
                payload["data"]["extractor_id"], PROFILE_IMPORT_EXTRACTOR_ID
            )
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

    def test_profile_commands_reject_a_multiply_linked_database_without_mutation(
        self,
    ) -> None:
        synthetic_claim_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-hard-link-review-item")
        )
        commands = (
            ("init", ("profile", "init", "--json")),
            ("import", self.profile_import_arguments()),
            ("review", ("profile", "review", "--json")),
            (
                "decide",
                self.profile_decide_arguments(
                    claim_id=synthetic_claim_id,
                    review_token="a" * 64,
                    confirm=True,
                ),
            ),
        )
        for command, arguments in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                home = root / "profile-home"
                init_result, _, _ = self.invoke(
                    "profile", "init", "--json", home=str(home)
                )
                self.assertEqual(init_result, 0)
                if command == "review":
                    import_result, _, _ = self.invoke(
                        *self.profile_import_arguments(), home=str(home)
                    )
                    self.assertEqual(import_result, 0)
                database = home / "data" / "grounded_apply.db"
                outside_alias = root / "synthetic-profile-alias.db"
                os.link(database, outside_alias)
                before_bytes = database.read_bytes()
                before_stat = database.stat()

                result, stdout, stderr = self.invoke(*arguments, home=str(home))
                payload = json.loads(stdout)
                database_after = database.stat()
                alias_after = outside_alias.stat()

                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn("one hard link", payload["error"]["message"])
                self.assertNotIn("Avery Quill", stdout)
                self.assertEqual(database.read_bytes(), before_bytes)
                self.assertEqual(outside_alias.read_bytes(), before_bytes)
                self.assertEqual(database_after.st_ino, alias_after.st_ino)
                self.assertEqual(database_after.st_nlink, 2)
                self.assertEqual(alias_after.st_nlink, 2)
                self.assertEqual(database_after.st_mode, before_stat.st_mode)
                self.assertEqual(alias_after.st_mode, before_stat.st_mode)
                self.assertEqual(database_after.st_mtime_ns, before_stat.st_mtime_ns)
                self.assertEqual(alias_after.st_mtime_ns, before_stat.st_mtime_ns)
                self.assertEqual(database_after.st_ctime_ns, before_stat.st_ctime_ns)
                self.assertEqual(alias_after.st_ctime_ns, before_stat.st_ctime_ns)

    def test_profile_init_rechecks_a_hard_link_created_before_database_open(
        self,
    ) -> None:
        from grounded_apply import cli as cli_module

        original_default_config = cli_module._ensure_default_config
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "profile-home"
            outside_alias = root / "synthetic-profile-alias.db"
            content = b"synthetic external database bytes"
            outside_alias.write_bytes(content)
            outside_alias.chmod(0o640)
            linked_stat: os.stat_result | None = None

            def configure_then_link(paths: object) -> bool:
                nonlocal linked_stat
                created = original_default_config(paths)  # type: ignore[arg-type]
                database = home / "data" / "grounded_apply.db"
                os.link(outside_alias, database)
                linked_stat = outside_alias.stat()
                return created

            with patch.object(
                cli_module,
                "_ensure_default_config",
                side_effect=configure_then_link,
            ):
                result, stdout, stderr = self.invoke(
                    "profile", "init", "--json", home=str(home)
                )

            self.assertIsNotNone(linked_stat)
            assert linked_stat is not None
            database = home / "data" / "grounded_apply.db"
            database_after = database.stat()
            alias_after = outside_alias.stat()
            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertIn("one hard link", json.loads(stdout)["error"]["message"])
            self.assertEqual(database.read_bytes(), content)
            self.assertEqual(outside_alias.read_bytes(), content)
            self.assertEqual(database_after.st_ino, alias_after.st_ino)
            self.assertEqual(database_after.st_nlink, 2)
            self.assertEqual(database_after.st_mode, linked_stat.st_mode)
            self.assertEqual(database_after.st_mtime_ns, linked_stat.st_mtime_ns)
            self.assertEqual(database_after.st_ctime_ns, linked_stat.st_ctime_ns)

    def test_profile_import_rechecks_for_a_hard_link_created_during_input(
        self,
    ) -> None:
        from grounded_apply import cli as cli_module

        original_read = cli_module._read_utf8_input
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "profile-home"
            init_result, _, _ = self.invoke(
                "profile", "init", "--json", home=str(home)
            )
            self.assertEqual(init_result, 0)
            database = home / "data" / "grounded_apply.db"
            outside_alias = root / "synthetic-profile-alias.db"
            before_bytes = database.read_bytes()
            linked = False
            linked_stat: os.stat_result | None = None

            def read_then_link(
                location: str,
                *,
                label: str,
                max_bytes: int,
            ) -> str:
                nonlocal linked, linked_stat
                text = original_read(location, label=label, max_bytes=max_bytes)
                if not linked:
                    linked = True
                    os.link(database, outside_alias)
                    linked_stat = database.stat()
                return text

            with patch.object(
                cli_module,
                "_read_utf8_input",
                side_effect=read_then_link,
            ):
                result, stdout, stderr = self.invoke(
                    *self.profile_import_arguments(),
                    home=str(home),
                )

            self.assertTrue(linked)
            self.assertIsNotNone(linked_stat)
            assert linked_stat is not None
            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertIn("one hard link", json.loads(stdout)["error"]["message"])
            database_after = database.stat()
            alias_after = outside_alias.stat()
            self.assertEqual(database.read_bytes(), before_bytes)
            self.assertEqual(outside_alias.read_bytes(), before_bytes)
            self.assertEqual(database_after.st_nlink, 2)
            self.assertEqual(alias_after.st_nlink, 2)
            self.assertEqual(database_after.st_mode, linked_stat.st_mode)
            self.assertEqual(database_after.st_mtime_ns, linked_stat.st_mtime_ns)
            self.assertEqual(database_after.st_ctime_ns, linked_stat.st_ctime_ns)
            self.assertFalse(Path(f"{database}-journal").exists())
            self.assertFalse(Path(f"{database}-wal").exists())
            self.assertFalse(Path(f"{database}-shm").exists())

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
            self.assertEqual(set(imported), JSON_ENVELOPE_KEYS)
            self.assertEqual(set(imported["data"]), IMPORT_RESULT_KEYS)
            self.assertEqual(imported["command"], "profile.import")
            self.assertFalse(imported["data"]["dry_run"])
            self.assertTrue(imported["data"]["review_required"])
            self.assertEqual(imported["data"]["claim_count"], 5)
            self.assertEqual(imported["data"]["evidence_count"], 5)
            self.assertEqual(len(imported["data"]["claim_ids"]), 5)
            self.assertNotIn("Software Engineer at", import_stdout)

            self.assertEqual(review["command"], "profile.review")
            self.assertEqual(set(review), JSON_ENVELOPE_KEYS)
            self.assertEqual(
                set(review["data"]), {"items", "pending_count", "read_only"}
            )
            self.assertTrue(review["data"]["read_only"])
            self.assertEqual(review["data"]["pending_count"], 5)
            workflow_run_id = imported["data"]["workflow_run_id"]
            parsed_workflow_run_id = uuid.UUID(workflow_run_id)
            self.assertEqual(str(parsed_workflow_run_id), workflow_run_id)
            self.assertEqual(parsed_workflow_run_id.version, 4)
            proposal_indexes: list[int] = []
            review_tokens: list[str] = []
            for item in review["data"]["items"]:
                self.assertEqual(set(item), REVIEW_ITEM_KEYS)
                self.assertEqual(set(item["claim"]), REVIEW_CLAIM_KEYS)
                self.assertEqual(len(item["evidence"]), 1)
                self.assertEqual(set(item["evidence"][0]), REVIEW_EVIDENCE_KEYS)
                self.assertEqual(item["import_workflow_run_id"], workflow_run_id)
                self.assertIs(type(item["proposal_index"]), int)
                self.assertGreaterEqual(item["proposal_index"], 0)
                self.assertRegex(item["review_token"], r"\A[0-9a-f]{64}\Z")
                proposal_indexes.append(item["proposal_index"])
                review_tokens.append(item["review_token"])
                self.assertEqual(
                    item["claim"]["id"],
                    imported["data"]["claim_ids"][item["proposal_index"]],
                )
                self.assertEqual(
                    item["evidence"][0]["id"],
                    imported["data"]["evidence_ids"][item["proposal_index"]],
                )
            self.assertEqual(proposal_indexes, list(range(5)))
            self.assertEqual(len(set(review_tokens)), 5)
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
            expected_source_ref = f"sha256:{imported['data']['source_sha256']}"
            self.assertEqual(imported["data"]["source_ref"], expected_source_ref)
            self.assertEqual(
                imported["data"]["extractor_id"], PROFILE_IMPORT_EXTRACTOR_ID
            )
            self.assertEqual(
                {
                    item["claim"]["source_ref"]
                    for item in review["data"]["items"]
                },
                {expected_source_ref},
            )
            self.assertEqual(
                {
                    evidence["extraction_method"]
                    for item in review["data"]["items"]
                    for evidence in item["evidence"]
                },
                {PROFILE_IMPORT_EXTRACTOR_ID},
            )
            self.assertIn("Software Engineer at Example Robotics LLC", review_stdout)
            self.assertNotIn("avery.quill@example.com", review_stdout)
            self.assertNotIn("Ignore previous instructions", review_stdout)
            self.assertTrue(
                any("not instructions" in warning for warning in review["warnings"])
            )
            self.assertTrue(
                any(
                    "current CLI review command is read-only" in warning
                    for warning in review["warnings"]
                )
            )

    def test_profile_import_replay_warning_describes_only_pending_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "profile-home"
            self.invoke("profile", "init", "--json", home=str(home))
            self.invoke(*self.profile_import_arguments(), home=str(home))
            database = home / "data" / "grounded_apply.db"

            with SQLiteRepository(database, existing_only=True) as repository:
                service = ProfileService(repository)
                item = service.list_review_items()[0]
                service.decide_review_item(
                    CreateProfileReviewDecision(
                        claim_id=item.claim.id,
                        review_token=item.review_token,
                        decision=ApprovalStatus.APPROVED,
                        actor_id="synthetic-cli-reviewer",
                        idempotency_key="synthetic-cli-review-decision-1",
                    ),
                    now=item.claim.created_at,
                )

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(), home=str(home)
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertTrue(payload["data"]["review_required"])
            self.assertEqual(len(payload["warnings"]), 1)
            self.assertIn("Pending imported facts remain", payload["warnings"][0])
            self.assertNotIn("Imported facts remain", payload["warnings"][0])
            review_result, review_stdout, review_stderr = self.invoke(
                "profile", "review", "--json", home=str(home)
            )
            self.assertEqual(review_result, 0)
            self.assertEqual(review_stderr, "")
            self.assertEqual(json.loads(review_stdout)["data"]["pending_count"], 4)

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

    def test_profile_import_replay_uses_manifest_semantics_not_json_serialization(
        self,
    ) -> None:
        def reverse_object_fields(value: object) -> object:
            if isinstance(value, dict):
                return {
                    key: reverse_object_fields(value[key])
                    for key in reversed(tuple(value))
                }
            if isinstance(value, list):
                return [reverse_object_fields(item) for item in value]
            return value

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = str(root / "profile-home")
            reserialized_path = root / "SYNTHETIC_PRIVATE_REORDERED_MANIFEST.json"
            manifest = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
            reserialized_path.write_text(
                json.dumps(
                    reverse_object_fields(manifest),
                    ensure_ascii=False,
                    indent=3,
                ),
                encoding="utf-8",
            )
            self.invoke("profile", "init", "--json", home=home)

            first_result, first_stdout, first_stderr = self.invoke(
                *self.profile_import_arguments(), home=home
            )
            retry_result, retry_stdout, retry_stderr = self.invoke(
                *self.profile_import_arguments(proposals_file=reserialized_path),
                home=home,
            )

            self.assertEqual((first_result, retry_result), (0, 0))
            self.assertEqual((first_stderr, retry_stderr), ("", ""))
            self.assertEqual(
                json.loads(first_stdout)["data"],
                json.loads(retry_stdout)["data"],
            )
            self.assertNotIn(
                str(reserialized_path),
                first_stdout + retry_stdout,
            )

    def test_profile_import_replay_is_independent_of_private_source_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = str(root / "profile-home")
            first_source = root / "SYNTHETIC_PRIVATE_PERSON" / "resume-one.txt"
            second_source = root / "different-private-folder" / "resume-two.txt"
            first_proposals = first_source.parent / "proposal-one.json"
            second_proposals = second_source.parent / "proposal-two.json"
            first_source.parent.mkdir()
            second_source.parent.mkdir()
            source_bytes = SOURCE_FILE.read_bytes()
            first_source.write_bytes(source_bytes)
            second_source.write_bytes(source_bytes)
            proposal_bytes = PROPOSALS_FILE.read_bytes()
            first_proposals.write_bytes(proposal_bytes)
            second_proposals.write_bytes(proposal_bytes)
            self.invoke("profile", "init", "--json", home=home)

            first_result, first_stdout, first_stderr = self.invoke(
                *self.profile_import_arguments(
                    source_file=first_source,
                    proposals_file=first_proposals,
                ),
                home=home,
            )
            retry_result, retry_stdout, retry_stderr = self.invoke(
                *self.profile_import_arguments(
                    source_file=second_source,
                    proposals_file=second_proposals,
                ),
                home=home,
            )
            review_result, review_stdout, review_stderr = self.invoke(
                "profile", "review", "--json", home=home
            )

            self.assertEqual((first_result, retry_result, review_result), (0, 0, 0))
            self.assertEqual((first_stderr, retry_stderr, review_stderr), ("", "", ""))
            self.assertEqual(json.loads(first_stdout)["data"], json.loads(retry_stdout)["data"])
            all_output = first_stdout + retry_stdout + review_stdout
            self.assertNotIn(str(first_source), all_output)
            self.assertNotIn(str(second_source), all_output)
            self.assertNotIn(str(first_proposals), all_output)
            self.assertNotIn(str(second_proposals), all_output)
            self.assertNotIn("SYNTHETIC_PRIVATE_PERSON", all_output)

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

    def test_profile_import_changed_source_conflicts_without_new_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "profile-home"
            self.invoke("profile", "init", "--json", home=str(home))
            first_result, _, _ = self.invoke(
                *self.profile_import_arguments(), home=str(home)
            )
            changed_source = root / "SYNTHETIC_PRIVATE_CHANGED_RESUME.txt"
            changed_bytes = SOURCE_FILE.read_bytes() + b"\nPRIVATE SYNTHETIC SUFFIX\n"
            changed_source.write_bytes(changed_bytes)
            changed_manifest = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
            changed_manifest["source_sha256"] = hashlib.sha256(changed_bytes).hexdigest()
            changed_proposals = root / "changed-proposals.json"
            changed_proposals.write_text(json.dumps(changed_manifest), encoding="utf-8")

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(
                    source_file=changed_source,
                    proposals_file=changed_proposals,
                ),
                home=str(home),
            )
            payload = json.loads(stdout)
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                artifact_count = len(repository.list_artifacts())
                claim_count = len(repository.list_claims())
                workflow_count = len(repository.list_workflow_runs())

            self.assertEqual(first_result, 0)
            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("idempotency key", payload["error"]["message"])
            self.assertNotIn("PRIVATE SYNTHETIC SUFFIX", stdout)
            self.assertNotIn(str(changed_source), stdout)
            self.assertEqual((artifact_count, claim_count, workflow_count), (1, 5, 1))

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

    def test_profile_import_rejects_authority_and_provenance_fields_without_writing(
        self,
    ) -> None:
        injected_fields = {
            "status": "verified",
            "source_ref": "file:///Users/SYNTHETIC_PRIVATE_PERSON/resume.txt",
            "extraction_method": "trusted-looking@999",
            "extractor_id": PROFILE_IMPORT_EXTRACTOR_ID,
            "artifact_id": "forged-artifact",
        }
        for index, (field, value) in enumerate(injected_fields.items()):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                home = root / "not-created"
                proposals = json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
                if field == "status":
                    proposals["proposals"][0][field] = value
                    proposals["proposals"][0]["canonical_text"] = (
                        "DO NOT ECHO PRIVATE VALUE"
                    )
                else:
                    proposals[field] = value
                proposals_file = root / f"authority-injection-{index}.json"
                proposals_file.write_text(json.dumps(proposals), encoding="utf-8")

                result, stdout, stderr = self.invoke(
                    *self.profile_import_arguments(
                        proposals_file=proposals_file, dry_run=True
                    ),
                    home=str(home),
                )
                payload = json.loads(stdout)

                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn("unexpected field", payload["error"]["message"])
                self.assertNotIn(str(value), stdout)
                self.assertNotIn("DO NOT ECHO PRIVATE VALUE", stdout)
                self.assertFalse(home.exists())

    def test_profile_import_strict_json_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid_text = PROPOSALS_FILE.read_text(encoding="utf-8")
            valid = json.loads(valid_text)
            wrong_schema = {**valid, "schema_version": 1}
            wrong_span_unit = {**valid, "span_unit": "utf8_byte"}
            unknown_root = {**valid, "approval_status": "approved"}
            uppercase_digest = {
                **valid,
                "source_sha256": str(valid["source_sha256"]).upper(),
            }
            short_digest = {**valid, "source_sha256": "0" * 63}
            nonhex_digest = {**valid, "source_sha256": "g" * 64}
            wrong_digest_type = {**valid, "source_sha256": 7}
            nested_unknown = json.loads(valid_text)
            nested_unknown["proposals"][0]["scope"] = {"type": "global"}
            inputs = (
                valid_text.replace(
                    '"schema_version": 2,',
                    '"schema_version": 2, "schema_version": 2,',
                    1,
                ),
                valid_text.replace("0.99", "NaN", 1),
                json.dumps(wrong_schema),
                json.dumps(wrong_span_unit),
                json.dumps(unknown_root),
                json.dumps(uppercase_digest),
                json.dumps(short_digest),
                json.dumps(nonhex_digest),
                json.dumps(wrong_digest_type),
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
            proposals["source_sha256"] = hashlib.sha256(
                source_file.read_bytes()
            ).hexdigest()
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

    def test_profile_import_rejects_a_source_that_does_not_match_manifest_digest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_file = root / "changed-source.txt"
            source_file.write_bytes(SOURCE_FILE.read_bytes() + b"\nSynthetic suffix.\n")
            home = root / "not-created"

            result, stdout, stderr = self.invoke(
                *self.profile_import_arguments(source_file=source_file, dry_run=True),
                home=str(home),
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("source digest", payload["error"]["message"].lower())
            self.assertNotIn("Synthetic suffix", stdout)
            self.assertNotIn(str(source_file), stdout)
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

    def test_profile_import_rejects_terminal_input_symlinks_without_reading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_link = root / "private-source-link.txt"
            proposal_link = root / "private-proposal-link.json"
            source_link.symlink_to(SOURCE_FILE)
            proposal_link.symlink_to(PROPOSALS_FILE)

            for index, (source_file, proposals_file) in enumerate(
                (
                    (source_link, PROPOSALS_FILE),
                    (SOURCE_FILE, proposal_link),
                )
            ):
                with self.subTest(index=index):
                    home = root / f"not-created-symlink-{index}"
                    result, stdout, stderr = self.invoke(
                        *self.profile_import_arguments(
                            source_file=source_file,
                            proposals_file=proposals_file,
                            dry_run=True,
                        ),
                        home=str(home),
                    )
                    payload = json.loads(stdout)

                    self.assertEqual(result, 2)
                    self.assertEqual(stderr, "")
                    self.assertFalse(payload["ok"])
                    self.assertIn("regular file", payload["error"]["message"])
                    self.assertNotIn(str(source_file), stdout)
                    self.assertNotIn(str(proposals_file), stdout)
                    self.assertFalse(home.exists())

    def test_profile_import_rejects_a_file_changed_during_capture(self) -> None:
        from grounded_apply import cli as cli_module

        original_fstat = cli_module.os.fstat
        calls = 0

        def changed_final_stat(descriptor: int) -> os.stat_result | SimpleNamespace:
            nonlocal calls
            calls += 1
            result = original_fstat(descriptor)
            if calls != 2:
                return result
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_dev=result.st_dev,
                st_ino=result.st_ino,
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns + 1,
                st_ctime_ns=result.st_ctime_ns,
            )

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "not-created"
            with patch.object(cli_module.os, "fstat", side_effect=changed_final_stat):
                result, stdout, stderr = self.invoke(
                    *self.profile_import_arguments(dry_run=True),
                    home=str(home),
                )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("changed while", payload["error"]["message"])
            self.assertNotIn(str(SOURCE_FILE), stdout)
            self.assertFalse(home.exists())

    def test_profile_import_fails_closed_when_secure_open_flags_are_unavailable(
        self,
    ) -> None:
        from grounded_apply import cli as cli_module

        for flag_name in ("O_NOFOLLOW", "O_NONBLOCK"):
            with (
                self.subTest(flag_name=flag_name),
                tempfile.TemporaryDirectory() as directory,
            ):
                home = Path(directory) / "not-created"
                with (
                    patch.object(cli_module.os, flag_name, 0, create=True),
                    patch.object(
                        cli_module.os,
                        "open",
                        wraps=cli_module.os.open,
                    ) as open_file,
                ):
                    result, stdout, stderr = self.invoke(
                        *self.profile_import_arguments(dry_run=True),
                        home=str(home),
                    )
                payload = json.loads(stdout)

                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                open_file.assert_not_called()
                self.assertNotIn(str(SOURCE_FILE), stdout)
                self.assertFalse(home.exists())

    def test_profile_import_rejects_lstat_open_identity_mismatch_before_read(
        self,
    ) -> None:
        from grounded_apply import cli as cli_module

        original_fstat = cli_module.os.fstat

        def changed_opened_stat(descriptor: int) -> SimpleNamespace:
            result = original_fstat(descriptor)
            return SimpleNamespace(
                st_mode=result.st_mode,
                st_dev=result.st_dev,
                st_ino=result.st_ino + 1,
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns,
                st_ctime_ns=result.st_ctime_ns,
            )

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "not-created"
            with (
                patch.object(
                    cli_module.os,
                    "fstat",
                    side_effect=changed_opened_stat,
                ),
                patch.object(
                    cli_module.os,
                    "read",
                    wraps=cli_module.os.read,
                ) as read_file,
            ):
                result, stdout, stderr = self.invoke(
                    *self.profile_import_arguments(dry_run=True),
                    home=str(home),
                )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("changed before", payload["error"]["message"])
            read_file.assert_not_called()
            self.assertNotIn(str(SOURCE_FILE), stdout)
            self.assertFalse(home.exists())

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

    def test_profile_review_never_discloses_legacy_path_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "profile-home"
            self.invoke("profile", "init", "--json", home=str(home))
            private_source_ref = (
                "file:///Users/SYNTHETIC_PRIVATE_PERSON/Documents/resume.txt"
            )
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                existing_only=True,
            ) as repository:
                claim = repository.add_claim(
                    claim_type="skill_use",
                    value="Python",
                    canonical_text="Used Python in a fictional project",
                    source_type=SourceType.IMPORTED_RESUME.value,
                    source_ref=private_source_ref,
                )
                repository.add_evidence(
                    claim_id=str(claim["id"]),
                    source_type=SourceType.IMPORTED_RESUME.value,
                    source_ref=private_source_ref,
                    source_text="Synthetic supporting evidence",
                    extraction_method="caller-claimed@999",
                )

            for json_output in (False, True):
                with self.subTest(json_output=json_output):
                    arguments = ("profile", "review")
                    if json_output:
                        arguments += ("--json",)
                    result, stdout, stderr = self.invoke(
                        *arguments,
                        home=str(home),
                    )

                    self.assertEqual(result, 2)
                    self.assertNotIn(private_source_ref, stdout + stderr)
                    self.assertNotIn("SYNTHETIC_PRIVATE_PERSON", stdout + stderr)
                    self.assertIn(
                        "Profile import review provenance failed integrity checks",
                        stdout + stderr,
                    )
                    if json_output:
                        payload = json.loads(stdout)
                        self.assertEqual(payload["error"]["type"], "RepositoryError")
                        self.assertEqual(
                            payload["error"]["message"],
                            "Profile import review provenance failed integrity checks",
                        )

    def test_profile_review_does_not_echo_a_corrupt_stored_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "profile-home"
            self.invoke("profile", "init", "--json", home=str(home))
            self.invoke(*self.profile_import_arguments(), home=str(home))
            private_marker = (
                "file:///Users/SYNTHETIC_PRIVATE_PERSON/private-resume.txt"
            )
            with closing(
                sqlite3.connect(home / "data" / "grounded_apply.db")
            ) as connection:
                connection.execute(
                    "UPDATE evidence SET captured_at = ?",
                    (private_marker,),
                )
                connection.commit()

            for json_output in (False, True):
                with self.subTest(json_output=json_output):
                    arguments = ("profile", "review")
                    if json_output:
                        arguments += ("--json",)
                    result, stdout, stderr = self.invoke(
                        *arguments,
                        home=str(home),
                    )

                    self.assertEqual(result, 2)
                    self.assertNotIn(private_marker, stdout + stderr)
                    self.assertNotIn("SYNTHETIC_PRIVATE_PERSON", stdout + stderr)
                    self.assertIn(
                        "Profile import review provenance failed integrity checks",
                        stdout + stderr,
                    )
                    if json_output:
                        payload = json.loads(stdout)
                        self.assertEqual(payload["error"]["type"], "RepositoryError")
                        self.assertEqual(
                            payload["error"]["message"],
                            "Profile import review provenance failed integrity checks",
                        )

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

    def test_profile_decide_preview_is_storage_free_and_confirmable(self) -> None:
        from grounded_apply import cli as cli_module

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)
            database = home / "data" / "grounded_apply.db"
            before_bytes = database.read_bytes()
            before_stat = database.stat()
            before_paths = {path.relative_to(home) for path in home.rglob("*")}
            arguments = self.profile_decide_arguments(
                claim_id=str(claim["id"]),
                review_token=str(item["review_token"]),
            )

            with patch.object(
                cli_module,
                "resolve_runtime_paths",
                side_effect=AssertionError("decision preview opened storage"),
            ) as resolve_paths:
                result, stdout, stderr = self.invoke(*arguments, home=str(home))

            payload = json.loads(stdout)
            after_stat = database.stat()
            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(set(payload), JSON_ENVELOPE_KEYS)
            self.assertEqual(set(payload["data"]), DECISION_PREVIEW_KEYS)
            self.assertEqual(payload["command"], "profile.decide")
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["data"]["dry_run"])
            self.assertFalse(payload["data"]["storage_checked"])
            self.assertFalse(payload["data"]["decision_recorded"])
            self.assertTrue(payload["data"]["requires_confirmation"])
            self.assertEqual(payload["data"]["decision"], "approved")
            self.assertNotIn(str(item["review_token"]), stdout)
            self.assertNotIn("synthetic-cli-review-decision", stdout)
            resolve_paths.assert_not_called()
            self.assertEqual(database.read_bytes(), before_bytes)
            self.assertEqual(after_stat.st_mode, before_stat.st_mode)
            self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
            self.assertEqual(after_stat.st_ctime_ns, before_stat.st_ctime_ns)
            self.assertEqual(
                {path.relative_to(home) for path in home.rglob("*")},
                before_paths,
            )

            human_result, human_stdout, human_stderr = self.invoke(
                *arguments[:-1],
                home=str(home),
            )
            self.assertEqual(human_result, 0)
            self.assertEqual(human_stderr, "")
            self.assertIn("no decision or external action was recorded", human_stdout)
            self.assertNotIn(str(item["review_token"]), human_stdout)
            self.assertNotIn("synthetic-cli-review-decision", human_stdout)

            confirmed_result, confirmed_stdout, confirmed_stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=str(claim["id"]),
                    review_token=str(item["review_token"]),
                    confirm=True,
                ),
                home=str(home),
            )
            self.assertEqual(confirmed_result, 0)
            self.assertEqual(confirmed_stderr, "")
            self.assertTrue(json.loads(confirmed_stdout)["data"]["decision_recorded"])

    def test_profile_decide_approval_is_minimized_and_exactly_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            evidence = item["evidence"]
            self.assertIsInstance(claim, dict)
            self.assertIsInstance(evidence, list)
            assert isinstance(claim, dict)
            assert isinstance(evidence, list)
            arguments = self.profile_decide_arguments(
                claim_id=str(claim["id"]),
                review_token=str(item["review_token"]),
                confirm=True,
            )

            first_result, first_stdout, first_stderr = self.invoke(
                *arguments,
                home=str(home),
            )
            replay_result, replay_stdout, replay_stderr = self.invoke(
                *arguments,
                home=str(home),
            )
            first = json.loads(first_stdout)
            replay = json.loads(replay_stdout)

            self.assertEqual((first_result, replay_result), (0, 0))
            self.assertEqual((first_stderr, replay_stderr), ("", ""))
            self.assertEqual(set(first), JSON_ENVELOPE_KEYS)
            self.assertEqual(set(first["data"]), DECISION_RESULT_KEYS)
            self.assertEqual(replay["data"], first["data"])
            self.assertTrue(first["data"]["decision_recorded"])
            self.assertFalse(first["data"]["dry_run"])
            self.assertFalse(first["data"]["external_action_taken"])
            self.assertEqual(first["data"]["decision"], "approved")
            self.assertEqual(first["data"]["claim_status"], "verified")
            self.assertEqual(first["data"]["claim_approval_status"], "approved")
            self.assertEqual(
                first["data"]["evidence_confirmation_status"],
                "confirmed",
            )
            self.assertEqual(first["data"]["claim_id"], claim["id"])
            self.assertEqual(first["data"]["evidence_id"], evidence[0]["id"])
            self.assertRegex(first["data"]["decided_at"], r"Z\Z")
            for private_text in (
                str(item["review_token"]),
                "synthetic-cli-review-decision",
                str(claim["canonical_text"]),
                str(evidence[0]["source_text"]),
                "avery.quill@example.com",
                str(SOURCE_FILE),
            ):
                self.assertNotIn(private_text, first_stdout)

            human_result, human_stdout, human_stderr = self.invoke(
                *arguments[:-1],
                home=str(home),
            )
            self.assertEqual(human_result, 0)
            self.assertEqual(human_stderr, "")
            self.assertIn("No external action was taken", human_stdout)
            for private_text in (
                str(item["review_token"]),
                "synthetic-cli-review-decision",
                str(claim["canonical_text"]),
                str(evidence[0]["source_text"]),
            ):
                self.assertNotIn(private_text, human_stdout)

            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                self.assertEqual(len(repository.list_workflow_runs()), 2)
            review_result, review_stdout, _ = self.invoke(
                "profile", "review", "--json", home=str(home)
            )
            self.assertEqual(review_result, 0)
            review = json.loads(review_stdout)
            self.assertEqual(review["data"]["pending_count"], 4)
            self.assertNotIn(
                claim["id"],
                {candidate["claim"]["id"] for candidate in review["data"]["items"]},
            )

            changed_result, changed_stdout, changed_stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=str(claim["id"]),
                    review_token=str(item["review_token"]),
                    actor_id="different-synthetic-cli-reviewer",
                    confirm=True,
                ),
                home=str(home),
            )
            self.assertEqual(changed_result, 2)
            self.assertEqual(changed_stderr, "")
            self.assertFalse(json.loads(changed_stdout)["ok"])
            self.assertNotIn("synthetic-cli-review-decision", changed_stdout)
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                self.assertEqual(len(repository.list_workflow_runs()), 2)

    def test_profile_decide_rejection_preserves_terminal_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)

            result, stdout, stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=str(claim["id"]),
                    review_token=str(item["review_token"]),
                    decision="reject",
                    idempotency_key="synthetic-cli-review-rejection",
                    confirm=True,
                ),
                home=str(home),
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(set(payload["data"]), DECISION_RESULT_KEYS)
            self.assertEqual(payload["data"]["decision"], "rejected")
            self.assertEqual(payload["data"]["claim_status"], "withdrawn")
            self.assertEqual(payload["data"]["claim_approval_status"], "rejected")
            self.assertEqual(
                payload["data"]["evidence_confirmation_status"],
                "rejected",
            )
            self.assertFalse(payload["data"]["external_action_taken"])
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                stored_claim = repository.get_claim(str(claim["id"]))
                self.assertIsNotNone(stored_claim)
                assert stored_claim is not None
                self.assertIsNone(stored_claim["verified_at"])
                self.assertIsNone(stored_claim["verified_by"])

    def test_profile_decide_wrong_token_fails_without_reserving_the_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)
            idempotency_key = "synthetic-cli-wrong-token-decision"

            result, stdout, stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=str(claim["id"]),
                    review_token="0" * 64,
                    idempotency_key=idempotency_key,
                    confirm=True,
                ),
                home=str(home),
            )

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(json.loads(stdout)["ok"])
            self.assertNotIn(idempotency_key, stdout)

            human_arguments = self.profile_decide_arguments(
                claim_id=str(claim["id"]),
                review_token="0" * 64,
                idempotency_key=idempotency_key,
                confirm=True,
            )
            human_result, human_stdout, human_stderr = self.invoke(
                *human_arguments[:-1],
                home=str(home),
            )
            self.assertEqual(human_result, 2)
            self.assertEqual(human_stdout, "")
            self.assertIn("No external action was taken", human_stderr)
            self.assertNotIn("0" * 64, human_stderr)
            self.assertNotIn(idempotency_key, human_stderr)
            self.assertNotIn(str(claim["canonical_text"]), human_stderr)
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                self.assertEqual(len(repository.list_workflow_runs()), 1)
                stored_claim = repository.get_claim(str(claim["id"]))
                self.assertIsNotNone(stored_claim)
                assert stored_claim is not None
                self.assertEqual(stored_claim["status"], "needs_review")

            confirmed_result, confirmed_stdout, _ = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=str(claim["id"]),
                    review_token=str(item["review_token"]),
                    idempotency_key=idempotency_key,
                    confirm=True,
                ),
                home=str(home),
            )
            self.assertEqual(confirmed_result, 0)
            self.assertTrue(json.loads(confirmed_stdout)["data"]["decision_recorded"])

    def test_profile_decide_sensitive_approval_fails_but_rejection_is_allowed(
        self,
    ) -> None:
        for index, sensitivity in enumerate(
            (Sensitivity.CONFIDENTIAL, Sensitivity.HIGHLY_SENSITIVE)
        ):
            with self.subTest(sensitivity=sensitivity), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve() / "profile-home"
                init_result, _, _ = self.invoke(
                    "profile", "init", "--json", home=str(home)
                )
                self.assertEqual(init_result, 0)
                item = self.create_service_import(
                    home,
                    sensitivity=sensitivity,
                    idempotency_key=f"synthetic-cli-sensitive-import-{index}",
                )
                private_text = item.claim.canonical_text

                result, stdout, stderr = self.invoke(
                    *self.profile_decide_arguments(
                        claim_id=item.claim.id,
                        review_token=str(item.review_token),
                        idempotency_key=f"synthetic-cli-sensitive-approval-{index}",
                        confirm=True,
                    ),
                    home=str(home),
                )

                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(json.loads(stdout)["ok"])
                self.assertNotIn(private_text, stdout)
                with SQLiteRepository(
                    home / "data" / "grounded_apply.db",
                    read_only=True,
                ) as repository:
                    self.assertEqual(len(repository.list_workflow_runs()), 1)
                    stored_claim = repository.get_claim(item.claim.id)
                    self.assertIsNotNone(stored_claim)
                    assert stored_claim is not None
                    self.assertEqual(stored_claim["status"], "needs_review")

                reject_result, reject_stdout, _ = self.invoke(
                    *self.profile_decide_arguments(
                        claim_id=item.claim.id,
                        review_token=str(item.review_token),
                        decision="reject",
                        idempotency_key=f"synthetic-cli-sensitive-rejection-{index}",
                        confirm=True,
                    ),
                    home=str(home),
                )
                self.assertEqual(reject_result, 0)
                rejected = json.loads(reject_stdout)
                self.assertEqual(rejected["data"]["decision"], "rejected")
                self.assertNotIn(private_text, reject_stdout)

    def test_profile_decide_public_contradiction_is_structured_and_minimized(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)
            conflict_value = "SYNTHETIC_PUBLIC_CONFLICT_VALUE_4DP"
            conflict_text = "SYNTHETIC_PUBLIC_CONFLICT_CANONICAL_5EQ"
            conflict_ref = "synthetic-public-conflict-ref-6fr"
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                existing_only=True,
            ) as repository:
                conflicting = ProfileService(repository).create_claim(
                    CreateClaim(
                        claim_type=str(claim["claim_type"]),
                        value=conflict_value,
                        canonical_text=conflict_text,
                        subject_type=str(claim["subject_type"]),
                        subject_id=claim["subject_id"],  # type: ignore[arg-type]
                        source_type=SourceType.USER_STATEMENT,
                        source_ref=conflict_ref,
                        status=ClaimStatus.CONTRADICTED,
                        approval_status=ApprovalStatus.REJECTED,
                        sensitivity=Sensitivity.PUBLIC,
                    )
                )
                workflow_count = len(repository.list_workflow_runs())

            result, stdout, stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=str(claim["id"]),
                    review_token=str(item["review_token"]),
                    confirm=True,
                ),
                home=str(home),
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["type"], "Contradiction")
            self.assertEqual(set(payload["data"]), DECISION_CONTRADICTION_KEYS)
            self.assertFalse(payload["data"]["decision_recorded"])
            self.assertFalse(payload["data"]["external_action_taken"])
            self.assertEqual(
                payload["data"]["conflicting_claim_ids"],
                [conflicting.id],
            )
            for omitted in (conflict_value, conflict_text, conflict_ref):
                self.assertNotIn(omitted, stdout)

            human_arguments = self.profile_decide_arguments(
                claim_id=str(claim["id"]),
                review_token=str(item["review_token"]),
                confirm=True,
            )
            human_result, human_stdout, human_stderr = self.invoke(
                *human_arguments[:-1],
                home=str(home),
            )
            self.assertEqual(human_result, 2)
            self.assertEqual(human_stderr, "")
            self.assertIn("No external action was taken", human_stdout)
            self.assertNotIn(str(item["review_token"]), human_stdout)
            self.assertNotIn("synthetic-cli-review-decision", human_stdout)
            for omitted in (conflict_value, conflict_text, conflict_ref):
                self.assertNotIn(omitted, human_stdout)
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                self.assertEqual(len(repository.list_workflow_runs()), workflow_count)
                stored_claim = repository.get_claim(str(claim["id"]))
                self.assertIsNotNone(stored_claim)
                assert stored_claim is not None
                self.assertEqual(stored_claim["status"], "needs_review")

    def test_profile_decide_private_contradiction_is_not_disclosed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)
            private_value = "SYNTHETIC_PRIVATE_CONFLICT_VALUE_7GS"
            private_text = "SYNTHETIC_PRIVATE_CONFLICT_CANONICAL_8HT"
            private_ref = "synthetic-private-conflict-ref-9iu"
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                existing_only=True,
            ) as repository:
                ProfileService(repository).create_claim(
                    CreateClaim(
                        claim_type=str(claim["claim_type"]),
                        value=private_value,
                        canonical_text=private_text,
                        subject_type=str(claim["subject_type"]),
                        subject_id=claim["subject_id"],  # type: ignore[arg-type]
                        source_type=SourceType.USER_STATEMENT,
                        source_ref=private_ref,
                        status=ClaimStatus.CONTRADICTED,
                        approval_status=ApprovalStatus.REJECTED,
                        sensitivity=Sensitivity.HIGHLY_SENSITIVE,
                    )
                )
                workflow_count = len(repository.list_workflow_runs())

            result, stdout, stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=str(claim["id"]),
                    review_token=str(item["review_token"]),
                    confirm=True,
                ),
                home=str(home),
            )

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(json.loads(stdout)["ok"])
            for omitted in (private_value, private_text, private_ref):
                self.assertNotIn(omitted, stdout)
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                self.assertEqual(len(repository.list_workflow_runs()), workflow_count)

    def test_profile_decide_confirm_requires_initialized_storage_without_writing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "not-created"
            claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-cli-review-item"))

            result, stdout, stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=claim_id,
                    review_token="a" * 64,
                    confirm=True,
                ),
                home=str(home),
            )

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(json.loads(stdout)["ok"])
            self.assertFalse(home.exists())

    def test_profile_decide_unconfirmed_input_validation_does_not_require_an_item(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "not-created"
            claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-missing-review-item"))

            result, stdout, stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=claim_id,
                    review_token="a" * 64,
                ),
                home=str(home),
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 0)
            self.assertEqual(stderr, "")
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["data"]["dry_run"])
            self.assertFalse(payload["data"]["storage_checked"])
            self.assertFalse(payload["data"]["decision_recorded"])
            self.assertFalse(home.exists())

    def test_profile_decide_invalid_action_uses_the_json_error_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "not-created"
            claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-invalid-action"))

            result, stdout, stderr = self.invoke(
                *self.profile_decide_arguments(
                    claim_id=claim_id,
                    review_token="a" * 64,
                    decision="approved",
                ),
                home=str(home),
            )
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertEqual(set(payload), JSON_ENVELOPE_KEYS)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["type"], "CliInputError")
            self.assertNotIn("a" * 64, stdout)
            self.assertFalse(home.exists())

    def test_profile_decide_parser_errors_use_a_non_disclosing_json_contract(
        self,
    ) -> None:
        claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-parser-error"))
        secret = "SYNTHETIC_PARSER_VALUE_MUST_NOT_BE_ECHOED"
        cases = (
            (
                "missing_required_option",
                (
                    "profile",
                    "decide",
                    "--claim-id",
                    claim_id,
                    "--review-token",
                    "a" * 64,
                    "--decision",
                    "approve",
                    "--actor-id",
                    "synthetic-parser-reviewer",
                    "--json",
                ),
            ),
            (
                "unknown_option",
                (
                    *self.profile_decide_arguments(
                        claim_id=claim_id,
                        review_token="a" * 64,
                    ),
                    "--synthetic-unknown-option",
                    secret,
                ),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "not-created"
            for case, arguments in cases:
                with self.subTest(case=case):
                    result, stdout, stderr = self.invoke(
                        *arguments,
                        home=str(home),
                    )
                    payload = json.loads(stdout)

                    self.assertEqual(result, 2)
                    self.assertEqual(stderr, "")
                    self.assertEqual(set(payload), JSON_ENVELOPE_KEYS)
                    self.assertEqual(payload["command"], "profile.decide")
                    self.assertFalse(payload["ok"])
                    self.assertEqual(payload["error"]["type"], "CliUsageError")
                    self.assertNotIn(secret, stdout)
                    self.assertNotIn("a" * 64, stdout)
                    self.assertFalse(home.exists())

            human_arguments = self.profile_decide_arguments(
                claim_id=claim_id,
                review_token="a" * 64,
            )
            human_result, human_stdout, human_stderr = self.invoke(
                *human_arguments[:-1],
                "--synthetic-unknown-option",
                secret,
                home=str(home),
            )
            self.assertEqual(human_result, 2)
            self.assertEqual(human_stdout, "")
            self.assertIn("No external action was taken", human_stderr)
            self.assertNotIn(secret, human_stderr)
            self.assertNotIn("a" * 64, human_stderr)
            self.assertNotIn("synthetic-cli-review-decision", human_stderr)

    def test_profile_decide_output_failure_is_recoverable_by_exact_retry(self) -> None:
        from grounded_apply import cli as cli_module

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)
            arguments = self.profile_decide_arguments(
                claim_id=str(claim["id"]),
                review_token=str(item["review_token"]),
                idempotency_key="synthetic-cli-output-retry",
                confirm=True,
            )
            original_emit = cli_module._emit
            failed = False
            partial_output = '{"command":"profile.decide","partial":'

            def fail_after_commit(*emit_args: object, **emit_kwargs: object) -> None:
                nonlocal failed
                data = emit_kwargs.get("data")
                if (
                    not failed
                    and emit_kwargs.get("command") == "profile.decide"
                    and isinstance(data, dict)
                    and data.get("decision_recorded") is True
                ):
                    failed = True
                    print(partial_output, end="")
                    raise OSError("synthetic decision output failure")
                original_emit(*emit_args, **emit_kwargs)  # type: ignore[arg-type]

            with patch.object(cli_module, "_emit", side_effect=fail_after_commit):
                failed_result, failed_stdout, failed_stderr = self.invoke(
                    *arguments,
                    home=str(home),
                )

            self.assertTrue(failed)
            self.assertEqual(failed_result, 2)
            self.assertEqual(failed_stdout, partial_output)
            self.assertIn("may already be recorded", failed_stderr)
            self.assertIn("exact same confirmed request", failed_stderr)
            self.assertIn("No external action was taken", failed_stderr)
            self.assertEqual(failed_stdout.count("profile.decide"), 1)
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                workflows = repository.list_workflow_runs(
                    workflow_type="profile_import_review_decision"
                )
                self.assertEqual(len(workflows), 1)
                committed = workflows[0]
                review_item = repository.get_profile_import_review_item(str(claim["id"]))
                self.assertIsNotNone(review_item)
                assert review_item is not None
                self.assertEqual(committed["status"], "succeeded")
                self.assertEqual(
                    committed["id"],
                    review_item["decision_workflow_run_id"],
                )
                committed_decided_at = review_item["decided_at"]

            retry_result, retry_stdout, retry_stderr = self.invoke(
                *arguments,
                home=str(home),
            )
            retry = json.loads(retry_stdout)
            self.assertEqual(retry_result, 0)
            self.assertEqual(retry_stderr, "")
            self.assertTrue(retry["data"]["decision_recorded"])
            self.assertEqual(retry["data"]["decision_workflow_run_id"], committed["id"])
            self.assertEqual(retry["data"]["decided_at"], committed_decided_at)
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                self.assertEqual(len(repository.list_workflow_runs()), 2)

    def test_profile_decide_persistent_output_failure_falls_back_safely(self) -> None:
        from grounded_apply import cli as cli_module

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)
            arguments = self.profile_decide_arguments(
                claim_id=str(claim["id"]),
                review_token=str(item["review_token"]),
                idempotency_key="synthetic-cli-persistent-output-retry",
                confirm=True,
            )

            with patch.object(
                cli_module,
                "_emit",
                side_effect=OSError("synthetic persistent output failure"),
            ):
                result, stdout, stderr = self.invoke(*arguments, home=str(home))

            self.assertEqual(result, 2)
            self.assertEqual(stdout, "")
            self.assertIn("may already be recorded", stderr)
            self.assertIn("exact same confirmed request", stderr)
            self.assertIn("No external action was taken", stderr)
            self.assertNotIn(str(item["review_token"]), stderr)
            self.assertNotIn("synthetic-cli-persistent-output-retry", stderr)
            self.assertNotIn(str(claim["canonical_text"]), stderr)

            retry_result, retry_stdout, retry_stderr = self.invoke(
                *arguments,
                home=str(home),
            )
            retry = json.loads(retry_stdout)
            self.assertEqual(retry_result, 0)
            self.assertEqual(retry_stderr, "")
            self.assertTrue(retry["data"]["decision_recorded"])
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as repository:
                self.assertEqual(len(repository.list_workflow_runs()), 2)

    def test_profile_decide_close_failure_after_commit_is_recoverable(self) -> None:
        from grounded_apply import cli as cli_module

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)
            arguments = self.profile_decide_arguments(
                claim_id=str(claim["id"]),
                review_token=str(item["review_token"]),
                idempotency_key="synthetic-cli-close-output-retry",
                confirm=True,
            )
            repository = SQLiteRepository(
                home / "data" / "grounded_apply.db",
                existing_only=True,
            ).initialize()
            original_close = repository.close

            def close_then_fail() -> None:
                original_close()
                raise OSError("synthetic close reporting failure")

            with (
                patch.object(
                    cli_module,
                    "_open_initialized_profile_repository",
                    return_value=repository,
                ),
                patch.object(repository, "close", side_effect=close_then_fail),
            ):
                result, stdout, stderr = self.invoke(*arguments, home=str(home))

            self.assertEqual(result, 2)
            self.assertEqual(stdout, "")
            self.assertIn("may already be recorded", stderr)
            self.assertIn("exact same confirmed request", stderr)
            self.assertIn("No external action was taken", stderr)

            retry_result, retry_stdout, retry_stderr = self.invoke(
                *arguments,
                home=str(home),
            )
            retry = json.loads(retry_stdout)
            self.assertEqual(retry_result, 0)
            self.assertEqual(retry_stderr, "")
            self.assertTrue(retry["data"]["decision_recorded"])
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as stored:
                self.assertEqual(len(stored.list_workflow_runs()), 2)

    def test_profile_decide_interruption_after_commit_gives_exact_retry_guidance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / "profile-home"
            item = self.initialize_import_and_review(home)[0]
            claim = item["claim"]
            self.assertIsInstance(claim, dict)
            assert isinstance(claim, dict)
            arguments = self.profile_decide_arguments(
                claim_id=str(claim["id"]),
                review_token=str(item["review_token"]),
                idempotency_key="synthetic-cli-interrupted-output-retry",
                confirm=True,
            )
            original_decide = ProfileService.decide_review_item

            def commit_then_interrupt(
                service: ProfileService,
                request: CreateProfileReviewDecision,
            ) -> object:
                original_decide(service, request)
                raise KeyboardInterrupt

            with patch.object(
                ProfileService,
                "decide_review_item",
                autospec=True,
                side_effect=commit_then_interrupt,
            ):
                result, stdout, stderr = self.invoke(*arguments, home=str(home))

            self.assertEqual(result, 130)
            self.assertEqual(stdout, "")
            self.assertIn("may already be recorded", stderr)
            self.assertIn("exact same confirmed request", stderr)
            self.assertIn("No external action was taken", stderr)
            self.assertNotIn(str(item["review_token"]), stderr)
            self.assertNotIn("synthetic-cli-interrupted-output-retry", stderr)
            self.assertNotIn(str(claim["canonical_text"]), stderr)

            retry_result, retry_stdout, retry_stderr = self.invoke(
                *arguments,
                home=str(home),
            )
            retry = json.loads(retry_stdout)
            self.assertEqual(retry_result, 0)
            self.assertEqual(retry_stderr, "")
            self.assertTrue(retry["data"]["decision_recorded"])
            with SQLiteRepository(
                home / "data" / "grounded_apply.db",
                read_only=True,
            ) as stored:
                self.assertEqual(len(stored.list_workflow_runs()), 2)

    def test_profile_decide_help_exposes_no_override_or_external_action(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            main(("profile", "decide", "--help"))

        help_text = stdout.getvalue()
        self.assertEqual(raised.exception.code, 0)
        for forbidden in (
            "--all",
            "--decided-at",
            "--edit",
            "--evidence",
            "--force",
            "--sensitivity",
            "--submit",
            "--value",
        ):
            self.assertNotIn(forbidden, help_text)
        for required in (
            "--actor-id",
            "--claim-id",
            "--confirm",
            "--decision",
            "--idempotency-key",
            "--review-token",
        ):
            self.assertIn(required, help_text)
        self.assertIn("OPAQUE_ACTOR_ID", help_text)
        self.assertIn("OPAQUE_KEY", help_text)
        self.assertIn("OPAQUE_TOKEN", help_text)
        normalized_help = " ".join(help_text.split())
        self.assertGreaterEqual(normalized_help.count("do not include candidate data"), 3)

    def test_profile_decide_does_not_abbreviate_the_confirmation_gate(self) -> None:
        from grounded_apply import cli as cli_module

        claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-abbreviated-confirm"))
        arguments = self.profile_decide_arguments(
            claim_id=claim_id,
            review_token="a" * 64,
        )
        without_json = arguments[:-1]
        for abbreviation in ("--conf", "--confi"):
            with self.subTest(abbreviation=abbreviation):
                with (
                    redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    cli_module.build_parser().parse_args(
                        (*without_json, abbreviation, "--json")
                    )
                self.assertEqual(raised.exception.code, 2)

    def test_profile_review_and_doctor_do_not_touch_active_wal_sidecars(self) -> None:
        for command, arguments in (
            ("review", ("profile", "review", "--json")),
            ("doctor", ("doctor", "--json")),
        ):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve() / "profile-home"
                init_result, _, _ = self.invoke(
                    "profile", "init", "--json", home=str(home)
                )
                self.assertEqual(init_result, 0)
                database = home / "data" / "grounded_apply.db"
                with closing(sqlite3.connect(database)) as connection:
                    journal_mode = connection.execute(
                        "PRAGMA journal_mode = WAL"
                    ).fetchone()
                    self.assertIsNotNone(journal_mode)
                    assert journal_mode is not None
                    self.assertEqual(str(journal_mode[0]).lower(), "wal")
                    connection.execute(
                        "CREATE TABLE synthetic_wal_probe (value TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO synthetic_wal_probe (value) VALUES (?)",
                        ("synthetic WAL state",),
                    )
                    connection.commit()
                    wal = Path(f"{database}-wal")
                    shared_memory = Path(f"{database}-shm")
                    self.assertTrue(wal.is_file())
                    self.assertTrue(shared_memory.is_file())
                    snapshots = {
                        path: (path.read_bytes(), path.stat())
                        for path in (database, wal, shared_memory)
                    }

                    result, stdout, stderr = self.invoke(
                        *arguments,
                        home=str(home),
                    )

                    payload = json.loads(stdout)
                    self.assertEqual(result, 2)
                    self.assertEqual(stderr, "")
                    self.assertFalse(payload["ok"])
                    self.assertIn("sidecar", json.dumps(payload).lower())
                    for path, (content, before) in snapshots.items():
                        after = path.stat()
                        self.assertEqual(path.read_bytes(), content)
                        self.assertEqual(after.st_mode, before.st_mode)
                        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                        self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)

    def test_profile_review_and_doctor_reject_closed_persistent_wal_without_writes(
        self,
    ) -> None:
        for command, arguments in (
            ("review", ("profile", "review", "--json")),
            ("doctor", ("doctor", "--json")),
        ):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve() / "profile-home"
                init_result, _, _ = self.invoke(
                    "profile", "init", "--json", home=str(home)
                )
                self.assertEqual(init_result, 0)
                database = home / "data" / "grounded_apply.db"
                with closing(sqlite3.connect(database)) as connection:
                    journal_mode = connection.execute(
                        "PRAGMA journal_mode = WAL"
                    ).fetchone()
                    self.assertIsNotNone(journal_mode)
                    assert journal_mode is not None
                    self.assertEqual(str(journal_mode[0]).lower(), "wal")
                    connection.execute(
                        "CREATE TABLE synthetic_closed_wal_probe (value TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO synthetic_closed_wal_probe (value) VALUES (?)",
                        ("synthetic closed WAL state",),
                    )
                    connection.commit()
                wal = Path(f"{database}-wal")
                shared_memory = Path(f"{database}-shm")
                self.assertFalse(wal.exists())
                self.assertFalse(shared_memory.exists())
                before_bytes = database.read_bytes()
                before = database.stat()
                before_paths = {path.relative_to(home) for path in home.rglob("*")}

                result, stdout, stderr = self.invoke(
                    *arguments,
                    home=str(home),
                )

                payload = json.loads(stdout)
                after = database.stat()
                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn("persistent sqlite wal", json.dumps(payload).lower())
                self.assertEqual(database.read_bytes(), before_bytes)
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)
                self.assertFalse(wal.exists())
                self.assertFalse(shared_memory.exists())
                self.assertEqual(
                    {path.relative_to(home) for path in home.rglob("*")},
                    before_paths,
                )

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
            home.chmod(0o700)
            database.parent.chmod(0o700)
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA user_version = 999")
            connection.close()
            database.chmod(0o600)

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
            home.chmod(0o700)
            database.parent.chmod(0o700)
            connection = sqlite3.connect(database)
            connection.execute("CREATE TABLE unknown_personal_data (value TEXT)")
            connection.commit()
            connection.close()
            database.chmod(0o600)

            result, stdout, _ = self.invoke("doctor", "--json", home=str(home))
            payload = json.loads(stdout)

            self.assertEqual(result, 2)
            self.assertFalse(payload["ok"])
            self.assertIn("unversioned schema", payload["data"]["checks"]["database"]["error"])

    def test_doctor_does_not_inspect_a_multiply_linked_database(self) -> None:
        from grounded_apply import cli as cli_module

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "profile-home"
            init_result, _, _ = self.invoke(
                "profile", "init", "--json", home=str(home)
            )
            self.assertEqual(init_result, 0)
            database = home / "data" / "grounded_apply.db"
            outside_alias = root / "synthetic-profile-alias.db"
            os.link(database, outside_alias)
            before = database.stat()
            before_bytes = database.read_bytes()

            with patch.object(
                cli_module,
                "_read_schema_version",
                side_effect=AssertionError("unsafe database was inspected"),
            ) as read_schema:
                result, stdout, stderr = self.invoke(
                    "doctor", "--json", home=str(home)
                )

            payload = json.loads(stdout)
            after = database.stat()
            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertFalse(payload["data"]["checks"]["runtime_home"]["ok"])
            self.assertFalse(payload["data"]["checks"]["database"]["ok"])
            self.assertIsNone(
                payload["data"]["checks"]["database"]["initialized"]
            )
            self.assertIn(
                "inspection was skipped",
                payload["data"]["checks"]["database"]["error"],
            )
            read_schema.assert_not_called()
            self.assertEqual(database.read_bytes(), before_bytes)
            self.assertEqual(outside_alias.read_bytes(), before_bytes)
            self.assertEqual(after.st_mode, before.st_mode)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)

    def test_commands_reject_a_nonregular_database_before_storage_access(self) -> None:
        from grounded_apply import cli as cli_module

        claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-directory-database"))
        commands = (
            ("init", ("profile", "init", "--json")),
            ("init_dry_run", ("profile", "init", "--dry-run", "--json")),
            ("import", self.profile_import_arguments()),
            ("import_dry_run", self.profile_import_arguments(dry_run=True)),
            ("review", ("profile", "review", "--json")),
            (
                "decide",
                self.profile_decide_arguments(
                    claim_id=claim_id,
                    review_token="a" * 64,
                    confirm=True,
                ),
            ),
            ("doctor", ("doctor", "--json")),
        )
        for command, arguments in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                home = Path(directory).resolve() / "profile-home"
                data_dir = home / "data"
                data_dir.mkdir(mode=0o700, parents=True)
                home.chmod(0o700)
                data_dir.chmod(0o700)
                database = data_dir / "grounded_apply.db"
                database.mkdir(mode=0o700)
                sentinel = database / "synthetic-sentinel.txt"
                content = "SYNTHETIC DIRECTORY DATABASE CONTENT"
                sentinel.write_text(content, encoding="utf-8")
                before = database.stat()

                with (
                    patch.object(
                        cli_module,
                        "_repository_for",
                        side_effect=AssertionError("nonregular database reached SQLite"),
                    ) as repository_for,
                    patch.object(
                        cli_module,
                        "_read_schema_version",
                        side_effect=AssertionError("nonregular database was inspected"),
                    ) as read_schema,
                ):
                    result, stdout, stderr = self.invoke(
                        *arguments,
                        home=str(home),
                    )

                payload = json.loads(stdout)
                after = database.stat()
                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn("regular non-symlink", json.dumps(payload))
                repository_for.assert_not_called()
                read_schema.assert_not_called()
                self.assertTrue(database.is_dir())
                self.assertEqual(sentinel.read_text(encoding="utf-8"), content)
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)
                self.assertFalse((home / "config").exists())

    def test_repository_open_rechecks_a_hard_link_added_during_connect(self) -> None:
        from grounded_apply.repositories import sqlite as sqlite_module

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "profile-home"
            init_result, _, _ = self.invoke(
                "profile", "init", "--json", home=str(home)
            )
            self.assertEqual(init_result, 0)
            database = home / "data" / "grounded_apply.db"
            outside_alias = root / "synthetic-connect-race-alias.db"
            before_bytes = database.read_bytes()
            before = database.stat()
            original_connect = sqlite_module.sqlite3.connect

            def connect_then_link(*args: object, **kwargs: object) -> sqlite3.Connection:
                connection = original_connect(*args, **kwargs)  # type: ignore[arg-type]
                os.link(database, outside_alias)
                return connection

            with patch.object(
                sqlite_module.sqlite3,
                "connect",
                side_effect=connect_then_link,
            ):
                result, stdout, stderr = self.invoke(
                    *self.profile_import_arguments(),
                    home=str(home),
                )

            payload = json.loads(stdout)
            after = database.stat()
            alias_after = outside_alias.stat()
            self.assertEqual(result, 2)
            self.assertEqual(stderr, "")
            self.assertFalse(payload["ok"])
            self.assertIn("one hard link", payload["error"]["message"])
            self.assertEqual(database.read_bytes(), before_bytes)
            self.assertEqual(outside_alias.read_bytes(), before_bytes)
            self.assertEqual(after.st_ino, alias_after.st_ino)
            self.assertEqual(after.st_nlink, 2)
            self.assertEqual(after.st_mode, before.st_mode)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            with closing(
                original_connect(f"file:{database}?mode=ro", uri=True)
            ) as connection:
                workflow_count = connection.execute(
                    "SELECT count(*) FROM workflow_runs"
                ).fetchone()
                self.assertIsNotNone(workflow_count)
                assert workflow_count is not None
                self.assertEqual(workflow_count[0], 0)

    def test_commands_do_not_open_a_database_with_an_unsafe_sqlite_sidecar(
        self,
    ) -> None:
        from grounded_apply import cli as cli_module

        synthetic_claim_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-sidecar-review-item")
        )
        commands = (
            ("init", ("profile", "init", "--json")),
            ("import", self.profile_import_arguments()),
            ("review", ("profile", "review", "--json")),
            (
                "decide",
                self.profile_decide_arguments(
                    claim_id=synthetic_claim_id,
                    review_token="a" * 64,
                    confirm=True,
                ),
            ),
            ("doctor", ("doctor", "--json")),
        )
        for command, arguments in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                home = root / "profile-home"
                init_result, _, _ = self.invoke(
                    "profile", "init", "--json", home=str(home)
                )
                self.assertEqual(init_result, 0)
                database = home / "data" / "grounded_apply.db"
                sidecar = Path(f"{database}-journal")
                content = b"synthetic unsafe rollback journal bytes"
                sidecar.write_bytes(content)
                sidecar.chmod(0o600)
                outside_alias = root / "synthetic-profile-journal-alias"
                os.link(sidecar, outside_alias)
                before = sidecar.stat()

                with (
                    patch.object(
                        cli_module,
                        "_repository_for",
                        side_effect=AssertionError("unsafe database was opened"),
                    ) as repository_for,
                    patch.object(
                        cli_module,
                        "_read_schema_version",
                        side_effect=AssertionError("unsafe database was inspected"),
                    ) as read_schema,
                ):
                    result, stdout, stderr = self.invoke(
                        *arguments,
                        home=str(home),
                    )

                payload = json.loads(stdout)
                sidecar_after = sidecar.stat()
                alias_after = outside_alias.stat()
                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn("sidecar", json.dumps(payload).lower())
                repository_for.assert_not_called()
                read_schema.assert_not_called()
                self.assertEqual(sidecar.read_bytes(), content)
                self.assertEqual(outside_alias.read_bytes(), content)
                self.assertEqual(sidecar_after.st_ino, alias_after.st_ino)
                self.assertEqual(sidecar_after.st_nlink, 2)
                self.assertEqual(sidecar_after.st_mode, before.st_mode)
                self.assertEqual(sidecar_after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(sidecar_after.st_ctime_ns, before.st_ctime_ns)

    def test_commands_reject_orphan_sqlite_sidecars_before_storage_access(
        self,
    ) -> None:
        from grounded_apply import cli as cli_module

        commands = (
            ("init", ("profile", "init", "--json")),
            ("init_dry_run", ("profile", "init", "--dry-run", "--json")),
            ("import_dry_run", self.profile_import_arguments(dry_run=True)),
            ("doctor", ("doctor", "--json")),
        )
        for command, arguments in commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                home = root / "profile-home"
                data_dir = home / "data"
                data_dir.mkdir(mode=0o700, parents=True)
                home.chmod(0o700)
                data_dir.chmod(0o700)
                database = data_dir / "grounded_apply.db"
                sidecar = Path(f"{database}-journal")
                content = b"synthetic orphan rollback journal bytes"
                sidecar.write_bytes(content)
                sidecar.chmod(0o600)
                before = sidecar.stat()

                with (
                    patch.object(
                        cli_module,
                        "_repository_for",
                        side_effect=AssertionError("orphan sidecar reached SQLite"),
                    ) as repository_for,
                    patch.object(
                        cli_module,
                        "_read_schema_version",
                        side_effect=AssertionError("orphan sidecar was inspected"),
                    ) as read_schema,
                ):
                    result, stdout, stderr = self.invoke(
                        *arguments,
                        home=str(home),
                    )

                payload = json.loads(stdout)
                after = sidecar.stat()
                self.assertEqual(result, 2)
                self.assertEqual(stderr, "")
                self.assertFalse(payload["ok"])
                self.assertIn("sidecar", json.dumps(payload).lower())
                repository_for.assert_not_called()
                read_schema.assert_not_called()
                self.assertFalse(database.exists())
                self.assertEqual(sidecar.read_bytes(), content)
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(after.st_ctime_ns, before.st_ctime_ns)
                self.assertFalse((home / "config").exists())

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
