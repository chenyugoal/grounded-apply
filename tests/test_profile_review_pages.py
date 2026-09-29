from __future__ import annotations

import hashlib
import tempfile
import unittest
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grounded_apply.domain import ApprovalStatus, ClaimStatus, SourceType
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services import (
    CreateClaim, CreateEvidence, CreateImportProposal, CreateProfileReviewDecision,
    ProfileReviewPage, ProfileService, ProposedImportClaim, TextSourceSpan,
    validate_profile_review_page_request,
)


NOW = datetime(2026, 9, 21, 6, 0, tzinfo=UTC)


class ProfileReviewPageTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "fictional-profile.db"
        self.repository = SQLiteRepository(self.path).initialize()
        self.addCleanup(self.repository.close)
        self.service = ProfileService(self.repository)

    def import_facts(self, label: str, count: int, *, now: datetime = NOW,
                     workflow_number: int | None = None) -> tuple[str, ...]:
        lines = [f"Fictional {label} skill {index}." for index in range(count)]
        text = "\n".join(lines)
        proposals = []
        start = 0
        for line in lines:
            proposals.append(ProposedImportClaim(claim_type="skill_use", value=line,
                canonical_text=line, span=TextSourceSpan(start=start, end=start + len(line), text=line)))
            start += len(line) + 1
        request = CreateImportProposal(idempotency_key=f"fictional-{label}", source_text=text,
            expected_source_sha256=hashlib.sha256(text.encode()).hexdigest(),
            proposals=tuple(proposals), content_policy_version=3)
        if workflow_number is None:
            result = self.service.create_import_proposal(request, now=now)
        else:
            workflow_id = uuid.UUID(f"00000000-0000-4000-8000-{workflow_number:012d}")
            with patch("grounded_apply.services.profile.uuid.uuid4", return_value=workflow_id):
                result = self.service.create_import_proposal(request, now=now)
        return tuple(claim.id for claim in result.claims)

    def generic(self, claim_id: str, *, now: datetime = NOW,
                approved: bool = False) -> str:
        claim = self.service.create_claim(CreateClaim(claim_id=claim_id, claim_type="skill_use",
            value="Fictional manual skill", canonical_text="Used a fictional manual skill.",
            source_type=SourceType.USER_STATEMENT, source_ref="user-statement://fictional/review",
            status=ClaimStatus.VERIFIED if approved else ClaimStatus.NEEDS_REVIEW,
            approval_status=ApprovalStatus.APPROVED if approved else ApprovalStatus.PENDING,
            verified_by="fictional-reviewer" if approved else None), now=now)
        return claim.id

    def decide(self, claim_id: str, decision: ApprovalStatus) -> None:
        item = next(item for item in self.service.list_review_items() if item.claim.id == claim_id)
        assert item.review_token is not None
        self.service.decide_review_item(CreateProfileReviewDecision(claim_id=claim_id,
            review_token=item.review_token, decision=decision, actor_id="fictional-reviewer",
            idempotency_key=f"fictional-{decision.value}-{claim_id}"), now=NOW + timedelta(days=1))

    def assert_page(self, page: ProfileReviewPage, ids: tuple[str, ...], *, before: int,
                    after: int, total: int) -> None:
        self.assertEqual(tuple(item.claim.id for item in page.items), ids)
        self.assertEqual(page.returned_count, len(ids))
        self.assertEqual(page.pending_count, total)
        self.assertEqual(page.pending_before_count, before)
        self.assertEqual(page.pending_after_count, after)
        self.assertEqual(before + len(ids) + after, total)
        self.assertEqual(page.next_after, ids[-1] if after else None)

    def test_pages_preserve_full_review_order_and_tokens_without_changing_queue(self) -> None:
        ids = self.import_facts("first", 5)
        all_items = self.service.list_review_items()
        before = self.path.read_bytes()
        page = self.service.list_review_page(limit=2)
        self.assert_page(page, ids[:2], before=0, after=3, total=5)
        self.assertEqual(page.items, all_items[:2])
        middle = self.service.list_review_page(limit=2, after_claim_id=page.next_after)
        self.assert_page(middle, ids[2:4], before=2, after=1, total=5)
        last = self.service.list_review_page(limit=2, after_claim_id=middle.next_after)
        self.assert_page(last, ids[4:], before=4, after=0, total=5)
        self.assertEqual(self.service.list_review_items(), all_items)
        self.assertEqual(self.path.read_bytes(), before)

    def test_decided_anchor_and_earlier_decisions_do_not_skip_unseen_pending_items(self) -> None:
        for decision in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            with self.subTest(decision=decision):
                ids = self.import_facts(decision.value, 4)
                # Prior batch, if any, has no pending records by this point.
                first = self.service.list_review_page(limit=2)
                self.assertEqual(first.next_after, ids[1])
                self.decide(ids[0], decision)
                self.decide(ids[1], decision)
                page = self.service.list_review_page(limit=2, after_claim_id=first.next_after)
                self.assert_page(page, ids[2:], before=0, after=0, total=2)
                self.decide(ids[2], decision)
                self.decide(ids[3], decision)
                self.assert_page(self.service.list_review_page(limit=2, after_claim_id=ids[1]),
                    (), before=0, after=0, total=0)

    def test_skipped_earlier_facts_remain_counted_and_restart_returns_them(self) -> None:
        ids = self.import_facts("skipped", 5)
        self.decide(ids[1], ApprovalStatus.REJECTED)
        page = self.service.list_review_page(limit=2, after_claim_id=ids[1])
        self.assert_page(page, ids[2:4], before=1, after=1, total=4)
        restarted = self.service.list_review_page(limit=2)
        self.assert_page(restarted, (ids[0], ids[2]), before=0, after=2, total=4)
        terminal = self.service.list_review_page(limit=50, after_claim_id=ids[-1])
        self.assert_page(terminal, (), before=4, after=0, total=4)

    def test_equal_timestamp_imports_follow_workflow_then_proposal_order(self) -> None:
        later_workflow = self.import_facts("workflow-three", 2, workflow_number=3)
        first_workflow = self.import_facts("workflow-one", 2, workflow_number=1)
        middle_workflow = self.import_facts("workflow-two", 2, workflow_number=2)
        expected = first_workflow + middle_workflow + later_workflow
        self.assert_page(self.service.list_review_page(limit=50), expected, before=0, after=0, total=6)
        self.decide(middle_workflow[0], ApprovalStatus.APPROVED)
        self.assert_page(self.service.list_review_page(limit=2, after_claim_id=middle_workflow[0]),
            (middle_workflow[1], later_workflow[0]), before=2, after=1, total=5)

    def test_generic_records_follow_imports_then_created_descending_and_id(self) -> None:
        early = self.generic("generic-old", now=NOW - timedelta(days=2))
        same_z = self.generic("generic-z", now=NOW)
        same_a = self.generic("generic-a", now=NOW)
        newest = self.generic("generic-new", now=NOW + timedelta(days=2))
        imported = self.import_facts("imported", 2, now=NOW - timedelta(days=3))
        expected = imported + (newest, same_a, same_z, early)
        self.assertEqual(tuple(item.claim.id for item in self.service.list_review_items()), expected)
        self.assert_page(self.service.list_review_page(limit=2, after_claim_id=imported[-1]),
            (newest, same_a), before=2, after=2, total=6)
        self.assert_page(self.service.list_review_page(limit=2, after_claim_id=same_a),
            (same_z, early), before=4, after=0, total=6)
        decided = self.generic("generic-decided", now=NOW + timedelta(days=1), approved=True)
        self.assert_page(self.service.list_review_page(limit=2, after_claim_id=decided),
            (same_a, same_z), before=3, after=1, total=6)

    def test_new_earlier_and_later_imports_are_counted_relative_to_stable_anchor(self) -> None:
        initial = self.import_facts("initial", 3, now=NOW)
        first = self.service.list_review_page(limit=2)
        earlier = self.import_facts("earlier", 2, now=NOW - timedelta(days=1))
        later = self.import_facts("later", 2, now=NOW + timedelta(days=1))
        self.assert_page(self.service.list_review_page(limit=2, after_claim_id=first.next_after),
            (initial[2], later[0]), before=4, after=1, total=7)
        self.assert_page(self.service.list_review_page(limit=2), earlier, before=0, after=5, total=7)

    def test_generic_anchor_counts_new_imports_and_newer_generic_claims_before_it(self) -> None:
        anchor = self.generic("generic-anchor")
        older = self.generic("generic-older", now=NOW - timedelta(days=1))
        self.import_facts("new-import", 2, now=NOW + timedelta(days=1))
        self.generic("generic-newer", now=NOW + timedelta(days=1))
        later = self.generic("generic-last", now=NOW - timedelta(days=2))
        self.assert_page(self.service.list_review_page(limit=1, after_claim_id=anchor),
            (older,), before=4, after=1, total=6)
        self.assert_page(self.service.list_review_page(limit=1, after_claim_id=older),
            (later,), before=5, after=0, total=6)

    def test_empty_queue_and_unknown_anchor_are_distinct_and_do_not_echo_input(self) -> None:
        self.assert_page(self.service.list_review_page(limit=3), (), before=0, after=0, total=0)
        unknown = "PRIVATE_UNKNOWN_MARKER"
        with self.assertRaisesRegex(ValueError, "anchor claim does not exist") as caught:
            self.service.list_review_page(limit=3, after_claim_id=unknown)
        self.assertNotIn(unknown, str(caught.exception))

    def test_request_shape_is_pure_and_rejected_before_any_read(self) -> None:
        invalid = [dict(limit=value) for value in (True, False, 0, -1, 51, 1.0, "3", None)]
        invalid += [dict(limit=3, after_claim_id=value) for value in
            ("", " ", "\t\n", "\ud800", "private\udfff", True, 1, [])]
        for options in invalid:
            with self.subTest(options=options), patch.object(self.repository, "read_transaction") as read:
                with self.assertRaises(ValueError) as caught:
                    validate_profile_review_page_request(**options)
                self.assertNotIn("private", str(caught.exception))
                self.assertNotIn("\udfff", str(caught.exception))
                with self.assertRaises(ValueError):
                    self.service.list_review_page(**options)
                read.assert_not_called()
        for value in (None, "a", "Z" * 300, "claim._:-123", "generic/claim", "é", "claim with spaces", "claim\n"):
            self.assertIsNone(validate_profile_review_page_request(limit=1, after_claim_id=value))
            self.assertIsNone(validate_profile_review_page_request(limit=50, after_claim_id=value))

    def test_legacy_generic_ids_round_trip_as_exact_anchors_without_new_restrictions(self) -> None:
        legacy_ids = ("generic/fictional-first", "候选人-fictional", "generic with spaces", "-leading-dash",
                      "generic'\";$()", "generic\ncontrol\x1b[2J", "generic\x00nul", "x" * 300)
        oldest = self.generic("generic-oldest", now=NOW - timedelta(days=1))
        for index, claim_id in enumerate(legacy_ids):
            self.generic(claim_id, now=NOW + timedelta(seconds=index))
        expected = tuple(reversed(legacy_ids)) + (oldest,)
        self.assertEqual(tuple(item.claim.id for item in self.service.list_review_items()), expected)
        seen: list[str] = []
        page = self.service.list_review_page(limit=1)
        while True:
            seen.extend(item.claim.id for item in page.items)
            if page.next_after is None:
                break
            self.assertEqual(page.next_after, page.items[-1].claim.id)
            page = self.service.list_review_page(limit=1, after_claim_id=page.next_after)
        self.assertEqual(tuple(seen), expected)
        for index, anchor in enumerate(expected[:-1]):
            with self.subTest(index=index):
                self.assertEqual(self.service.list_review_page(limit=1, after_claim_id=anchor).items[0].claim.id,
                                 expected[index + 1])

    def test_corruption_outside_displayed_page_is_not_hidden(self) -> None:
        ids = self.import_facts("off-page", 4)
        self.service.list_review_page(limit=1)
        # Deliberate corruption of a fictional off-page claim tests fail-closed validation.
        self.repository._connection.execute("UPDATE claims SET canonical_text=? WHERE id=?",
            ("PRIVATE_CORRUPTED_MARKER", ids[-1]))
        with self.assertRaises(RepositoryError) as caught:
            self.service.list_review_page(limit=1)
        self.assertNotIn("PRIVATE_CORRUPTED_MARKER", str(caught.exception))

    def test_decided_anchor_revalidates_claim_evidence_and_decision_audit(self) -> None:
        for target in ("claim", "evidence", "decision"):
            with self.subTest(target=target):
                ids = self.import_facts(f"decided-{target}", 2)
                self.decide(ids[0], ApprovalStatus.REJECTED)
                self.service.list_review_page(limit=1, after_claim_id=ids[0])
                association = self.repository.get_profile_import_review_item(ids[0])
                self.repository._connection.execute("BEGIN")
                try:
                    if target == "claim":
                        self.repository._connection.execute("UPDATE claims SET canonical_text=? WHERE id=?",
                            ("PRIVATE_CORRUPTED_MARKER", ids[0]))
                    elif target == "evidence":
                        self.repository._connection.execute("UPDATE evidence SET source_text=? WHERE id=?",
                            ("PRIVATE_CORRUPTED_MARKER", association["evidence_id"]))
                    else:
                        self.repository._connection.execute("UPDATE workflow_runs SET input_hash_sha256=? WHERE id=?",
                            ("0" * 64, association["decision_workflow_run_id"]))
                    with self.assertRaises(RepositoryError) as caught:
                        self.service.list_review_page(limit=1, after_claim_id=ids[0])
                    self.assertNotIn("PRIVATE_CORRUPTED_MARKER", str(caught.exception))
                finally:
                    # Roll back only deliberate test corruption; the real decision remains.
                    self.repository._connection.rollback()
                self.decide(ids[1], ApprovalStatus.REJECTED)

    def test_off_page_generic_evidence_is_validated(self) -> None:
        self.generic("generic-first", now=NOW + timedelta(days=1))
        second = self.generic("generic-second")
        evidence = self.service.create_evidence(CreateEvidence(claim_id=second,
            source_type=SourceType.USER_STATEMENT, source_ref="user-statement://fictional/evidence",
            source_text="Fictional supporting text."), now=NOW)
        self.repository._connection.execute("UPDATE evidence SET captured_at=? WHERE id=?",
            ("PRIVATE_INVALID_TIMESTAMP", evidence.id))
        with self.assertRaises(ValueError):
            self.service.list_review_page(limit=1)

    def test_nonpending_generic_anchor_claim_and_evidence_are_validated(self) -> None:
        anchor = self.generic("generic-decided", approved=True)
        self.generic("generic-later", now=NOW - timedelta(days=1))
        evidence = self.service.create_evidence(CreateEvidence(claim_id=anchor,
            source_type=SourceType.USER_STATEMENT, source_ref="user-statement://fictional/evidence",
            source_text="Fictional supporting text."), now=NOW)
        for table, identifier, field in (("claims", anchor, "created_at"),
                                          ("evidence", evidence.id, "captured_at")):
            self.repository._connection.execute("BEGIN")
            try:
                # Fixed table/field names above deliberately corrupt only synthetic records.
                self.repository._connection.execute(f"UPDATE {table} SET {field}=? WHERE id=?",
                    ("0_PRIVATE_INVALID_TIMESTAMP", identifier))
                with self.assertRaises(ValueError) as caught:
                    self.service.list_review_page(limit=1, after_claim_id=anchor)
                self.assertNotIn("PRIVATE_INVALID_TIMESTAMP", str(caught.exception))
            finally:
                self.repository._connection.rollback()

    def test_one_snapshot_spans_full_queue_anchor_and_order_reads(self) -> None:
        ids = self.import_facts("snapshot", 3)
        statements: list[str] = []
        self.repository._connection.set_trace_callback(statements.append)
        with patch.object(self.repository, "read_transaction", wraps=self.repository.read_transaction) as read:
            page = self.service.list_review_page(limit=1, after_claim_id=ids[0])
        self.repository._connection.set_trace_callback(None)
        self.assertEqual(read.call_count, 1)
        first_select = next(index for index, statement in enumerate(statements) if statement.lstrip().startswith("SELECT"))
        self.assertLess(statements.index("BEGIN"), first_select)
        self.assertEqual(statements[-1], "ROLLBACK")
        self.assertEqual(sum(statement == "BEGIN" for statement in statements), 1)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assert_page(page, (ids[1],), before=1, after=1, total=3)

    def test_caller_owned_transaction_is_not_committed_or_rolled_back_on_success_or_error(self) -> None:
        claim_id = "generic-uncommitted"
        with self.assertRaisesRegex(RuntimeError, "Roll back fictional setup"):
            with self.repository.transaction():
                self.generic(claim_id)
                self.assert_page(self.service.list_review_page(limit=3), (claim_id,), before=0, after=0, total=1)
                self.assertTrue(self.repository._connection.in_transaction)
                with self.assertRaises(ValueError):
                    self.service.list_review_page(limit=3, after_claim_id="missing-fictional")
                self.assertTrue(self.repository._connection.in_transaction)
                raise RuntimeError("Roll back fictional setup")
        self.assertIsNone(self.repository.get_claim(claim_id))

    def test_read_only_adapter_leaves_database_bytes_and_sidecars_unchanged(self) -> None:
        ids = self.import_facts("read-only", 3)
        self.decide(ids[0], ApprovalStatus.APPROVED)
        before = self.path.read_bytes()
        before_files = sorted(path.name for path in self.path.parent.iterdir())
        with SQLiteRepository(self.path, read_only=True).initialize() as repository:
            page = ProfileService(repository).list_review_page(limit=1, after_claim_id=ids[0])
        self.assert_page(page, (ids[1],), before=0, after=1, total=2)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.path.parent.iterdir()), before_files)

    def test_result_counts_and_continuation_cannot_be_forged(self) -> None:
        self.import_facts("result", 3)
        page = self.service.list_review_page(limit=1)
        for changes in ({"pending_count": True}, {"returned_count": 0}, {"pending_before_count": -1},
                        {"pending_after_count": 0}, {"next_after": None}, {"next_after": "other-claim"},
                        {"items": []}, {"limit": 2}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(page, **changes)


if __name__ == "__main__":
    unittest.main()
