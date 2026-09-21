from __future__ import annotations

import json
import sqlite3
import unittest
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from grounded_apply.domain import ClaimPacket, EvidenceConfirmationStatus, to_jsonable
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.materials import MaterialBlocked, MaterialHistoryIntegrityError, MaterialService, _structure
from grounded_apply.services.matching import job_policy
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
from grounded_apply.services.workflow import canonical, digest, hash_bytes
from tests.test_materials import SyntheticRenderer, approved_fixture


CLAIM_AT = "2029-01-01T00:00:00+00:00"
MATERIAL_AT = datetime(2030, 1, 1, tzinfo=UTC)
LATER = datetime(2031, 1, 1, tzinfo=UTC)


class MaterialHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = SQLiteRepository(":memory:").initialize()
        self.addCleanup(self.repository.close)
        self.job_id, self.claim_ids = approved_fixture(self.repository)
        self.renderer = SyntheticRenderer()
        self.service = MaterialService(self.repository, self.renderer)
        self.build_index = 0

    def build(self, claim_ids: tuple[str, ...] | None = None, *, transformation: str = "approved_text_selection@2",
              layout: object = None, questions: object = ()) -> str:
        self.build_index += 1
        with patch("grounded_apply.services.materials.timestamp", return_value=MATERIAL_AT.isoformat()), patch(
            "grounded_apply.services.materials.job_policy", return_value=job_policy(self.job_id, now=MATERIAL_AT),
        ), patch("grounded_apply.services.materials.CURRENT_TRANSFORMATION", transformation):
            return self.service.build(
                self.job_id, self.claim_ids if claim_ids is None else claim_ids,
                idempotency_key=f"fictional-history-material-{self.build_index}", layout=layout, questions=questions,
            )["material_id"]

    def generic_claim(
        self, suffix: str, *, claim_type: str = "skill_use", value: str = "Python",
        text: str = "Used Python in a fictional service", evidence_text: str | None = None,
        **fields: object,
    ) -> str:
        claim_id = f"fictional-history-claim-{suffix}"
        defaults = dict(
            claim_id=claim_id, claim_type=claim_type, value=value, canonical_text=text,
            subject_type="person", subject_id=f"fictional-subject-{suffix}",
            status="verified", approval_status="approved", source_type="user_statement",
            source_ref="fictional history fixture", verified_at=CLAIM_AT,
            verified_by="fictional-reviewer", created_at=CLAIM_AT,
        )
        self.repository.add_claim(**(defaults | fields))
        self.repository.add_evidence(
            evidence_id=f"fictional-history-evidence-{suffix}", claim_id=claim_id,
            source_type="user_statement", source_ref="fictional history fixture",
            source_text=evidence_text or text, confirmation_status="confirmed",
            confirmed_at=CLAIM_AT, confirmed_by="fictional-reviewer", captured_at=CLAIM_AT,
            created_at=CLAIM_AT,
        )
        return claim_id

    def rewrite_material(
        self, material_id: str, *, edit_structure: Callable[[dict], None] | None = None,
        edit_manifest: Callable[[dict], None] | None = None,
        edit_payload: Callable[[dict], None] | None = None,
        edit_validation: Callable[[dict], None] | None = None,
    ) -> None:
        """Forge only isolated test state, including every ordinary hash binding.

        Restoring exact trigger SQL permits subsequent snapshot validation.
        This is not an application mutation path or a source of factual authority.
        """
        record = self.repository.get_material_version(material_id)
        structure = json.loads(record["structure_json"])
        manifest = json.loads(record["manifest_json"])
        validation = json.loads(record["validation_json"])
        workflow = self.repository.get_workflow_run(record["workflow_run_id"])
        payload = json.loads(workflow["input_json"])
        if edit_structure:
            edit_structure(structure)
        parsed = _structure(structure)
        normalized = asdict(parsed)
        rendered = self.renderer.render(parsed)
        manifest.update(
            structure_sha256=digest(normalized), pdf_sha256=hash_bytes(rendered.pdf),
            latex_sha256=hash_bytes(rendered.latex.encode()), text_sha256=hash_bytes(rendered.extracted_text.encode()),
        )
        validation["factual_units"] = len(parsed.units)
        if edit_manifest:
            edit_manifest(manifest)
        if edit_validation:
            edit_validation(validation)
        payload.update(structure_sha256=digest(normalized), question_specs_sha256=digest(manifest["question_specs"]),
                       answers_sha256=digest(manifest["answers"]))
        if edit_payload:
            edit_payload(payload)
        bundle = digest({"structure": normalized, "manifest": manifest, "validation": validation})
        trigger_names = ("materials_no_update", "material_claims_no_delete")
        triggers = [self.repository._connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type = 'trigger' AND name = ?", (name,),
        ).fetchone()[0] for name in trigger_names]
        with self.repository.transaction():
            for name in trigger_names:
                self.repository._connection.execute(f"DROP TRIGGER {name}")
            self.repository._connection.execute("""UPDATE material_versions SET structure_json = ?, manifest_json = ?,
                validation_json = ?, pdf_bytes = ?, latex_text = ?, extracted_text = ?, bundle_sha256 = ? WHERE id = ?""",
                (canonical(structure), canonical(manifest), canonical(validation), rendered.pdf, rendered.latex,
                 rendered.extracted_text, bundle, material_id))
            self.repository._connection.execute(
                "UPDATE workflow_runs SET input_json = ?, input_hash_sha256 = ? WHERE id = ?",
                (canonical(payload), digest(payload), workflow["id"]),
            )
            self.repository._connection.execute("DELETE FROM material_claims WHERE material_id = ?", (material_id,))
            all_ids = {claim_id for unit in structure["units"] for claim_id in unit["packet_claim_ids"]}
            all_ids |= {claim_id for answer in manifest["answers"] for unit in answer["factual_units"] for claim_id in unit["claim_ids"]}
            for claim_id in sorted(all_ids):
                self.repository._connection.execute("INSERT INTO material_claims VALUES (?, ?)", (material_id, claim_id))
            for sql in triggers:
                self.repository._connection.execute(sql)
        # Every forged fixture must reach the new factual check, rather than
        # merely failing an existing PDF, bundle, mapping, or workflow checksum.
        self.service.get(material_id, require_current=False)

    def refresh_packet_hashes(self, structure: dict) -> None:
        claims, evidence = ProfileService(self.repository).validated_profile(apply_retirements=False)
        by_id = {claim.id: claim for claim in claims}
        for unit in structure["units"]:
            selected = tuple(by_id[identifier] for identifier in unit["packet_claim_ids"])
            supporting = tuple(item for item in evidence if item.claim_id in unit["packet_claim_ids"]
                               and item.confirmation_status is EvidenceConfirmationStatus.CONFIRMED)
            packet = ClaimPacket(intent=unit["claim_type"], claims=selected, evidence=supporting)
            unit["claim_sha256"] = digest(to_jsonable(packet))
            unit["evidence_ids"] = [item.id for item in supporting]

    def assert_history_rejected(self, material_id: str) -> None:
        before = self.repository._connection.serialize()
        with self.assertRaises(MaterialHistoryIntegrityError):
            self.service.validate_historical_facts(material_id)
        self.assertEqual(self.repository._connection.serialize(), before)
        self.assertFalse(self.repository._connection.in_transaction)

    def retire(self, claim_id: str, *, at: datetime = LATER) -> None:
        service = ProfileLifecycleService(self.repository)
        options = dict(actor_id="fictional-reviewer", idempotency_key=f"retire-{claim_id}", now=at)
        preview = service.retire(claim_id, **options)
        service.retire(claim_id, **options, confirm=True, preview_token=preview.preview_token)

    def test_registered_legacy_modern_and_explicit_presentations_validate_exactly(self) -> None:
        heading = self.generic_claim("heading", claim_type="employment_description", value="Fictional research role",
            text="Fictional Research Engineer | Example City | Example Lab | 2029",
            evidence_text=r"\resumeSubheading{Fictional Research Engineer}{Example City}{Example Lab}{2029}")
        bullet = self.generic_claim("bullet", evidence_text=r"\resumeItem{Used Python in a fictional service}")
        legacy = self.build((heading, bullet), transformation="approved_text_selection@1")
        modern = self.build((heading, bullet))
        styled = self.build((heading, bullet), layout={"schema_version": 1, "presentations": {heading: "paragraph"}})
        self.assertEqual([unit["presentation"] for unit in self.service.get(legacy, require_current=False)["structure"]["units"][:2]], ["paragraph", "paragraph"])
        self.assertEqual([unit["presentation"] for unit in self.service.get(modern, require_current=False)["structure"]["units"][:2]], ["heading", "bullet"])
        before = self.repository._connection.serialize()
        for material_id in (legacy, modern, styled):
            self.assertIsNone(self.service.validate_historical_facts(material_id))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_valid_at_creation_but_stale_now_facts_remain_historically_valid(self) -> None:
        claim = self.generic_claim("effective", effective_from=CLAIM_AT, effective_to=LATER.isoformat())
        material_id = self.build((claim,))
        with patch("grounded_apply.services.materials.job_policy", return_value=job_policy(self.job_id, now=LATER)):
            with self.assertRaises(MaterialBlocked):
                self.service.get(material_id)
        self.assertIsNone(self.service.validate_historical_facts(material_id))

    def test_later_retirement_is_valid_history_but_still_blocks_current_use(self) -> None:
        material_id = self.build()
        self.retire(self.claim_ids[0])
        with self.assertRaises(MaterialBlocked):
            self.service.get(material_id)
        before = self.repository._connection.serialize()
        self.assertIsNone(self.service.validate_historical_facts(material_id))
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_later_duplicate_and_conflicting_name_facts_do_not_reinterpret_recorded_packets(self) -> None:
        material_id = self.build()
        name_id = next(unit["claim_id"] for unit in self.service.get(material_id, require_current=False)["structure"]["units"]
                       if unit["claim_type"] == "candidate_name")
        original = self.repository.get_claim(name_id)
        for suffix, value in (("later-duplicate", json.loads(original["value_json"])), ("later-conflict", "Bailey Fiction")):
            self.generic_claim(suffix, claim_type="candidate_name", value=value, text=value,
                subject_type=original["subject_type"], subject_id=original["subject_id"],
                created_at=LATER.isoformat(), verified_at=LATER.isoformat())
            self.assertIsNone(self.service.validate_historical_facts(material_id))
            with self.assertRaises(MaterialBlocked):
                self.service.get(material_id)

    def test_shared_evidence_across_same_value_claims_preserves_both_packet_links(self) -> None:
        first = self.generic_claim("shared-a", claim_type="employment_title", value="Engineer", text="Fictional Engineer",
                                   subject_id="fictional-shared-role")
        second = self.generic_claim("shared-b", claim_type="employment_title", value="Engineer", text="Fictional Engineer",
                                    subject_id="fictional-shared-role")
        evidence_id = "fictional-history-evidence-shared-a"
        self.repository.link_claim_evidence(second, evidence_id, created_at=CLAIM_AT)
        material_id = self.build((first,))
        unit = self.service.get(material_id, require_current=False)["structure"]["units"][0]
        self.assertEqual(set(unit["packet_claim_ids"]), {first, second})
        self.assertEqual(unit["evidence_ids"].count(evidence_id), 2)
        self.assertIsNone(self.service.validate_historical_facts(material_id))

    def test_rehashed_text_claim_type_and_packet_hash_forgeries_are_rejected(self) -> None:
        for field, forged in (("text", "Led an invented fictional production project"), ("claim_type", "education"), ("claim_sha256", "0" * 64)):
            with self.subTest(field=field):
                material_id = self.build()
                self.rewrite_material(material_id, edit_structure=lambda value: value["units"][0].__setitem__(field, forged))
                self.assert_history_rejected(material_id)

    def test_rehashed_packet_omission_and_unrelated_membership_are_rejected(self) -> None:
        for omit in (False, True):
            with self.subTest(omit=omit):
                material_id = self.build()

                def forge(structure: dict) -> None:
                    first, other = structure["units"][:2]
                    first["packet_claim_ids"] = [] if omit else list(other["packet_claim_ids"])
                    first["evidence_ids"] = [] if omit else list(other["evidence_ids"])
                    first["claim_sha256"] = other["claim_sha256"]

                self.rewrite_material(material_id, edit_structure=forge)
                self.assert_history_rejected(material_id)

    def test_rehashed_packet_order_cannot_change_original_profile_order(self) -> None:
        first = self.generic_claim("packet-order-a", claim_type="employment_title", value="Engineer",
                                   text="Fictional Engineer", subject_id="fictional-order-role")
        second = self.generic_claim("packet-order-b", claim_type="employment_title", value="Engineer",
                                    text="Fictional Engineer", subject_id="fictional-order-role")
        material_id = self.build((first,))
        self.assertIsNone(self.service.validate_historical_facts(material_id))

        def reverse_packet(structure: dict) -> None:
            unit = structure["units"][0]
            self.assertEqual(set(unit["packet_claim_ids"]), {first, second})
            unit["packet_claim_ids"].reverse()
            self.refresh_packet_hashes(structure)

        self.rewrite_material(material_id, edit_structure=reverse_packet)
        self.assert_history_rejected(material_id)

    def test_rehashed_packet_cannot_omit_provably_historical_same_subject_peers(self) -> None:
        for mode in ("same-value", "conflicting", "contradicted"):
            with self.subTest(mode=mode):
                subject = f"fictional-historical-peer-{mode}"
                selected = self.generic_claim(f"peer-selected-{mode}", claim_type="employment_title",
                    value="Engineer", text="Fictional Engineer", subject_id=subject)
                peer = self.generic_claim(f"peer-omitted-{mode}", claim_type="employment_title",
                    value="Engineer", text="Fictional Engineer", subject_id=subject)
                material_id = self.build((selected,))
                if mode == "conflicting":
                    self.repository._connection.execute(
                        "UPDATE claims SET value_json = ?, canonical_text = ? WHERE id = ?",
                        ('"Manager"', "Fictional Manager", peer),
                    )
                elif mode == "contradicted":
                    self.repository._connection.execute("UPDATE claims SET status = 'contradicted' WHERE id = ?", (peer,))

                def omit_peer(structure: dict) -> None:
                    self.assertEqual(set(structure["units"][0]["packet_claim_ids"]), {selected, peer})
                    structure["units"][0]["packet_claim_ids"] = [selected]
                    self.refresh_packet_hashes(structure)

                self.rewrite_material(material_id, edit_structure=omit_peer)
                self.assert_history_rejected(material_id)

    def test_later_created_changed_or_approved_peers_do_not_reinterpret_recorded_packets(self) -> None:
        for mode in ("created", "changed", "approved"):
            with self.subTest(mode=mode):
                subject = f"fictional-later-peer-{mode}"
                selected = self.generic_claim(f"later-selected-{mode}", claim_type="employment_title",
                    value="Engineer", text="Fictional Engineer", subject_id=subject)
                if mode == "changed":
                    # This existing independent career fact was never a peer in
                    # the saved packet; its later type/subject change cannot
                    # reconstruct a contradiction at material creation.
                    peer = self.generic_claim("later-peer-changed")
                elif mode == "approved":
                    peer = self.generic_claim("later-peer-approved", claim_type="employment_title",
                        value="Manager", text="Fictional Manager", subject_id=subject,
                        status="withdrawn", approval_status="pending", verified_at=None, verified_by=None)
                material_id = self.build((selected,))
                if mode == "created":
                    self.generic_claim("later-peer-created", claim_type="employment_title", value="Manager",
                        text="Fictional Manager", subject_id=subject,
                        created_at=LATER.isoformat(), verified_at=LATER.isoformat())
                elif mode == "changed":
                    self.repository._connection.execute("""UPDATE claims SET claim_type = 'employment_title',
                        subject_id = ?, status = 'contradicted', updated_at = ? WHERE id = ?""",
                        (subject, LATER.isoformat(), peer))
                else:
                    self.repository._connection.execute("""UPDATE claims SET status = 'verified',
                        approval_status = 'approved', verified_at = ?, verified_by = 'fictional-reviewer',
                        updated_at = ? WHERE id = ?""", (LATER.isoformat(), LATER.isoformat(), peer))
                with self.assertRaises(MaterialBlocked):
                    self.service.get(material_id)
                before = self.repository._connection.serialize()
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                self.assertEqual(self.repository._connection.serialize(), before)

    def test_rehashed_selected_unit_order_and_auto_contact_order_are_rejected(self) -> None:
        for position in (0, -2):
            with self.subTest(position=position):
                material_id = self.build()

                def reorder(structure: dict) -> None:
                    units = structure["units"]
                    units[position], units[position + 1] = units[position + 1], units[position]

                self.rewrite_material(material_id, edit_structure=reorder)
                self.assert_history_rejected(material_id)

    def test_rehashed_empty_duplicate_or_oversized_selected_id_list_is_rejected(self) -> None:
        for selected in ([], [self.claim_ids[0], self.claim_ids[0]], [self.claim_ids[0]] * 81):
            with self.subTest(count=len(selected)):
                material_id = self.build()
                self.rewrite_material(material_id, edit_payload=lambda payload: payload.__setitem__("selected_claim_ids", selected))
                self.assert_history_rejected(material_id)

    def test_rehashed_evidence_omission_duplicate_and_order_changes_are_rejected(self) -> None:
        claim = self.generic_claim("evidence-order")
        self.repository.add_evidence(
            evidence_id="fictional-second-evidence", claim_id=claim, source_type="user_statement",
            source_ref="fictional second support", source_text="Used Python in a fictional service",
            confirmation_status="confirmed", confirmed_at=CLAIM_AT, confirmed_by="fictional-reviewer",
            captured_at=CLAIM_AT, created_at=CLAIM_AT,
        )
        for mode in ("omit", "duplicate", "reverse"):
            with self.subTest(mode=mode):
                material_id = self.build((claim,))
                self.assertIsNone(self.service.validate_historical_facts(material_id))

                def forge(structure: dict) -> None:
                    unit = structure["units"][0]
                    identifiers = unit["evidence_ids"]
                    self.assertEqual(len(identifiers), 2)
                    unit["evidence_ids"] = identifiers[:1] if mode == "omit" else identifiers * 2 if mode == "duplicate" else identifiers[::-1]

                self.rewrite_material(material_id, edit_structure=forge)
                self.assert_history_rejected(material_id)

    def test_rehashed_missing_or_foreign_requirement_links_are_rejected(self) -> None:
        claim = self.generic_claim("requirements", text="Built services in a fictional project")
        for requirements in ([], ["fictional-foreign-requirement"]):
            with self.subTest(requirements=requirements):
                material_id = self.build((claim,))
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                self.assertTrue(self.service.get(material_id, require_current=False)["structure"]["units"][0]["requirement_ids"])
                self.rewrite_material(material_id, edit_structure=lambda value: value["units"][0].__setitem__("requirement_ids", requirements))
                self.assert_history_rejected(material_id)

    def test_rehashed_presentation_not_justified_by_original_layout_is_rejected(self) -> None:
        claim = self.generic_claim("presentation", evidence_text="- Used Python in a fictional service")
        for transformation in ("approved_text_selection@1", "approved_text_selection@2"):
            with self.subTest(transformation=transformation):
                material_id = self.build((claim,), transformation=transformation)
                self.rewrite_material(material_id, edit_structure=lambda value: value["units"][0].__setitem__("presentation", "paragraph"))
                self.assert_history_rejected(material_id)

    def test_rehashed_boolean_structure_schema_is_not_normalized_into_valid_history(self) -> None:
        material_id = self.build()
        self.rewrite_material(material_id, edit_structure=lambda value: value.__setitem__("schema_version", True))
        self.assert_history_rejected(material_id)

    def test_rehashed_manifest_and_validation_boolean_number_aliases_are_rejected(self) -> None:
        alterations = (
            ("manifest", "schema_version", True),
            ("validation", "schema_version", True),
            ("validation", "page_count", True),
            ("validation", "valid", 1),
            ("validation", "unsupported_factual_units", False),
        )
        for target, key, value in alterations:
            with self.subTest(target=target, key=key):
                material_id = self.build()
                edit = lambda item: item.__setitem__(key, value)
                self.rewrite_material(material_id, **{f"edit_{target}": edit})
                self.assert_history_rejected(material_id)

    def test_future_claim_and_evidence_times_fail_even_after_packet_rehash(self) -> None:
        alterations = (
            ("claims", "created_at = ?, updated_at = ?", (LATER.isoformat(), LATER.isoformat())),
            ("claims", "verified_at = ?", (LATER.isoformat(),)),
            ("claims", "updated_at = ?", (LATER.isoformat(),)),
            ("evidence", "captured_at = ?", (LATER.isoformat(),)),
            ("evidence", "confirmed_at = ?", (LATER.isoformat(),)),
            ("evidence", "created_at = ?, updated_at = ?", (LATER.isoformat(), LATER.isoformat())),
            ("evidence", "updated_at = ?", (LATER.isoformat(),)),
            ("claim_evidence", "created_at = ?", (LATER.isoformat(),)),
        )
        for index, (table, assignment, values) in enumerate(alterations):
            with self.subTest(table=table, assignment=assignment):
                claim = self.generic_claim(f"future-{index}")
                material_id = self.build((claim,))
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                key = "claim_id" if table == "claim_evidence" else "id"
                identifier = f"fictional-history-evidence-future-{index}" if table == "evidence" else claim
                self.repository._connection.execute(f"UPDATE {table} SET {assignment} WHERE {key} = ?", (*values, identifier))
                self.rewrite_material(material_id, edit_structure=self.refresh_packet_hashes)
                self.assert_history_rejected(material_id)

    def test_scope_sensitivity_and_half_open_effective_boundaries_fail_even_after_packet_rehash(self) -> None:
        alterations = (
            ("scope_type = 'job', scope_id = 'fictional-other-job'", ()),
            ("sensitivity = 'confidential'", ()),
            ("effective_from = ?", ((MATERIAL_AT + timedelta(seconds=1)).isoformat(),)),
            ("effective_to = ?", (MATERIAL_AT.isoformat(),)),
        )
        for index, (assignment, values) in enumerate(alterations):
            with self.subTest(assignment=assignment):
                claim = self.generic_claim(f"policy-{index}")
                material_id = self.build((claim,))
                self.assertIsNone(self.service.validate_historical_facts(material_id))
                self.repository._connection.execute(f"UPDATE claims SET {assignment} WHERE id = ?", (*values, claim))
                self.rewrite_material(material_id, edit_structure=self.refresh_packet_hashes)
                self.assert_history_rejected(material_id)

    def test_absent_or_blank_approval_and_confirmation_actors_fail_after_packet_rehash(self) -> None:
        for table, field in (("claims", "verified_by"), ("evidence", "confirmed_by")):
            for actor in (None, " \t "):
                with self.subTest(table=table, absent=actor is None):
                    suffix = f"actor-{table}-{actor is None}"
                    claim = self.generic_claim(suffix)
                    material_id = self.build((claim,))
                    self.assertIsNone(self.service.validate_historical_facts(material_id))
                    identifier = claim if table == "claims" else f"fictional-history-evidence-{suffix}"
                    self.repository._connection.execute(f"UPDATE {table} SET {field} = ? WHERE id = ?", (actor, identifier))
                    self.rewrite_material(material_id, edit_structure=self.refresh_packet_hashes)
                    self.assert_history_rejected(material_id)

    def test_unconfirmed_or_derived_records_fail_closed(self) -> None:
        for derived in (False, True):
            with self.subTest(derived=derived):
                claim = self.generic_claim(f"unsupported-{derived}")
                material_id = self.build((claim,))
                if derived:
                    self.repository._connection.execute("""UPDATE claims SET status = 'derived',
                        derivation_rule_name = 'fictional-rule', derivation_rule_version = '1',
                        derivation_staleness_policy = 'inputs_effective_window', derivation_calculated_at = ? WHERE id = ?""",
                        (CLAIM_AT, claim))
                    self.repository._connection.execute("INSERT INTO claim_derivation_inputs VALUES (?, ?, ?)", (claim, self.claim_ids[0], 0))
                else:
                    self.repository._connection.execute("UPDATE evidence SET confirmation_status = 'pending', confirmed_at = NULL WHERE id = ?",
                                                        (f"fictional-history-evidence-unsupported-{derived}",))
                self.assert_history_rejected(material_id)

    def test_retirement_at_or_before_material_creation_is_rejected(self) -> None:
        for index, at in enumerate((MATERIAL_AT, MATERIAL_AT - timedelta(days=1))):
            with self.subTest(at=at):
                claim = self.claim_ids[index]
                material_id = self.build((claim,))
                self.retire(claim, at=at)
                self.assert_history_rejected(material_id)

    def test_questionnaire_draft_and_sensitive_blocker_remain_valid_after_later_retirement(self) -> None:
        questions = [
            {"id": "fictional-career", "text": "Describe your project experience.",
             "claim_ids": list(self.claim_ids[:2]), "required": True},
            {"id": "fictional-sensitive", "text": "Are you authorized to work without sponsorship?",
             "claim_ids": [], "required": True},
        ]
        material_id = self.build(questions=questions)
        answers = self.service.get(material_id, require_current=False)["manifest"]["answers"]
        self.assertEqual([answer["status"] for answer in answers], ["draft", "need_info"])
        self.assertEqual(len(answers[0]["factual_units"]), 2)
        self.assertIsNone(answers[1]["answer"])
        self.assertIsNone(self.service.validate_historical_facts(material_id))
        self.retire(self.claim_ids[0])
        self.assertIsNone(self.service.validate_historical_facts(material_id))

    def test_rehashed_questionnaire_facts_mappings_and_safety_markers_are_rejected(self) -> None:
        questions = [
            {"id": "fictional-career", "text": "Describe your project experience.",
             "claim_ids": list(self.claim_ids[:2]), "required": True},
            {"id": "fictional-sensitive", "text": "Are you authorized to work without sponsorship?",
             "claim_ids": [], "required": True},
        ]
        for mode in ("text", "mapping", "human_review", "external_action", "blocked_answer"):
            with self.subTest(mode=mode):
                material_id = self.build(questions=questions)
                self.assertIsNone(self.service.validate_historical_facts(material_id))

                def forge(manifest: dict) -> None:
                    draft, blocked = manifest["answers"]
                    if mode == "text":
                        draft["factual_units"][0]["text"] = "Led an invented fictional project"
                        draft["answer"] = "\n".join(unit["text"] for unit in draft["factual_units"])
                    elif mode == "mapping":
                        first, second = draft["factual_units"]
                        first["claim_ids"] = second["claim_ids"]
                        first["evidence_ids"] = second["evidence_ids"]
                    elif mode == "human_review":
                        draft["human_review_required"] = False
                    elif mode == "external_action":
                        draft["external_action_taken"] = True
                    else:
                        blocked["answer"] = "Invented sensitive answer"

                self.rewrite_material(material_id, edit_manifest=forge)
                self.assert_history_rejected(material_id)

    def test_answer_only_claim_approval_after_creation_is_not_historical_authority(self) -> None:
        claim = self.generic_claim("answer-only")
        questions = [{"id": "fictional-career", "text": "Describe your project experience.",
                      "claim_ids": [claim], "required": True}]
        material_id = self.build(questions=questions)
        self.assertIsNone(self.service.validate_historical_facts(material_id))
        self.repository._connection.execute("UPDATE claims SET verified_at = ? WHERE id = ?", (LATER.isoformat(), claim))
        # Answer mappings have no claim hash to update. The historical policy
        # must still establish approval at the saved material's creation time.
        self.service.get(material_id, require_current=False)
        self.assert_history_rejected(material_id)

    def test_single_original_profile_read_and_snapshot_repository_leave_history_unchanged(self) -> None:
        material_id = self.build()
        self.retire(self.claim_ids[0])
        snapshot = self.repository._connection.serialize()
        original = ProfileService.validated_profile
        calls = []

        def read_original(service: ProfileService, *, apply_retirements: bool = True):
            calls.append(apply_retirements)
            return original(service, apply_retirements=apply_retirements)

        with SQLiteRepository.from_snapshot(snapshot) as repository:
            service = MaterialService(repository, SyntheticRenderer())
            with patch.object(ProfileService, "validated_profile", autospec=True, side_effect=read_original), patch.object(
                service, "plan", side_effect=AssertionError("Historical validation must not reselect current facts"),
            ):
                self.assertIsNone(service.validate_historical_facts(material_id))
            self.assertFalse(repository._connection.in_transaction)
            self.assertEqual(repository.get_material_version(material_id), self.repository.get_material_version(material_id))
        self.assertEqual(calls, [False])
        self.assertEqual(self.repository._connection.serialize(), snapshot)

    def test_missing_material_is_a_fatal_history_error_not_a_readiness_blocker(self) -> None:
        self.assertTrue(issubclass(MaterialHistoryIntegrityError, RepositoryError))
        self.assertFalse(issubclass(MaterialHistoryIntegrityError, MaterialBlocked))
        self.assert_history_rejected("fictional-missing-material")

    def test_storage_errors_are_sanitized_and_interrupt_identity_is_preserved(self) -> None:
        material_id = self.build()
        before = self.repository._connection.serialize()
        with patch.object(self.repository, "get_material_version", side_effect=sqlite3.OperationalError("fictional-private-sql-detail")):
            with self.assertRaisesRegex(MaterialHistoryIntegrityError, "^Material historical facts failed integrity checks$"):
                self.service.validate_historical_facts(material_id)
        interrupt = KeyboardInterrupt("fictional cancellation")
        with patch.object(self.repository, "get_material_version", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as raised:
                self.service.validate_historical_facts(material_id)
        self.assertIs(raised.exception, interrupt)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)

    def test_deep_stored_json_maps_to_a_fixed_fatal_history_error(self) -> None:
        claim = self.generic_claim("deep-history-value")
        material_id = self.build((claim,))
        # SQLite accepts this JSON depth; recursive domain reconstruction can
        # exhaust Python's stack before reaching the ordinary packet mismatch.
        self.repository._connection.execute("UPDATE claims SET value_json = ? WHERE id = ?",
            ("[" * 995 + "0" + "]" * 995, claim))
        before = self.repository._connection.serialize()
        with self.assertRaisesRegex(MaterialHistoryIntegrityError, "^Material historical facts failed integrity checks$"):
            self.service.validate_historical_facts(material_id)
        self.assertFalse(self.repository._connection.in_transaction)
        self.assertEqual(self.repository._connection.serialize(), before)


if __name__ == "__main__":
    unittest.main()
