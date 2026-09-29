from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import ApprovalStatus, ClaimStatus, SourceType, to_jsonable
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.profile import (
    CreateClaim,
    CreateEvidence,
    CreateImportProposal,
    CreateProfileReviewDecision,
    ProfileReviewSelection,
    ProfileService,
    ProposedImportClaim,
    TextSourceSpan,
    validate_profile_review_selection_request,
)


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


class ProfileReviewSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fictional-profile.db"
        self.repository = SQLiteRepository(self.path).initialize()
        self.addCleanup(self.repository.close)
        self.service = ProfileService(self.repository)

    def import_facts(
        self, label: str, count: int = 3, *,
        source_type: SourceType = SourceType.IMPORTED_RESUME,
    ) -> tuple[str, ...]:
        lines = [f"Fictional {label} skill {index}." for index in range(count)]
        text = "\n".join(lines)
        proposals = []
        start = 0
        for line in lines:
            proposals.append(ProposedImportClaim(
                claim_type="skill_use", value=line, canonical_text=line,
                span=TextSourceSpan(start=start, end=start + len(line), text=line),
            ))
            start += len(line) + 1
        result = self.service.create_import_proposal(CreateImportProposal(
            idempotency_key=f"fictional-{label}", source_text=text,
            expected_source_sha256=hashlib.sha256(text.encode()).hexdigest(),
            proposals=tuple(proposals), content_policy_version=3,
            source_type=source_type,
        ), now=NOW)
        return tuple(claim.id for claim in result.claims)

    def generic(self, claim_id: str, *, approved: bool = False) -> str:
        claim = self.service.create_claim(CreateClaim(
            claim_id=claim_id, claim_type="skill_use", value="Fictional manual skill",
            canonical_text="Used a fictional manual skill.",
            source_type=SourceType.USER_STATEMENT,
            source_ref="user-statement://fictional/review",
            status=ClaimStatus.VERIFIED if approved else ClaimStatus.NEEDS_REVIEW,
            approval_status=ApprovalStatus.APPROVED if approved else ApprovalStatus.PENDING,
            verified_by="fictional-reviewer" if approved else None,
        ), now=NOW)
        return claim.id

    def decide(self, claim_id: str, decision: ApprovalStatus) -> None:
        item = self.service.get_review_item(claim_id).item
        assert item.review_token is not None
        self.service.decide_review_item(CreateProfileReviewDecision(
            claim_id=claim_id, review_token=item.review_token, decision=decision,
            actor_id="fictional-reviewer", idempotency_key=f"fictional-{decision.value}-{claim_id}",
        ), now=NOW + timedelta(days=1))

    def test_selects_exact_managed_item_and_token_without_unrelated_evidence(self) -> None:
        ids = self.import_facts("resume")
        self.import_facts("statement", source_type=SourceType.USER_STATEMENT)
        generic = self.generic("generic-fictional")
        all_items = self.service.list_review_items()
        before = self.path.read_bytes()
        for expected in all_items:
            with self.subTest(source_type=expected.claim.source_type, claim_id=expected.claim.id):
                selected = self.service.get_review_item(expected.claim.id)
                self.assertEqual(selected.item, expected)
                self.assertEqual(selected.pending_count, 7)
                self.assertEqual(set(to_jsonable(selected)), {"item", "pending_count"})
                self.assertEqual(to_jsonable(selected)["item"], to_jsonable(expected))
                self.assertEqual(selected.item.content_trust, "untrusted")
                self.assertFalse(selected.item.usable)
                if expected.claim.id == generic:
                    self.assertIsNone(selected.item.review_token)
                else:
                    self.assertIsNotNone(selected.item.review_token)
        serialized = str(to_jsonable(self.service.get_review_item(ids[1])))
        self.assertNotIn("Fictional resume skill 0.", serialized)
        self.assertNotIn("Fictional statement skill", serialized)
        self.assertEqual(self.service.list_review_items(), all_items)
        self.assertEqual(self.path.read_bytes(), before)

    def test_selection_does_not_change_default_or_paged_review_contract(self) -> None:
        ids = self.import_facts("compatibility", 4)
        self.generic("generic-fictional")
        full = to_jsonable(self.service.list_review_items())
        first = to_jsonable(self.service.list_review_page(limit=2))
        later = to_jsonable(self.service.list_review_page(limit=2, after_claim_id=ids[1]))
        selected = self.service.get_review_item(ids[-1])
        self.assertEqual(selected.item.claim.id, ids[-1])
        self.assertEqual(to_jsonable(self.service.list_review_items()), full)
        self.assertEqual(to_jsonable(self.service.list_review_page(limit=2)), first)
        self.assertEqual(to_jsonable(self.service.list_review_page(limit=2, after_claim_id=ids[1])), later)

    def test_empty_missing_and_nonpending_ids_share_fixed_private_refusal(self) -> None:
        expected_error = "Requested claim is not pending review."
        unknown = "PRIVATE_UNKNOWN_ID\n\x00"
        with self.assertRaises(ValueError) as empty:
            self.service.get_review_item(unknown)
        self.assertEqual(str(empty.exception), expected_error)
        ids = self.import_facts("decided")
        self.decide(ids[0], ApprovalStatus.APPROVED)
        self.decide(ids[1], ApprovalStatus.REJECTED)
        approved_generic = self.generic("generic-approved", approved=True)
        before = self.path.read_bytes()
        for claim_id in (unknown, ids[0], ids[1], approved_generic):
            with self.subTest(claim_id=claim_id), self.assertRaises(ValueError) as caught:
                self.service.get_review_item(claim_id)
            self.assertEqual(str(caught.exception), expected_error)
            self.assertNotIn(claim_id, str(caught.exception))
        self.assertEqual(self.service.get_review_item(ids[2]).pending_count, 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_request_validation_is_pure_and_precedes_any_repository_read(self) -> None:
        invalid = (None, True, False, 1, 1.0, [], {}, b"claim", "", " \t\n",
                   "\ud800", "PRIVATE_ID\udfff")
        for claim_id in invalid:
            with self.subTest(claim_id=claim_id):
                with patch.object(self.repository, "read_transaction") as read, \
                     patch.object(self.service, "list_review_items") as queue:
                    with self.assertRaises(ValueError) as caught:
                        validate_profile_review_selection_request(claim_id=claim_id)
                    self.assertNotIn("PRIVATE_ID", str(caught.exception))
                    self.assertNotIn("\udfff", str(caught.exception))
                    with self.assertRaises(ValueError):
                        self.service.get_review_item(claim_id)
                    read.assert_not_called()
                    queue.assert_not_called()
        class StringSubclass(str):
            pass
        with self.assertRaises(ValueError):
            validate_profile_review_selection_request(claim_id=StringSubclass("claim"))

    def test_all_legacy_ids_remain_exact_including_control_nul_and_long_ids(self) -> None:
        ids = ("generic/fictional", "候选人-fictional", "generic with spaces", " leading trailing ",
               "-leading-dash", "generic'\";$()", "generic\ncontrol\x1b[2J", "generic\x00nul",
               "generic\u2028separator\u2029", "x" * 300)
        for claim_id in ids:
            self.assertIsNone(validate_profile_review_selection_request(claim_id=claim_id))
            self.generic(claim_id)
        for claim_id in ids:
            selected = self.service.get_review_item(claim_id)
            self.assertEqual(selected.item.claim.id, claim_id)
            self.assertIsNone(selected.item.review_token)
            self.assertEqual(selected.pending_count, len(ids))
        with self.assertRaisesRegex(ValueError, "not pending review"):
            self.service.get_review_item("leading trailing")

    def test_corrupt_requested_or_unrelated_import_fails_before_selection(self) -> None:
        ids = self.import_facts("corruption", 4)
        for corrupt_id in (ids[0], ids[-1]):
            with self.subTest(corrupt_id=corrupt_id):
                self.repository._connection.execute("BEGIN")
                try:
                    # Deliberately corrupt a fictional row to test the complete queue audit.
                    self.repository._connection.execute(
                        "UPDATE claims SET canonical_text=? WHERE id=?",
                        ("PRIVATE_CORRUPTED_MARKER", corrupt_id),
                    )
                    with self.assertRaises(RepositoryError) as caught:
                        self.service.get_review_item(ids[0])
                    self.assertNotIn("PRIVATE_CORRUPTED_MARKER", str(caught.exception))
                    with self.assertRaises(RepositoryError):
                        self.service.get_review_item("missing-fictional")
                finally:
                    self.repository._connection.rollback()

    def test_unrelated_statement_association_loss_cannot_escape_as_generic(self) -> None:
        resume = self.import_facts("valid-resume", 1)
        statement = self.import_facts("unrelated-statement", 1, source_type=SourceType.USER_STATEMENT)
        self.repository._connection.execute(
            "DELETE FROM profile_import_review_items WHERE claim_id=?", (statement[0],),
        )
        with self.assertRaises(RepositoryError):
            self.service.get_review_item(resume[0])

    def test_unrelated_generic_evidence_is_validated(self) -> None:
        selected = self.import_facts("managed", 1)[0]
        unrelated = self.generic("generic-unrelated")
        evidence = self.service.create_evidence(CreateEvidence(
            claim_id=unrelated, source_type=SourceType.USER_STATEMENT,
            source_ref="user-statement://fictional/evidence",
            source_text="Fictional supporting text.",
        ), now=NOW)
        self.repository._connection.execute(
            "UPDATE evidence SET captured_at=? WHERE id=?",
            ("PRIVATE_INVALID_TIMESTAMP", evidence.id),
        )
        with self.assertRaises(ValueError) as caught:
            self.service.get_review_item(selected)
        self.assertNotIn("PRIVATE_INVALID_TIMESTAMP", str(caught.exception))

    def test_one_read_snapshot_covers_the_whole_queue_and_has_no_writes(self) -> None:
        ids = self.import_facts("snapshot")
        statements: list[str] = []
        self.repository._connection.set_trace_callback(statements.append)
        try:
            with patch.object(self.repository, "read_transaction", wraps=self.repository.read_transaction) as read, \
                 patch.object(self.service, "list_review_items", wraps=self.service.list_review_items) as queue:
                selected = self.service.get_review_item(ids[-1])
            read.assert_called_once_with()
            queue.assert_called_once_with()
        finally:
            self.repository._connection.set_trace_callback(None)
        self.assertEqual(selected.pending_count, 3)
        first_select = next(index for index, statement in enumerate(statements)
                            if statement.lstrip().startswith("SELECT"))
        self.assertLess(statements.index("BEGIN"), first_select)
        self.assertEqual(statements[-1], "ROLLBACK")
        self.assertEqual(statements.count("BEGIN"), 1)
        self.assertTrue(all(statement == "PRAGMA user_version"
                            or statement.lstrip().startswith(("SELECT", "BEGIN", "ROLLBACK"))
                            for statement in statements))
        self.assertFalse(self.repository._connection.in_transaction)

    def test_caller_owned_transaction_survives_success_missing_and_corruption(self) -> None:
        ids = self.import_facts("existing")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "Roll back fictional setup"):
            with self.repository.transaction():
                claim_id = self.generic("generic-uncommitted")
                self.assertEqual(self.service.get_review_item(claim_id).pending_count, 4)
                self.assertTrue(self.repository._connection.in_transaction)
                with self.assertRaises(ValueError):
                    self.service.get_review_item("missing-fictional")
                self.assertTrue(self.repository._connection.in_transaction)
                self.repository._connection.execute(
                    "UPDATE claims SET canonical_text=? WHERE id=?", ("Fictional corrupted text.", ids[-1]),
                )
                with self.assertRaises(RepositoryError):
                    self.service.get_review_item(claim_id)
                self.assertTrue(self.repository._connection.in_transaction)
                raise RuntimeError("Roll back fictional setup")
        self.assertIsNone(self.repository.get_claim("generic-uncommitted"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.service.get_review_item(ids[-1]).pending_count, 3)

    def test_read_only_adapter_preserves_bytes_metadata_and_sidecars_on_success_and_error(self) -> None:
        ids = self.import_facts("read-only")
        self.decide(ids[0], ApprovalStatus.REJECTED)
        before = self.path.read_bytes()
        stat_before = self.path.stat()
        names_before = sorted(path.name for path in self.path.parent.iterdir())
        with SQLiteRepository(self.path, read_only=True).initialize() as repository:
            service = ProfileService(repository)
            self.assertEqual(service.get_review_item(ids[-1]).pending_count, 2)
            for claim_id in (ids[0], "missing-fictional"):
                with self.assertRaises(ValueError):
                    service.get_review_item(claim_id)
        stat_after = self.path.stat()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual((stat_after.st_ino, stat_after.st_size, stat_after.st_mtime_ns, stat_after.st_mode),
                         (stat_before.st_ino, stat_before.st_size, stat_before.st_mtime_ns, stat_before.st_mode))
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()), names_before)

    def test_result_is_frozen_and_rejects_invalid_item_or_nonpositive_noninteger_count(self) -> None:
        selected = self.service.get_review_item(self.import_facts("result", 1)[0])
        for pending_count in (True, False, 0, -1, 1.0, "1", None):
            with self.subTest(pending_count=pending_count), self.assertRaises(ValueError):
                replace(selected, pending_count=pending_count)
        for item in (None, (), [], selected.item.claim, to_jsonable(selected.item)):
            with self.subTest(item_type=type(item)), self.assertRaises(ValueError):
                ProfileReviewSelection(item=item, pending_count=1)
        with self.assertRaises(FrozenInstanceError):
            selected.pending_count = 99

    def test_interrupts_propagate_and_owned_read_transaction_is_closed(self) -> None:
        claim_id = self.import_facts("interrupt", 1)[0]
        for interruption in (KeyboardInterrupt, SystemExit):
            with self.subTest(interruption=interruption):
                with patch.object(self.service, "list_review_items", side_effect=interruption):
                    with self.assertRaises(interruption):
                        self.service.get_review_item(claim_id)
                self.assertFalse(self.repository._connection.in_transaction)


if __name__ == "__main__":
    unittest.main()
