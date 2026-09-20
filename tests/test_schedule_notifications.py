from __future__ import annotations

import copy
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from grounded_apply.services.schedule_notifications import (
    NotificationCapacityError, notification_capacity, notification_delta, notification_failure, notification_retry_limit,
    notification_update, validate_notification_summary,
)
from grounded_apply.services.workflow import digest


def blocker(**changes):
    return {"kind": "need_info", "reason": "missing", "intent": "career_answer",
        "question": "Fictional private question", "related_claim_ids": ["fictional-claim"], **changes}


def item(identifier="one", *, status="draft", material=True, blockers=None, **changes):
    return {"job_id": "fictional-job-" + identifier, "item_id": "fictional-run-item-" + identifier,
        "source_url": "https://example.com/jobs/" + identifier, "title": "Fictional private title",
        "status": status, "currently_valid": material,
        "material_id": "fictional-material-" + identifier if material else None,
        "bundle_sha256": digest(identifier) if material else None,
        "blockers": blockers or [], **changes}


def source(identifier="fictional-source", *, stage="complete", status="successful", errors=None, **changes):
    return {"source_id": identifier, "stage": stage,
        "report": None if stage != "complete" else {"source_id": identifier, "status": status,
            "errors": errors or [], "fetched_at": "2026-09-19T12:00:00+00:00", "count": 0},
        "skipped": {"invalid_record": 0}, **changes}


def report(*items, **changes):
    return {"schema_version": 1, "search_id": "fictional-search", "run_id": "fictional-run",
        "phase": "complete", "status": "completed", "stop_reason": None, "coverage_complete": True,
        "items": list(items), "sources": [source()], **changes}


