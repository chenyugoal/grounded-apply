from __future__ import annotations

import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from grounded_apply.repositories import (
    RepositoryError,
    RepositoryNotInitializedError,
    SchemaError,
    SQLiteRepository,
)


IMPORT_TIME = "2026-08-11T12:00:00Z"
DECISION_TIME = "2026-08-11T13:00:00Z"


def add_import_review_fixture(
    repository: SQLiteRepository,
    *,
    suffix: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    workflow = repository.add_workflow_run(
        run_id=f"import-workflow-{suffix}",
        workflow_type="profile_import_proposal",
        status="succeeded",
        input_data={"fixture": suffix},
        current_step="awaiting_review",
        created_at=IMPORT_TIME,
    )
    claim = repository.add_claim(
        claim_id=f"import-claim-{suffix}",
        claim_type="skill_use",
        value="Python",
        canonical_text="Used Python in a fictional project",
        source_type="imported_resume",
        source_ref=f"sha256:{'a' * 64}",
        created_at=IMPORT_TIME,
    )
    evidence = repository.add_evidence(
        evidence_id=f"import-evidence-{suffix}",
        claim_id=str(claim["id"]),
        source_type="imported_resume",
        source_ref=f"sha256:{'a' * 64}",
        source_text="Synthetic selected evidence",
        extraction_method="grounded-apply.profile-import.manifest@1",
        created_at=IMPORT_TIME,
    )
    review_item = repository.add_profile_import_review_item(
        import_workflow_run_id=str(workflow["id"]),
        proposal_index=0,
        claim_id=str(claim["id"]),
        evidence_id=str(evidence["id"]),
        record_sha256="b" * 64,
        created_at=IMPORT_TIME,
    )
    return workflow, claim, evidence, review_item


class SQLiteRepositoryTests(unittest.TestCase):
    def test_data_access_requires_explicit_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "profile.db")
            self.addCleanup(repository.close)

            with self.assertRaises(RepositoryNotInitializedError):
                repository.list_claims()

    def test_profile_file_is_private_and_claim_graph_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "profile.db"
            with SQLiteRepository(database) as repository:
                source = repository.add_claim(
                    claim_id="employment-start",
                    claim_type="employment_date",
                    value="2021-08-01",
                    canonical_text="Started at Example Robotics in August 2021",
                    status="verified",
                    approval_status="approved",
                    sensitivity="personal",
                    source_type="user_statement",
                    source_ref="synthetic onboarding answer",
                    verified_at="2026-08-11T12:00:00Z",
                )
                evidence = repository.add_evidence(
                    evidence_id="resume-line",
                    source_type="imported_resume",
                    source_ref="synthetic-resume.txt#line=4",
                    source_text="Example Robotics — August 2021 to present",
                    confirmation_status="confirmed",
                    claim_id=str(source["id"]),
                )
                derived = repository.add_claim(
                    claim_id="experience-total",
                    claim_type="experience_years",
                    value=5,
                    canonical_text="Five years of relevant experience",
                    status="derived",
                    approval_status="approved",
                    sensitivity="personal",
                    source_type="derivation",
                    source_ref="dated-experience-total@1",
                    derivation_rule_name="dated-experience-total",
                    derivation_rule_version="1",
                    derivation_input_claim_ids=(str(source["id"]),),
                    derivation_staleness_policy="inputs_effective_window",
                )

                self.assertEqual(
                    repository.list_derivation_input_ids(str(derived["id"])),
                    [str(source["id"])],
                )
                self.assertEqual(
                    repository.list_evidence(claim_id=str(source["id"]))[0]["id"],
                    evidence["id"],
                )
                self.assertEqual(len(repository.list_claims()), 2)

            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)

    def test_link_failure_rolls_back_new_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "profile.db") as repository:
                with self.assertRaises(sqlite3.IntegrityError):
                    repository.add_evidence(
                        evidence_id="orphan",
                        source_type="user_statement",
                        source_ref="synthetic answer",
                        source_text="Synthetic evidence",
                        claim_id="missing-claim",
                    )

                self.assertIsNone(repository.get_evidence("orphan"))

    def test_repository_does_not_invent_a_verification_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "profile.db") as repository:
                with self.assertRaisesRegex(ValueError, "explicit verified_at"):
                    repository.add_claim(
                        claim_type="skill_use",
                        value="Python",
                        canonical_text="Used Python",
                        status="verified",
                        approval_status="approved",
                        source_type="user_statement",
                        source_ref="synthetic answer",
                    )

    def test_workflow_idempotency_key_is_safe_to_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "profile.db") as repository:
                first = repository.add_workflow_run(
                    workflow_type="profile_import",
                    idempotency_key="synthetic-import-1",
                    input_hash_sha256="a" * 64,
                    input_data={"artifact_id": "synthetic-resume"},
                )
                retry = repository.add_workflow_run(
                    workflow_type="profile_import",
                    idempotency_key="synthetic-import-1",
                    input_hash_sha256="a" * 64,
                    input_data={"artifact_id": "synthetic-resume"},
                )

                self.assertEqual(retry["id"], first["id"])
                with self.assertRaises(RepositoryError):
                    repository.add_workflow_run(
                        workflow_type="profile_import",
                        idempotency_key="synthetic-import-1",
                        input_hash_sha256="b" * 64,
                        input_data={"artifact_id": "different"},
                    )

    def test_read_only_repository_reads_without_changing_the_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "profile.db"
            with SQLiteRepository(database) as repository:
                repository.add_claim(
                    claim_id="pending-synthetic-claim",
                    claim_type="skill_use",
                    value="Python",
                    canonical_text="Used Python in a fictional project",
                    source_type="user_statement",
                    source_ref="synthetic answer",
                )
            before_bytes = database.read_bytes()
            before_stat = database.stat()
            before_paths = set(Path(directory).iterdir())

            with SQLiteRepository(database, read_only=True) as repository:
                self.assertEqual(len(repository.list_claims()), 1)
                with self.assertRaisesRegex(RepositoryError, "read-only"):
                    repository.add_claim(
                        claim_type="skill_use",
                        value="Rust",
                        canonical_text="Used Rust",
                        source_type="user_statement",
                        source_ref="synthetic answer",
                    )

            after_stat = database.stat()
            self.assertEqual(database.read_bytes(), before_bytes)
            self.assertEqual(stat.S_IMODE(after_stat.st_mode), stat.S_IMODE(before_stat.st_mode))
            self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
            self.assertEqual(set(Path(directory).iterdir()), before_paths)

    def test_read_only_repository_does_not_create_or_migrate_a_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.db"

            with self.assertRaises(sqlite3.OperationalError):
                SQLiteRepository(missing, read_only=True)
            self.assertFalse(missing.exists())

            empty = Path(directory) / "empty.db"
            sqlite3.connect(empty).close()
            empty.chmod(0o600)
            before = empty.read_bytes()
            repository = SQLiteRepository(empty, read_only=True)
            self.addCleanup(repository.close)
            with self.assertRaises(SchemaError):
                repository.initialize()
            self.assertEqual(empty.read_bytes(), before)

    def test_existing_only_repository_does_not_create_or_migrate_a_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.db"

            with self.assertRaises(sqlite3.OperationalError):
                SQLiteRepository(missing, existing_only=True)
            self.assertFalse(missing.exists())

            empty = Path(directory) / "empty.db"
            sqlite3.connect(empty).close()
            empty.chmod(0o600)
            before = empty.read_bytes()
            repository = SQLiteRepository(empty, existing_only=True)
            try:
                with self.assertRaises(SchemaError):
                    repository.initialize()
            finally:
                repository.close()

            self.assertEqual(empty.read_bytes(), before)
            with closing(sqlite3.connect(empty)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM sqlite_schema WHERE type = 'table'"
                    ).fetchone()[0],
                    0,
                )

    def test_existing_only_repository_is_writable_on_a_current_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "profile.db"
            with SQLiteRepository(database):
                pass

            with SQLiteRepository(database, existing_only=True) as repository:
                created = repository.add_claim(
                    claim_id="existing-only-synthetic-claim",
                    claim_type="skill_use",
                    value="Python",
                    canonical_text="Used Python in a fictional project",
                    source_type="user_statement",
                    source_ref="synthetic answer",
                )

            with SQLiteRepository(database, existing_only=True) as repository:
                stored = repository.get_claim(str(created["id"]))

            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored["canonical_text"], created["canonical_text"])

    def test_profile_import_review_association_is_unique_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "profile.db") as repository:
                workflow, claim, evidence, first = add_import_review_fixture(
                    repository,
                    suffix="association",
                )

                with self.assertRaisesRegex(ValueError, "lowercase SHA-256"):
                    repository.add_profile_import_review_item(
                        import_workflow_run_id=str(workflow["id"]),
                        proposal_index=0,
                        claim_id=str(claim["id"]),
                        evidence_id=str(evidence["id"]),
                        record_sha256="B" * 64,
                    )

                replay = repository.add_profile_import_review_item(
                    import_workflow_run_id=str(workflow["id"]),
                    proposal_index=0,
                    claim_id=str(claim["id"]),
                    evidence_id=str(evidence["id"]),
                    record_sha256="b" * 64,
                    created_at=DECISION_TIME,
                )

                self.assertEqual(replay, first)
                self.assertEqual(first["record_sha256"], "b" * 64)
                self.assertIsNone(first["decision"])
                self.assertEqual(first["updated_at"], first["created_at"])
                self.assertEqual(claim["updated_at"], claim["created_at"])
                self.assertEqual(evidence["updated_at"], evidence["created_at"])
                self.assertEqual(
                    repository.get_profile_import_review_item(str(claim["id"])),
                    first,
                )
                self.assertEqual(
                    repository.list_profile_import_review_items(
                        import_workflow_run_id=str(workflow["id"])
                    ),
                    [first],
                )

                with self.assertRaisesRegex(RepositoryError, "another record"):
                    repository.add_profile_import_review_item(
                        import_workflow_run_id=str(workflow["id"]),
                        proposal_index=1,
                        claim_id=str(claim["id"]),
                        evidence_id=str(evidence["id"]),
                        record_sha256="c" * 64,
                    )

    def test_profile_import_review_approval_updates_one_audited_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "profile.db") as repository:
                _, claim, evidence, _ = add_import_review_fixture(
                    repository,
                    suffix="approval",
                )
                decision_workflow = repository.add_workflow_run(
                    run_id="decision-workflow-approval",
                    workflow_type="profile_import_review_decision",
                    status="running",
                    idempotency_key="decision-approval",
                    input_data={"claim_id": claim["id"], "decision": "approved"},
                    created_at=DECISION_TIME,
                )

                decided = repository.decide_profile_import_review_item(
                    str(claim["id"]),
                    decision="approved",
                    decision_workflow_run_id=str(decision_workflow["id"]),
                    decided_by="synthetic-user",
                    decided_at=DECISION_TIME,
                )
                replay = repository.decide_profile_import_review_item(
                    str(claim["id"]),
                    decision="approved",
                    decision_workflow_run_id=str(decision_workflow["id"]),
                    decided_by="synthetic-user",
                    decided_at=DECISION_TIME,
                )

                stored_claim = repository.get_claim(str(claim["id"]))
                stored_evidence = repository.get_evidence(str(evidence["id"]))
                self.assertEqual(replay, decided)
                self.assertEqual(decided["decision"], "approved")
                self.assertEqual(decided["decided_by"], "synthetic-user")
                self.assertEqual(decided["decided_at"], DECISION_TIME)
                self.assertEqual(decided["updated_at"], DECISION_TIME)
                self.assertEqual(
                    repository.list_profile_import_review_items(decision="approved"),
                    [decided],
                )
                assert stored_claim is not None and stored_evidence is not None
                self.assertEqual(
                    (
                        stored_claim["status"],
                        stored_claim["approval_status"],
                        stored_claim["verified_at"],
                        stored_claim["verified_by"],
                    ),
                    ("verified", "approved", DECISION_TIME, "synthetic-user"),
                )
                self.assertEqual(stored_claim["updated_at"], DECISION_TIME)
                self.assertEqual(
                    (
                        stored_evidence["confirmation_status"],
                        stored_evidence["confirmed_at"],
                        stored_evidence["confirmed_by"],
                    ),
                    ("confirmed", DECISION_TIME, "synthetic-user"),
                )
                self.assertEqual(stored_evidence["updated_at"], DECISION_TIME)
                with self.assertRaisesRegex(RepositoryError, "already decided"):
                    repository.decide_profile_import_review_item(
                        str(claim["id"]),
                        decision="approved",
                        decision_workflow_run_id=str(decision_workflow["id"]),
                        decided_by="synthetic-user",
                        decided_at="2026-08-11T14:00:00Z",
                    )

    def test_profile_import_review_rejection_preserves_unverified_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "profile.db") as repository:
                _, claim, evidence, _ = add_import_review_fixture(
                    repository,
                    suffix="rejection",
                )
                decision_workflow = repository.add_workflow_run(
                    run_id="decision-workflow-rejection",
                    workflow_type="profile_import_review_decision",
                    status="running",
                    idempotency_key="decision-rejection",
                    input_data={"claim_id": claim["id"], "decision": "rejected"},
                    created_at=DECISION_TIME,
                )

                decided = repository.decide_profile_import_review_item(
                    str(claim["id"]),
                    decision="rejected",
                    decision_workflow_run_id=str(decision_workflow["id"]),
                    decided_by="synthetic-user",
                    decided_at=DECISION_TIME,
                )

                stored_claim = repository.get_claim(str(claim["id"]))
                stored_evidence = repository.get_evidence(str(evidence["id"]))
                self.assertEqual(decided["decision"], "rejected")
                self.assertEqual(decided["updated_at"], DECISION_TIME)
                assert stored_claim is not None and stored_evidence is not None
                self.assertEqual(
                    (
                        stored_claim["status"],
                        stored_claim["approval_status"],
                        stored_claim["verified_at"],
                        stored_claim["verified_by"],
                    ),
                    ("withdrawn", "rejected", None, None),
                )
                self.assertEqual(stored_claim["updated_at"], DECISION_TIME)
                self.assertEqual(
                    (
                        stored_evidence["confirmation_status"],
                        stored_evidence["confirmed_at"],
                        stored_evidence["confirmed_by"],
                    ),
                    ("rejected", None, None),
                )
                self.assertEqual(stored_evidence["updated_at"], DECISION_TIME)

    def test_profile_import_review_transition_rolls_back_without_decision_workflow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "profile.db") as repository:
                _, claim, evidence, pending = add_import_review_fixture(
                    repository,
                    suffix="rollback",
                )

                with self.assertRaises(sqlite3.IntegrityError):
                    repository.decide_profile_import_review_item(
                        str(claim["id"]),
                        decision="approved",
                        decision_workflow_run_id="missing-decision-workflow",
                        decided_by="synthetic-user",
                        decided_at=DECISION_TIME,
                    )

                stored_claim = repository.get_claim(str(claim["id"]))
                stored_evidence = repository.get_evidence(str(evidence["id"]))
                self.assertEqual(
                    repository.get_profile_import_review_item(str(claim["id"])),
                    pending,
                )
                assert stored_claim is not None and stored_evidence is not None
                self.assertEqual(stored_claim["status"], "needs_review")
                self.assertEqual(stored_claim["approval_status"], "pending")
                self.assertEqual(stored_evidence["confirmation_status"], "pending")

    def test_profile_import_review_rejects_touched_pending_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with SQLiteRepository(Path(directory) / "profile.db") as repository:
                for table in ("claims", "evidence"):
                    with self.subTest(table=table):
                        _, claim, evidence, pending = add_import_review_fixture(
                            repository,
                            suffix=f"touched-{table}",
                        )
                        decision_workflow = repository.add_workflow_run(
                            run_id=f"decision-workflow-touched-{table}",
                            workflow_type="profile_import_review_decision",
                            status="running",
                            input_data={"claim_id": claim["id"]},
                            created_at=DECISION_TIME,
                        )
                        target_id = claim["id"] if table == "claims" else evidence["id"]
                        with closing(sqlite3.connect(repository.database)) as connection:
                            connection.execute(
                                f"UPDATE {table} SET updated_at = ? WHERE id = ?",
                                ("2026-08-11T12:30:00Z", target_id),
                            )
                            connection.commit()

                        with self.assertRaisesRegex(
                            RepositoryError,
                            "not pending review",
                        ):
                            repository.decide_profile_import_review_item(
                                str(claim["id"]),
                                decision="approved",
                                decision_workflow_run_id=str(decision_workflow["id"]),
                                decided_by="synthetic-user",
                                decided_at=DECISION_TIME,
                            )

                        self.assertEqual(
                            repository.get_profile_import_review_item(str(claim["id"])),
                            pending,
                        )


if __name__ == "__main__":
    unittest.main()
