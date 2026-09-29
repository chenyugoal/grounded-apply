from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import (
    ApprovalStatus, ClaimStatus, ClaimUsePolicy, NeedInfo, Resolved, Sensitivity,
    SourceType, to_jsonable,
)
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.profile import (
    CreateClaim, CreateEvidence, CreateImportProposal, CreateProfileReviewDecision,
    ProfileService, ProposedImportClaim, TextSourceSpan, _import_request_sha256,
    _import_workflow_input, _profile_import_record_sha256_for_proposal,
)
from grounded_apply.services.workflow import canonical, digest
from tests.test_profile_service import NOW, REVIEW_NOW


FACTS = (
    ("education", "PhD in Computer Science, Fictional University, expected May 2027."),
    ("research_description", "Contributed Python error analysis for a fictional laboratory prototype; did not lead the study."),
    ("publication", "Quill, A. Fictional Paper Kites; submitted September 2026, not accepted."),
)


class _RollbackCorruption(Exception):
    pass


def statement_request(*, key: str = "fictional-statement-intake", facts=FACTS,
                      source_type: SourceType = SourceType.USER_STATEMENT) -> CreateImportProposal:
    source = "\n".join(text for _, text in facts) + "\n"
    position = 0
    proposals = []
    for kind, text in facts:
        proposals.append(ProposedImportClaim(claim_type=kind, value=text, canonical_text=text,
            span=TextSourceSpan(start=position, end=position + len(text), text=text)))
        position += len(text) + 1
    return CreateImportProposal(idempotency_key=key, source_text=source,
        expected_source_sha256=sha256(source.encode()).hexdigest(), proposals=tuple(proposals),
        content_policy_version=3, source_type=source_type)


class StatementImportTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="gapply-statement-test-")
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "fictional.db"
        self.repository = SQLiteRepository(self.database).initialize()
        self.addCleanup(self.repository.close)
        self.service = ProfileService(self.repository)
        self.policy = ClaimUsePolicy(as_of=REVIEW_NOW + timedelta(days=1),
            allowed_sensitivities=frozenset({Sensitivity.PERSONAL}), require_confirmed_evidence=True)

    def decide(self, item, decision: ApprovalStatus = ApprovalStatus.APPROVED):
        request = CreateProfileReviewDecision(claim_id=item.claim.id, review_token=item.review_token,
            decision=decision, actor_id="fictional-user", idempotency_key="decision-" + item.claim.id)
        return request, self.service.decide_review_item(request, now=REVIEW_NOW)

    @contextmanager
    def corruption(self):
        try:
            with self.repository.transaction():
                yield self.repository._connection
                raise _RollbackCorruption
        except _RollbackCorruption:
            pass

    def assert_read_paths_fail(self, claim_id: str) -> None:
        before = self.repository._connection.serialize()
        for operation in (
            self.service.validated_profile,
            lambda: self.service.packet_for_claim(claim_id, policy=self.policy),
            lambda: self.service.resolve(intent="research_description", policy=self.policy),
        ):
            with self.assertRaises(RepositoryError):
                operation()
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_exact_statement_intake_stays_pending_until_individual_decisions_and_replays(self) -> None:
        request = statement_request()
        before = self.database.read_bytes()
        preview = self.service.preview_import_proposal(request)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(set(to_jsonable(preview)), {"source_sha256", "source_ref", "source_artifact_id",
            "extractor_id", "proposal_count", "planned_claim_count", "planned_evidence_count", "review_required"})
        imported = self.service.create_import_proposal(request, now=NOW)
        self.assertTrue(imported.review_required)
        self.assertEqual([c.canonical_text for c in imported.claims], [text for _, text in FACTS])
        self.assertTrue(all(c.source_type is SourceType.USER_STATEMENT for c in imported.claims))
        self.assertTrue(all(e.source_type is SourceType.USER_STATEMENT for e in imported.evidence))
        self.assertEqual([e.source_text for e in imported.evidence], [text for _, text in FACTS])
        self.assertTrue(all(isinstance(self.service.packet_for_claim(c.id, policy=self.policy), NeedInfo)
                            for c in imported.claims))
        workflow = self.repository.get_workflow_run(imported.workflow_run_id)
        payload = json.loads(workflow["input_json"])
        self.assertEqual(tuple(payload[k] for k in ("request_schema_version", "source_identity_schema_version",
                                                  "record_digest_schema_version")), (5, 2, 2))
        self.assertEqual(payload["source_type"], "user_statement")
        self.assertNotIn(FACTS[0][1], workflow["input_json"])
        artifact = self.repository.get_artifact(imported.source_artifact_id)
        self.assertIsNone(artifact["local_path"])
        self.assertIsNone(artifact["original_name"])
        self.assertEqual(json.loads(artifact["metadata_json"])["retention"], "digest_only")
        items = self.service.list_review_items()
        approved_request, approved = self.decide(items[0])
        rejected_request, rejected = self.decide(items[2], ApprovalStatus.REJECTED)
        self.assertEqual(self.service.list_review_items()[0].claim.id, items[1].claim.id)
        self.assertIsInstance(self.service.packet_for_claim(approved.claim.id, policy=self.policy), Resolved)
        self.assertIsInstance(self.service.packet_for_claim(rejected.claim.id, policy=self.policy), NeedInfo)
        replay = self.service.create_import_proposal(request)
        self.assertEqual([c.approval_status for c in replay.claims],
                         [ApprovalStatus.APPROVED, ApprovalStatus.PENDING, ApprovalStatus.REJECTED])
        self.assertEqual(self.service.decide_review_item(approved_request), approved)
        self.assertEqual(self.service.decide_review_item(rejected_request), rejected)
        self.assertEqual(len(self.repository.list_claims()), 3)
        approval_payload = json.loads(self.repository.get_workflow_run(approved.decision_workflow_run_id)["input_json"])
        self.assertEqual(approval_payload["record_digest_schema_version"], 2)

    def test_default_resume_hashes_and_output_fields_remain_frozen(self) -> None:
        text = FACTS[0][1]
        # Digests captured from the pre-increment service, with no statement field.
        request = CreateImportProposal(idempotency_key="fictional-statement-compatibility",
            source_text=text, expected_source_sha256=sha256(text.encode()).hexdigest(),
            proposals=(ProposedImportClaim(claim_type="education", value=text, canonical_text=text,
                span=TextSourceSpan(start=0, end=len(text), text=text)),), content_policy_version=3)
        record = _profile_import_record_sha256_for_proposal(request, request.proposals[0], text)
        payload = _import_workflow_input(request,
            idempotency_key_sha256=sha256(request.idempotency_key.encode()).hexdigest(), record_sha256s=(record,))
        self.assertEqual(record, "27d905d64dec657ded1be28bcfdc4cb12d967db769c11dac36f83df3df881ac5")
        self.assertEqual(_import_request_sha256(payload), "4bda4773cfb7e93ceff207cc38424b57f6eaac6ebd988c9f1f88a92d50497ffd")
        self.assertNotIn("source_type", payload)
        imported = self.service.create_import_proposal(request, now=NOW)
        result_fields = set(to_jsonable(imported))
        statement = self.service.create_import_proposal(replace(request,
            idempotency_key="fictional-statement-distinct", source_type=SourceType.USER_STATEMENT), now=NOW)
        self.assertEqual(result_fields, set(to_jsonable(statement)))
        self.assertNotIn("source_type", result_fields)
        self.assertNotEqual(imported.source_artifact_id, statement.source_artifact_id)
        self.assertEqual(self.service.create_import_proposal(request), imported)

    def test_origin_change_cannot_reuse_a_key_and_invalid_origins_fail_before_storage(self) -> None:
        request = statement_request()
        self.service.create_import_proposal(request, now=NOW)
        before = self.database.read_bytes()
        with self.assertRaises(RepositoryError):
            self.service.create_import_proposal(replace(request, source_type=SourceType.IMPORTED_RESUME))
        self.assertEqual(self.database.read_bytes(), before)
        for origin in ("user_statement", SourceType.OTHER, None, True):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                replace(request, source_type=origin)
        self.assertEqual(self.database.read_bytes(), before)

    def test_retention_policy_and_sensitive_restrictions_are_not_relaxed(self) -> None:
        request = statement_request(facts=(FACTS[0],))
        with patch.object(self.repository, "transaction", side_effect=AssertionError("storage entered")):
            with self.assertRaises(ValueError):
                self.service.create_import_proposal(replace(request, content_policy_version=2))
            sensitive = statement_request(facts=(("research_description", "Work authorization: yes"),))
            with self.assertRaises(ValueError):
                self.service.preview_import_proposal(sensitive)
            with self.assertRaises(ValueError):
                self.service.create_import_proposal(sensitive)
        self.assertEqual(self.repository.list_claims(), [])

    def test_statement_pages_resume_after_decisions_without_writes(self) -> None:
        self.service.create_import_proposal(statement_request(), now=NOW)
        first = self.service.list_review_page(limit=1)
        self.decide(first.items[0])
        before = self.database.read_bytes()
        with SQLiteRepository(self.database, read_only=True).initialize() as repository:
            service = ProfileService(repository)
            second = service.list_review_page(limit=1, after_claim_id=first.next_after)
            self.assertEqual(second.pending_count, 2)
            self.assertEqual(second.pending_before_count, 0)
            self.assertEqual(second.items[0].claim.canonical_text, FACTS[1][1])
            service.validated_profile()
        self.assertEqual(self.database.read_bytes(), before)
        self.assertFalse(any(Path(str(self.database) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")))

    def test_source_projection_and_closed_version_tuple_corruption_fail_without_writes(self) -> None:
        imported = self.service.create_import_proposal(statement_request(), now=NOW)
        claim_id = imported.claims[0].id
        workflow = self.repository.get_workflow_run(imported.workflow_run_id)
        original = json.loads(workflow["input_json"])
        for key, value in (("source_type", "imported_resume"), ("request_schema_version", 4),
                           ("source_identity_schema_version", 1), ("record_digest_schema_version", 1),
                           ("record_digest_schema_version", True)):
            with self.subTest(key=key, value=value), self.corruption() as connection:
                payload = dict(original, **{key: value})
                connection.execute("UPDATE workflow_runs SET input_json=?,input_hash_sha256=? WHERE id=?",
                                   (canonical(payload), digest(payload), imported.workflow_run_id))
                self.assert_read_paths_fail(claim_id)
                with self.assertRaises(RepositoryError):
                    self.service.list_review_page(limit=1)
        for table in ("claims", "evidence"):
            with self.subTest(table=table), self.corruption() as connection:
                connection.execute(f"UPDATE {table} SET source_type='imported_resume'")
                self.assert_read_paths_fail(claim_id)
        with self.corruption() as connection:
            connection.execute("UPDATE claim_evidence SET strength=0.5")
            self.assert_read_paths_fail(claim_id)
        artifact = self.repository.get_artifact(imported.source_artifact_id)
        metadata = json.loads(artifact["metadata_json"])
        metadata["source_identity_schema_version"] = 2.0
        with self.corruption() as connection:
            connection.execute("UPDATE artifacts SET metadata_json=? WHERE id=?",
                               (canonical(metadata), imported.source_artifact_id))
            self.assert_read_paths_fail(claim_id)
            with self.assertRaises(RepositoryError):
                self.service.create_import_proposal(statement_request())

    def test_missing_association_and_erased_markers_cannot_become_generic_statements(self) -> None:
        request = statement_request()
        imported = self.service.create_import_proposal(request, now=NOW)
        item = self.service.list_review_items()[0]
        decision, _ = self.decide(item)
        for erase_markers in (False, True):
            with self.subTest(erase_markers=erase_markers), self.corruption() as connection:
                connection.execute("DELETE FROM profile_import_review_items WHERE claim_id=?", (item.claim.id,))
                if erase_markers:
                    connection.execute("UPDATE claims SET source_ref='fictional-generic' WHERE id=?", (item.claim.id,))
                    connection.execute("UPDATE evidence SET source_ref='fictional-generic',artifact_id=NULL,extraction_method='manual' WHERE id=?", (item.evidence[0].id,))
                self.assert_read_paths_fail(item.claim.id)
                for operation in (self.service.list_review_items,
                    lambda: self.service.list_review_page(limit=1, after_claim_id=item.claim.id),
                    lambda: self.service.create_import_proposal(request),
                    lambda: self.service.decide_review_item(decision)):
                    with self.assertRaises(RepositoryError):
                        operation()
        self.assertEqual(len(self.service.validated_profile()[0]), len(imported.claims))

    def test_decided_anchor_and_replay_revalidate_origin_and_record_digest(self) -> None:
        request = statement_request()
        self.service.create_import_proposal(request, now=NOW)
        item = self.service.list_review_items()[0]
        decision, _ = self.decide(item)
        for mutation in ("origin", "wording"):
            with self.subTest(mutation=mutation), self.corruption() as connection:
                if mutation == "origin":
                    connection.execute("UPDATE claims SET source_type='other' WHERE id=?", (item.claim.id,))
                    connection.execute("UPDATE evidence SET source_type='other' WHERE id=?", (item.evidence[0].id,))
                else:
                    changed = FACTS[0][1].replace("expected", "awarded")
                    connection.execute("UPDATE claims SET value_json=?,canonical_text=? WHERE id=?",
                                       (canonical(changed), changed, item.claim.id))
                    connection.execute("UPDATE evidence SET source_text=?,checksum_sha256=? WHERE id=?",
                                       (changed, sha256(changed.encode()).hexdigest(), item.evidence[0].id))
                self.assert_read_paths_fail(item.claim.id)
                for operation in (
                    lambda: self.service.list_review_page(limit=1, after_claim_id=item.claim.id),
                    lambda: self.service.decide_review_item(decision),
                    lambda: self.service.create_import_proposal(request)):
                    with self.assertRaises(RepositoryError):
                        operation()

    def test_remaining_statement_evidence_cannot_use_legacy_pending_resume_exception(self) -> None:
        imported = self.service.create_import_proposal(statement_request(), now=NOW)
        claim = imported.claims[0]
        # Even loss of the workflow does not erase a surviving managed evidence
        # marker. This is not an unaudited legacy pending resume.
        with self.corruption() as connection:
            connection.execute("DELETE FROM profile_import_review_items WHERE import_workflow_run_id=?",
                               (imported.workflow_run_id,))
            connection.execute("DELETE FROM workflow_runs WHERE id=?", (imported.workflow_run_id,))
            connection.execute("UPDATE claims SET source_type='imported_resume',source_ref='fictional-legacy' WHERE id=?",
                               (claim.id,))
            self.assert_read_paths_fail(claim.id)
            with self.assertRaises(RepositoryError):
                self.service.list_review_items()

    def test_statement_decision_digest_version_requires_exact_integer(self) -> None:
        self.service.create_import_proposal(statement_request(), now=NOW)
        item = self.service.list_review_items()[0]
        decision, approved = self.decide(item)
        workflow = self.repository.get_workflow_run(approved.decision_workflow_run_id)
        payload = json.loads(workflow["input_json"])
        payload["record_digest_schema_version"] = 2.0
        with self.corruption() as connection:
            connection.execute("UPDATE workflow_runs SET input_json=?,input_hash_sha256=? WHERE id=?",
                               (canonical(payload), digest(payload), approved.decision_workflow_run_id))
            self.assert_read_paths_fail(item.claim.id)
            with self.assertRaises(RepositoryError):
                self.service.decide_review_item(decision)

    def test_reserved_statement_markers_are_not_generic_creation_inputs(self) -> None:
        generic = CreateClaim(claim_type="skill_use", value="Python", canonical_text="Python",
            source_type=SourceType.USER_STATEMENT, source_ref="user-statement://fictional/manual")
        claim = self.service.create_claim(generic, now=NOW)
        self.assertIsNone(self.service.list_review_items()[0].review_token)
        self.assertEqual(self.service.validated_profile()[0][0], claim)
        for marker in ("user-statement:sha256:malformed", "user-statement:sha256",
                       "profile-user-statement-source:malformed",
                       "grounded-apply.profile-user-statement.unregistered"):
            with self.subTest(marker=marker):
                with self.assertRaises(ValueError):
                    self.service.create_claim(replace(generic, source_ref=marker))
                evidence = CreateEvidence(claim_id=claim.id, source_type=SourceType.USER_STATEMENT,
                    source_ref=generic.source_ref, source_text="Python")
                for field in ("source_ref", "artifact_id", "extraction_method"):
                    with self.assertRaises(ValueError):
                        self.service.create_evidence(replace(evidence, **{field: marker}))
                with self.corruption() as connection:
                    connection.execute("UPDATE claims SET source_ref=? WHERE id=?", (marker, claim.id))
                    self.assert_read_paths_fail(claim.id)
                    with self.assertRaises(RepositoryError):
                        self.service.list_review_items()


if __name__ == "__main__":
    unittest.main()