class ScheduleNotificationTests(unittest.TestCase):
    def test_capacity_before_child_is_content_free_and_retains_prior_observations(self):
        empty = notification_capacity("fictional-search")
        self.assertTrue(empty.notify)
        self.assertEqual(empty.summary["seen_materials"], [])
        self.assertIsNone(empty.summary["run_health"]["coverage_complete"])
        self.assertFalse(notification_capacity("fictional-search", empty.summary).notify)
        prior = notification_update(report(item())).summary
        capacity = notification_capacity("fictional-search", prior)
        self.assertEqual(capacity.summary["seen_materials"], prior["seen_materials"])
        self.assertEqual(capacity.delta["new_materials"], [])
        self.assertEqual(notification_delta(prior, capacity.summary), capacity.delta)
        with self.assertRaises(ValueError):
            notification_capacity("another-search", prior)

    def test_capacity_with_valid_partial_report_preserves_new_draft_delta(self):
        prior = notification_update(report(item())).summary
        partial = report(item(), item("two"), phase="preparing", status="budget_exhausted", stop_reason="capacity_reached")
        capacity = notification_capacity("fictional-search", prior, report=partial)
        self.assertEqual(len(capacity.delta["new_materials"]), 1)
        self.assertEqual(capacity.summary["run_health"]["status"], "capacity_reached")
        self.assertFalse(notification_capacity("fictional-search", capacity.summary, report=partial).notify)
        self.assertTrue(notification_update(report(item(), item("two")), capacity.summary).notify)

    def test_retry_limit_preserves_new_materials_and_is_quiet_until_recovery(self):
        prior = notification_update(report(item())).summary
        current = report(item(), item("two"), status="budget_exhausted", stop_reason="item_budget")
        limited = notification_retry_limit("fictional-search", prior, report=current)
        self.assertTrue(limited.notify)
        self.assertEqual(len(limited.delta["new_materials"]), 1)
        self.assertEqual(limited.summary["run_health"]["status"], "retry_limit")
        self.assertEqual(prior["run_health"]["status"], "healthy")
        self.assertFalse(notification_retry_limit("fictional-search", limited.summary, report=current).notify)
        self.assertFalse(notification_retry_limit("fictional-search", limited.summary).notify)
        recovered = notification_update(report(item(), item("two")), limited.summary)
        self.assertTrue(recovered.notify)
        self.assertEqual(recovered.delta["run_health_change"]["previous"]["status"], "retry_limit")
        self.assertEqual(recovered.delta["new_materials"], [])

    def test_retry_limit_before_child_keeps_closed_content_free_state(self):
        limited = notification_retry_limit("fictional-search")
        self.assertTrue(limited.notify)
        self.assertIsNone(limited.summary["run_health"]["coverage_complete"])
        self.assertEqual(limited.summary["seen_materials"], [])
        with self.assertRaises(ValueError):
            notification_retry_limit("another-search", limited.summary)
        malformed = copy.deepcopy(limited.summary)
        malformed["run_health"]["stop_reason"] = "item_budget"
        with self.assertRaises(ValueError):
            validate_notification_summary(malformed)

    def test_actual_validated_search_partial_package_uses_the_contract(self):
        from grounded_apply.repositories import SQLiteRepository
        from grounded_apply.services.searches import SearchService
        from tests.test_discovery import encoded, greenhouse_job
        from tests.test_materials import SyntheticRenderer, approved_fixture

        with tempfile.TemporaryDirectory(prefix="gapply-synthetic-notifications-") as folder:
            database = Path(folder) / "fictional.db"
            with SQLiteRepository(database).initialize() as repository:
                _, claims = approved_fixture(repository)

            @contextmanager
            def factory(read_only):
                with SQLiteRepository(database, read_only=read_only, existing_only=True).initialize() as repository:
                    yield repository

            class Feed:
                def get(self, url, *, max_bytes, timeout):
                    return encoded({"jobs": [greenhouse_job(1)]})

            service = SearchService(factory, Feed(), SyntheticRenderer())
            scope = service.configure({"schema_version": 1,
                "sources": [{"id": "fictional", "provider": "greenhouse", "board": "example"}],
                "claim_ids": list(claims), "max_jobs": 1,
                "questions": [{"id": "fictional-question", "text": "Do you require sponsorship?",
                    "claim_ids": [], "required": True}]}, idempotency_key="fictional-notice-scope")
            result = service.run(scope["search_id"], idempotency_key="fictional-notice-run")
            self.assertEqual(result["status"], "waiting_for_input")
            first = notification_update(result)
            self.assertEqual(len(first.delta["new_materials"]), 1)
            self.assertEqual(len(first.delta["new_blockers"]), 1)
            self.assertFalse(notification_update(service.get(result["run_id"]), first.summary).notify)

    def test_initial_healthy_empty_and_unchanged_empty_are_quiet(self):
        first = notification_update(report())
        self.assertFalse(first.notify)
        self.assertFalse(notification_update(report(run_id="another-run"), first.summary).notify)
        self.assertEqual(validate_notification_summary(first.summary), first.summary)

    def test_materials_are_seen_once_across_disappearance_and_reordered_runs(self):
        first = notification_update(report(item()))
        self.assertTrue(first.notify)
        self.assertEqual(len(first.delta["new_materials"]), 1)
        empty = notification_update(report(), first.summary)
        self.assertFalse(empty.notify)
        recurring = notification_update(report(item(item_id="another-item", job_id="changed-job-version")), empty.summary)
        self.assertFalse(recurring.notify)
        second = notification_update(report(item("two"), item()), recurring.summary)
        self.assertEqual(second.delta["new_materials"], [{"material_id": "fictional-material-two", "bundle_sha256": digest("two")}])

    def test_partial_current_materials_notify_and_stale_materials_do_not(self):
        partial = item(status="blocked", blockers=[blocker()])
        fresh = notification_update(report(partial))
        self.assertEqual(len(fresh.delta["new_materials"]), 1)
        self.assertEqual(len(fresh.delta["new_blockers"]), 1)
        stale = notification_update(report({**partial, "currently_valid": False}))
        self.assertEqual(stale.delta["new_materials"], [])
        self.assertEqual(len(stale.delta["new_blockers"]), 1)

    def test_immutable_material_id_cannot_change_bundle_hash(self):
        prior = notification_update(report(item())).summary
        with self.assertRaises(ValueError):
            notification_update(report(item(bundle_sha256="0" * 64)), prior)

    def test_blocker_absence_is_not_resolution_but_reobservation_and_recurrence_are(self):
        blocked = item(status="blocked", material=False, blockers=[blocker()])
        first = notification_update(report(blocked))
        absent = notification_update(report(), first.summary)
        self.assertFalse(absent.notify)
        repeat = notification_update(report({**blocked, "job_id": "new-snapshot", "item_id": "new-run-item"}), absent.summary)
        self.assertFalse(repeat.notify)
        resolved = notification_update(report(item()), repeat.summary)
        self.assertEqual(len(resolved.delta["resolved_blockers"]), 1)
        self.assertEqual(resolved.summary["postings"][0]["blocker_sha256s"], [])
        recurring = notification_update(report(blocked), resolved.summary)
        self.assertEqual(len(recurring.delta["new_blockers"]), 1)
        self.assertTrue(recurring.notify)

    def test_pending_item_cannot_resolve_prior_blockers(self):
        first = notification_update(report(item(status="blocked", material=False, blockers=[blocker()])))
        for status in ("queued", "building"):
            with self.subTest(status=status):
                pending = notification_update(report(item(status=status, material=False)), first.summary)
                self.assertFalse(pending.notify)
                self.assertEqual(pending.summary["postings"], first.summary["postings"])

    def test_changed_semantic_blocker_not_prompt_label_or_run_identity_notifies(self):
        original = blocker(question_id="question-1", question_label="Fictional label", question_sha256=digest("question"))
        first = notification_update(report(item(status="blocked", material=False, blockers=[original])))
        cosmetic = {**original, "question": "Different display prompt", "question_id": "question-2", "question_label": "Different display label"}
        same = notification_update(report(item(status="blocked", material=False, blockers=[cosmetic])), first.summary)
        self.assertFalse(same.notify)
        changed = {**original, "question_sha256": digest("different question")}
        delta = notification_update(report(item(status="blocked", material=False, blockers=[changed])), first.summary).delta
        self.assertEqual(len(delta["new_blockers"]), 1)
        self.assertEqual(len(delta["resolved_blockers"]), 1)

    def test_unrelated_posting_blockers_survive_other_posting_resolution(self):
        first = notification_update(report(item(status="blocked", material=False, blockers=[blocker()]),
            item("two", status="blocked", material=False, blockers=[blocker(reason="stale")])))
        next_report = notification_update(report(item()), first.summary)
        self.assertEqual(len(next_report.delta["resolved_blockers"]), 1)
        self.assertEqual(sum(len(entry["blocker_sha256s"]) for entry in next_report.summary["postings"]), 1)

    def test_two_versions_same_posting_union_blockers_conservatively(self):
        blocked = item(status="blocked", material=False, blockers=[blocker()])
        cleared = item(job_id="new-version")
        result = notification_update(report(cleared, blocked))
        self.assertEqual(len(result.summary["postings"]), 1)
        self.assertEqual(len(result.summary["postings"][0]["blocker_sha256s"]), 1)

    def test_source_outage_repeat_and_recovery_ignore_counts_and_timestamps(self):
        first = notification_update(report())
        failed_report = report(sources=[source(status="failed", errors=["timeout"])], coverage_complete=False)
        failed = notification_update(failed_report, first.summary)
        self.assertTrue(failed.notify)
        self.assertEqual(failed.delta["source_changes"][0]["current"]["status"], "failed")
        copied = copy.deepcopy(failed_report)
        copied["sources"][0]["report"].update(fetched_at="later", count=100)
        self.assertFalse(notification_update(copied, failed.summary).notify)
        recovered = notification_update(report(), failed.summary)
        self.assertTrue(recovered.notify)
        self.assertEqual(recovered.delta["source_changes"][0]["current"]["status"], "successful")
        self.assertFalse(notification_update(report(), recovered.summary).notify)

    def test_source_error_order_ignored_but_changed_error_notifies(self):
        original = report(sources=[source(status="partial", errors=["timeout", "invalid_record"])], coverage_complete=False)
        first = notification_update(original)
        reorder = report(sources=[source(status="partial", errors=["invalid_record", "timeout"])], coverage_complete=False)
        self.assertFalse(notification_update(reorder, first.summary).notify)
        changed = report(sources=[source(status="partial", errors=["rate_limited"])], coverage_complete=False)
        self.assertTrue(notification_update(changed, first.summary).notify)

    def test_unfinished_sources_do_not_claim_outage_or_recovery(self):
        healthy = notification_update(report()).summary
        failed = notification_update(report(sources=[source(status="failed", errors=["timeout"])], coverage_complete=False)).summary
        for prior in (healthy, failed):
            for stage in ("pending", "fetching", "deferred"):
                with self.subTest(stage=stage, prior=prior["run_health"]):
                    result = notification_update(report(phase="discovering", status="running", coverage_complete=False,
                        sources=[source(stage=stage)]), prior)
                    self.assertFalse(result.notify)
                    self.assertEqual(result.summary["sources"], prior["sources"])
                    self.assertEqual(result.summary["run_health"], prior["run_health"])
        initial = notification_update(report(phase="discovering", status="pending", coverage_complete=False,
            sources=[source(stage="pending")]))
        self.assertFalse(initial.notify)
        self.assertIsNone(initial.summary["run_health"]["coverage_complete"])

    def test_manual_and_capture_gap_health_are_visible_once(self):
        for entry in (source(status="manual_required"), source(skipped={"invalid_record": 1})):
            with self.subTest(entry=entry):
                current = report(sources=[entry], coverage_complete=False)
                first = notification_update(current)
                self.assertTrue(first.notify)
                self.assertEqual(len(first.delta["source_changes"]), 1)
                self.assertFalse(notification_update(current, first.summary).notify)

    def test_rotating_budget_deferrals_are_quiet_after_initial_run_gap(self):
        for count in (2, 3):
            with self.subTest(source_count=count):
                prior = None
                for day in range(count * 2):
                    entries = [source(f"fictional-{index}",
                        stage="complete" if index == day % count else "deferred") for index in range(count)]
                    update = notification_update(report(sources=entries, coverage_complete=False,
                        status="completed_with_gaps", stop_reason="request_budget"), prior)
                    self.assertEqual(update.notify, day == 0)
                    self.assertEqual(update.delta["source_changes"], [])
                    self.assertEqual(update.summary["run_health"], {
                        "status": "budget_exhausted", "stop_reason": "request_budget", "coverage_complete": False})
                    self.assertEqual(len(update.summary["sources"]), min(day + 1, count))
                    self.assertTrue(all(entry["status"] == "successful" for entry in update.summary["sources"]))
                    prior = update.summary

    def test_observed_outage_survives_deferral_until_observed_recovery(self):
        def bounded(*entries):
            return report(sources=list(entries), coverage_complete=False,
                status="completed_with_gaps", stop_reason="request_budget")
        outage = notification_update(bounded(source("fictional-a", status="failed", errors=["timeout"]),
            source("fictional-b", stage="deferred")))
        skipped = notification_update(bounded(source("fictional-a", stage="deferred"),
            source("fictional-b")), outage.summary)
        self.assertFalse(skipped.notify)
        self.assertEqual(skipped.summary["sources"][0]["status"], "failed")
        repeated = notification_update(bounded(source("fictional-a", status="failed", errors=["timeout"]),
            source("fictional-b", stage="deferred")), skipped.summary)
        self.assertFalse(repeated.notify)
        recovered = notification_update(bounded(source("fictional-a"),
            source("fictional-b", stage="deferred")), repeated.summary)
        self.assertTrue(recovered.notify)
        self.assertEqual(len(recovered.delta["source_changes"]), 1)
        self.assertEqual(recovered.delta["source_changes"][0]["previous"]["status"], "failed")
        self.assertEqual(recovered.delta["source_changes"][0]["current"]["status"], "successful")
        self.assertIsNone(recovered.delta["run_health_change"])
        self.assertFalse(notification_update(bounded(source("fictional-a", stage="deferred"),
            source("fictional-b")), recovered.summary).notify)

    def test_legacy_deferred_summary_normalizes_once_without_changing_old_delta(self):
        initial = notification_update(report(sources=[source("fictional-a"), source("fictional-b", stage="deferred")],
            coverage_complete=False, status="completed_with_gaps", stop_reason="request_budget")).summary
        legacy = copy.deepcopy(initial)
        legacy["sources"].append({"source_id": "fictional-b", "status": "deferred", "errors": []})
        legacy = validate_notification_summary(legacy)
        old_delta = notification_delta(None, legacy)
        self.assertEqual(old_delta["source_changes"], [{"source_id": "fictional-b", "previous": None,
            "current": {"source_id": "fictional-b", "status": "deferred", "errors": []}}])
        observed = notification_update(report(sources=[source("fictional-a", stage="deferred"), source("fictional-b")],
            coverage_complete=False, status="completed_with_gaps", stop_reason="request_budget"), legacy)
        self.assertTrue(observed.notify)
        self.assertEqual(len(observed.delta["source_changes"]), 1)
        self.assertEqual(observed.delta["source_changes"][0]["source_id"], "fictional-b")
        quiet = notification_update(report(sources=[source("fictional-a"), source("fictional-b", stage="deferred")],
            coverage_complete=False, status="completed_with_gaps", stop_reason="request_budget"), observed.summary)
        self.assertFalse(quiet.notify)
        self.assertEqual(notification_delta(None, legacy), old_delta)

    def test_failure_capacity_budget_and_recovery_notify_once_independent_of_drafts(self):
        for status, stop in (("failed", "shared_failure"), ("budget_exhausted", "capacity_reached"),
            ("budget_exhausted", "item_budget"), ("completed_with_gaps", "request_budget")):
            with self.subTest(status=status, stop=stop):
                current = report(item(), status=status, stop_reason=stop)
                first = notification_update(current)
                self.assertTrue(first.notify)
                self.assertIsNotNone(first.delta["run_health_change"])
                self.assertFalse(notification_update(current, first.summary).notify)
                recovered = notification_update(report(item()), first.summary)
                self.assertTrue(recovered.notify)
                self.assertEqual(recovered.delta["new_materials"], [])
                self.assertFalse(notification_update(report(item()), recovered.summary).notify)

    def test_integrity_failure_envelope_preserves_history_without_copying_artifacts(self):
        prior = notification_update(report(item())).summary
        failed = notification_failure("fictional-search", prior)
        self.assertTrue(failed.notify)
        self.assertEqual(failed.delta["new_materials"], [])
        self.assertEqual(failed.summary["seen_materials"], prior["seen_materials"])
        self.assertEqual(failed.summary["sources"], prior["sources"])
        self.assertEqual(failed.summary["run_health"]["coverage_complete"], True)
        self.assertFalse(notification_failure("fictional-search", failed.summary).notify)

    def test_starting_a_retry_does_not_claim_shared_failure_recovery(self):
        prior = notification_failure("fictional-search").summary
        for status in ("pending", "running"):
            with self.subTest(status=status):
                retry = notification_update(report(phase="discovering", status=status,
                    coverage_complete=False, sources=[source(stage="pending")]), prior)
                self.assertFalse(retry.notify)
                self.assertEqual(retry.summary["run_health"], prior["run_health"])
        recovered = notification_update(report(), prior)
        self.assertTrue(recovered.notify)
        self.assertEqual(recovered.summary["run_health"]["status"], "healthy")

    def test_delta_recomputes_from_summaries_without_full_reports(self):
        first = notification_update(report(item(status="blocked", blockers=[blocker()])))
        self.assertEqual(notification_delta(None, first.summary), first.delta)
        second = notification_update(report(item(), sources=[source(status="partial", errors=["timeout"])],
            coverage_complete=False), first.summary)
        self.assertEqual(notification_delta(first.summary, second.summary), second.delta)
        self.assertEqual(notification_delta(second.summary, second.summary),
            {"new_materials": [], "new_blockers": [], "resolved_blockers": [], "source_changes": [], "run_health_change": None})

    def test_delta_rejects_cumulative_state_loss_and_scope_rebinding(self):
        prior = notification_update(report(item())).summary
        changes = [{"seen_materials": []}, {"postings": []}, {"sources": []},
            {"search_id": "different-search"},
            {"run_health": {**prior["run_health"], "coverage_complete": None}}]
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                notification_delta(prior, {**prior, **changed})

    def test_no_inputs_are_mutated_and_no_raw_text_or_urls_retained(self):
        current = report(item(status="blocked", blockers=[blocker()]))
        before = copy.deepcopy(current)
        first = notification_update(current)
        prior_copy = copy.deepcopy(first.summary)
        notification_update(current, first.summary)
        self.assertEqual(current, before)
        self.assertEqual(first.summary, prior_copy)
        retained = json.dumps({"summary": first.summary, "delta": first.delta})
        for private in ("Fictional private", "https://example.com", "fictional-run", "fictional-claim", "career_answer"):
            self.assertNotIn(private, retained)

    def test_closed_prior_shape_and_search_binding_reject_unknown_data(self):
        prior = notification_update(report(item())).summary
        bad = [None, [], {}, {**prior, "schema_version": True}, {**prior, "secret": "private"},
            {**prior, "search_id": "another-search"},
            {**prior, "seen_materials": [{**prior["seen_materials"][0], "secret": "private"}]},
            {**prior, "postings": [{"posting_sha256": "not-a-hash", "blocker_sha256s": []}]},
            {**prior, "sources": [{"source_id": "fictional-source", "status": "failed", "errors": ["private-error"]}]},
            {**prior, "run_health": {**prior["run_health"], "extra": "private"}}]
        for malformed in bad:
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                if malformed is None:
                    validate_notification_summary(malformed)
                else:
                    notification_update(report(), malformed)

    def test_malformed_report_shapes_fail_without_echoing_private_data(self):
        bad = [report(schema_version=True), report(status=[]), report(stop_reason={}), report(items=[{}]),
            report(item(status=[])), report(item(source_url="https://user:secret@example.com/job")),
            report(item(bundle_sha256="private")), report(item(), item()),
            report(item(status="blocked", material=False, blockers=[blocker(kind=[])])),
            report(item(status="blocked", material=False, blockers=[{**blocker(), "private": "secret"}])),
            report(sources=[source(stage=[])]), report(sources=[source(status="failed", errors=["private"])])]
        for malformed in bad:
            with self.subTest(malformed=malformed), self.assertRaises(ValueError) as error:
                notification_update(malformed)
            self.assertNotIn("secret", str(error.exception))

    def test_cumulative_material_and_posting_capacity_fail_closed_without_truncation(self):
        prior = notification_update(report(item())).summary
        before = copy.deepcopy(prior)
        for constant in ("MAX_NOTIFICATION_MATERIALS", "MAX_NOTIFICATION_POSTINGS"):
            with self.subTest(constant=constant), patch("grounded_apply.services.schedule_notifications." + constant, 1):
                with self.assertRaises(NotificationCapacityError):
                    notification_update(report(item("two")), prior)
        self.assertEqual(prior, before)

    def test_active_blocker_capacity_includes_absent_postings_and_allows_resolution(self):
        first = notification_update(report(item(status="blocked", material=False, blockers=[blocker()])))
        with patch("grounded_apply.services.schedule_notifications.MAX_NOTIFICATION_BLOCKERS", 1):
            with self.assertRaises(NotificationCapacityError):
                notification_update(report(item("two", status="blocked", material=False, blockers=[blocker()])), first.summary)
            resolved = notification_update(report(item()), first.summary)
            new = notification_update(report(item("two", status="blocked", material=False, blockers=[blocker()])), resolved.summary)
            self.assertEqual(len(new.delta["new_blockers"]), 1)


if __name__ == "__main__":
    unittest.main()
