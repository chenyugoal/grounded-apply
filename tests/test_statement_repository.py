"""Exact origin checks at the managed import persistence boundary."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories import Record, RepositoryError, SQLiteRepository
from tests.test_repository import DECISION_TIME, IMPORT_TIME


class StatementRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reset_repository()

    def reset_repository(self) -> None:
        if hasattr(self, "repository"):
            self.repository.close()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = SQLiteRepository(Path(directory.name) / "fictional.db").initialize()
        self.addCleanup(self.repository.close)

    def fixture(self, *, source_type: str = "user_statement", bind: bool = True) -> dict[str, object]:
        statement = source_type == "user_statement"
        ref = ("user-statement:sha256:" if statement else "sha256:") + "a" * 64
        artifact = ("profile-user-statement-source:sha256:" if statement else "profile-import-source:sha256:") + "a" * 64
        extractor = "grounded-apply.profile-user-statement.manifest@1" if statement else "grounded-apply.profile-import.manifest@1"
        self.repository.add_workflow_run(run_id="fictional-import", workflow_type="profile_import_proposal", created_at=IMPORT_TIME)
        self.repository.add_workflow_run(run_id="fictional-decision", workflow_type="profile_import_review_decision", created_at=DECISION_TIME)
        self.repository.add_artifact(artifact_id=artifact, artifact_type="profile_import_source_digest", uri=ref, created_at=IMPORT_TIME)
        self.repository.add_claim(claim_id="fictional-claim", claim_type="skill_use", value="Python", canonical_text="Used Python", source_type=source_type, source_ref=ref, created_at=IMPORT_TIME)
        self.repository.add_evidence(evidence_id="fictional-evidence", claim_id="fictional-claim", source_type=source_type, source_ref=ref, source_text="Used Python", artifact_id=artifact, extraction_method=extractor, created_at=IMPORT_TIME)
        args = dict(import_workflow_run_id="fictional-import", proposal_index=0, claim_id="fictional-claim", evidence_id="fictional-evidence", record_sha256="b" * 64, source_type=source_type, created_at=IMPORT_TIME)
        if bind:
            self.repository.add_profile_import_review_item(**args)
        return args

    def decide(self, decision: str = "approved", *, source_type: str = "user_statement") -> Record:
        return self.repository.decide_profile_import_review_item("fictional-claim", decision=decision, decision_workflow_run_id="fictional-decision", decided_by="fictional-reviewer", decided_at=DECISION_TIME, source_type=source_type)

    def corrupt(self, table: str, column: str, value: object) -> None:
        # Deliberate corruption of disposable synthetic storage; identifiers are
        # fixed test cases, never external input.
        with closing(sqlite3.connect(self.repository.database)) as connection, connection:
            connection.execute(f"UPDATE {table} SET {column} = ?", (value,))

    def test_statement_decisions_and_both_replays_keep_exact_origin(self) -> None:
        for decision in ("approved", "rejected"):
            with self.subTest(decision=decision):
                if decision == "rejected":
                    self.reset_repository()
                args = self.fixture()
                first = self.decide(decision)
                self.assertEqual(first, self.decide(decision))
                self.assertEqual(first, self.repository.add_profile_import_review_item(**args))
                before = Path(self.repository.database).read_bytes()
                with self.assertRaises(RepositoryError):
                    self.decide(decision, source_type="imported_resume")
                with self.assertRaises(RepositoryError):
                    self.repository.add_profile_import_review_item(**{**args, "source_type": "imported_resume"})
                self.assertEqual(before, Path(self.repository.database).read_bytes())
                self.assertEqual(self.repository.get_claim("fictional-claim")["source_type"], "user_statement")
                self.assertEqual(self.repository.get_evidence("fictional-evidence")["source_type"], "user_statement")
                self.assertEqual(first["decision"], decision)

    def test_pending_association_rejects_mixed_and_malformed_identity(self) -> None:
        cases = (
            ("claims", "source_type", "imported_resume"),
            ("evidence", "source_type", "imported_resume"),
            ("claims", "source_ref", "sha256:" + "a" * 64),
            ("claims", "source_ref", "user-statement:sha256:" + "A" * 64),
            ("evidence", "source_ref", "user-statement:sha256:" + "c" * 64),
            ("evidence", "artifact_id", None),
            ("evidence", "extraction_method", "grounded-apply.profile-import.manifest@1"),
        )
        for index, (table, column, value) in enumerate(cases):
            with self.subTest(table=table, column=column, index=index):
                if index:
                    self.reset_repository()
                args = self.fixture(bind=False)
                self.corrupt(table, column, value)
                before = Path(self.repository.database).read_bytes()
                with self.assertRaises(RepositoryError):
                    self.repository.add_profile_import_review_item(**args)
                self.assertEqual(before, Path(self.repository.database).read_bytes())
                self.assertIsNone(self.repository.get_profile_import_review_item("fictional-claim"))

    def test_pending_decision_rejects_identity_change_without_partial_approval(self) -> None:
        self.fixture()
        self.corrupt("evidence", "source_ref", "user-statement:sha256:" + "c" * 64)
        before = Path(self.repository.database).read_bytes()
        with self.assertRaisesRegex(RepositoryError, "source identity"):
            self.decide()
        self.assertEqual(before, Path(self.repository.database).read_bytes())
        self.assertEqual(self.repository.get_claim("fictional-claim")["status"], "needs_review")
        self.assertEqual(self.repository.get_evidence("fictional-evidence")["confirmation_status"], "pending")
        self.assertIsNone(self.repository.get_profile_import_review_item("fictional-claim")["decision"])

    def test_decided_replays_reject_origin_and_identity_changes_for_both_ingresses(self) -> None:
        cases = (
            ("claims", "source_type", "other"),
            ("evidence", "source_type", "other"),
            ("claims", "source_ref", "fictional-source"),
            ("evidence", "source_ref", "fictional-source"),
            ("evidence", "extraction_method", "manual"),
        )
        index = 0
        for source_type in ("imported_resume", "user_statement"):
            for decision in ("approved", "rejected"):
                for table, column, value in cases:
                    with self.subTest(source_type=source_type, decision=decision, table=table, column=column):
                        if index:
                            self.reset_repository()
                        index += 1
                        args = self.fixture(source_type=source_type)
                        self.decide(decision, source_type=source_type)
                        self.corrupt(table, column, value)
                        before = Path(self.repository.database).read_bytes()
                        with self.assertRaisesRegex(RepositoryError, "source identity"):
                            self.decide(decision, source_type=source_type)
                        with self.assertRaisesRegex(RepositoryError, "source identity"):
                            self.repository.add_profile_import_review_item(**args)
                        self.assertEqual(before, Path(self.repository.database).read_bytes())

    def test_invalid_expected_origin_is_rejected_before_transaction(self) -> None:
        args = self.fixture()
        for origin in (None, True, "", "other", "private-invalid-origin"):
            with self.subTest(origin=origin), patch.object(self.repository, "transaction") as transaction:
                with self.assertRaisesRegex(ValueError, "source type") as error:
                    self.repository.add_profile_import_review_item(**{**args, "source_type": origin})
                self.assertNotIn("private-invalid-origin", str(error.exception))
                with self.assertRaisesRegex(ValueError, "source type"):
                    self.decide(source_type=origin)
                transaction.assert_not_called()


if __name__ == "__main__":
    unittest.main()
