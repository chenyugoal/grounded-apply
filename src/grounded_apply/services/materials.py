"""Approved claims -> immutable, traceable resume versions -> explicit approval."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from grounded_apply.domain import Resolved, resolve_claims, to_jsonable
from grounded_apply.repositories import Record, RepositoryError, SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.matching import job_policy, terms
from grounded_apply.services.material_models import (
    PRESENTATION_TRANSFORMATIONS, TRANSFORMATIONS, FactualUnit, MaterialValidationError, RenderedResume,
    ResumeRenderer, ResumeStructure, selected_presentation, validate_layout,
)
from grounded_apply.services.profile import ProfileService, resolve_selected_claim
from grounded_apply.services.questionnaires import QuestionnaireService, validate_question_specs
from grounded_apply.services.workflow import (
    digest, existing_workflow, finish_workflow, hash_bytes, opaque, request_input,
    start_workflow, timestamp, validate_workflow,
)

CURRENT_TRANSFORMATION = "approved_text_selection@2"


class MaterialHistoryIntegrityError(RepositoryError):
    """Historical material custody failed; never an ordinary preparation blocker."""


class MaterialApprovalIntegrityError(RepositoryError):
    """Saved approval integrity failed; never an unapproved or blocked result."""


class MaterialBlocked(MaterialValidationError):
    def __init__(self, outcomes: list[Any]) -> None:
        super().__init__("Material requires information or claim review before it can be used")
        self.outcomes = outcomes


class MaterialCapacityError(MaterialValidationError):
    """A bounded material operation cannot fit inside its storage allowance."""


class MaterialRenderError(MaterialValidationError):
    """New output failed rendering; no material was persisted by this attempt."""

    def __init__(self, reason: str = "render_failed") -> None:
        messages = {
            "render_failed": "New material could not pass rendering and PDF validation",
            "layout_overflow": "Resume layout overflows; review the selected content or presentation",
            "unsupported_text": "PDF cannot preserve the selected text; review unsupported characters or local TeX packages",
            "render_timeout": "Local PDF rendering failed or exceeded its time limit",
        }
        self.reason = reason if reason in messages else "render_failed"
        super().__init__(messages[self.reason])


class MaterialDependencyError(MaterialValidationError):
    """The shared PDF environment is unavailable, rather than one bad job."""


def _missing(intent: str, question: str) -> dict[str, str]:
    return {"kind": "need_info", "intent": intent, "reason": "missing", "question": question}


def _structure(data: dict[str, Any]) -> ResumeStructure:
    if set(data) != {"job_id", "units", "schema_version", "transformation"} or data["schema_version"] != 1 or data["transformation"] not in TRANSFORMATIONS:
        raise MaterialValidationError("Unsupported material structure")
    units = []
    for unit in data["units"]:
        value = dict(unit)
        for key in ("packet_claim_ids", "evidence_ids", "requirement_ids"):
            value[key] = tuple(value[key])
        units.append(FactualUnit(**value))
    return ResumeStructure(data["job_id"], tuple(units), transformation=data["transformation"])


class MaterialService:
    def __init__(self, repository: SQLiteRepository, renderer: ResumeRenderer) -> None:
        self._repository = repository
        self._renderer = renderer

    def plan(self, job_id: str, claim_ids: tuple[str, ...], *, layout: object = None,
             transformation: str | None = None) -> ResumeStructure:
        if transformation is not None and transformation not in TRANSFORMATIONS:
            raise MaterialValidationError("Unsupported material transformation")
        styles = validate_layout(layout, claim_ids)
        if transformation == "approved_text_selection@1" and styles:
            raise MaterialValidationError("Legacy materials cannot change presentation on replay")
        if not claim_ids or len(claim_ids) > 80 or len(set(claim_ids)) != len(claim_ids):
            raise ValueError("Select one to eighty distinct claim IDs")
        for claim_id in claim_ids:
            opaque(claim_id)
        with self._repository.read_transaction():
            job = JobService(self._repository).get(job_id)
            profile = ProfileService(self._repository)
            claims, evidence = profile.validated_profile()
            by_id = {c.id: c for c in claims}
            if transformation is None:
                transformation = ("approved_text_selection@3" if any(
                    identifier in by_id and by_id[identifier].claim_type == "research_description"
                    for identifier in claim_ids) else CURRENT_TRANSFORMATION)
            if transformation not in TRANSFORMATIONS:
                raise MaterialValidationError("Unsupported material transformation")
            if transformation == "approved_text_selection@1" and styles:
                raise MaterialValidationError("Legacy materials cannot change presentation on replay")
            policy = job_policy(job_id)
            selected = list(claim_ids)
            issues: list[Any] = []
            for kind in ("candidate_name", "contact_email"):
                if not any(by_id[i].claim_type == kind for i in selected if i in by_id):
                    outcome = resolve_claims(claims, intent=kind, policy=policy, evidence=evidence)
                    if isinstance(outcome, Resolved):
                        selected.append(outcome.packet.claim_ids[0])
                    else:
                        issues.append(to_jsonable(outcome))
            units = []
            outcomes = tuple(resolve_selected_claim(claim_id, claims, evidence, policy) for claim_id in selected)
            for claim_id, outcome in zip(selected, outcomes, strict=True):
                if not isinstance(outcome, Resolved):
                    issues.append(to_jsonable(outcome))
                    continue
                claim = by_id[claim_id]
                if claim_id not in outcome.packet.claim_ids:
                    issues.append(_missing("selected_claim", "Review the selected claim again."))
                    continue
                packet = outcome.packet
                relevant = tuple(r.id for r in job.requirements if terms(r.quote) & terms(claim.canonical_text))
                presentation = selected_presentation(claim, packet, transformation, styles)
                units.append(FactualUnit(claim.id, claim.claim_type, claim.canonical_text,
                    packet.claim_ids, tuple(e.id for e in packet.evidence), relevant,
                    digest(to_jsonable(packet)), presentation))
            for kind in ("candidate_name", "contact_email"):
                if sum(u.claim_type == kind for u in units) != 1:
                    issues.append(_missing(kind, "Select exactly one approved value for this resume field."))
            if not any(u.claim_type not in {"candidate_name", "contact_email", "contact_phone", "contact_location", "contact_url"} for u in units):
                issues.append(_missing("career_evidence", "Approve and select career evidence for the resume."))
            if issues:
                raise MaterialBlocked(issues)
            return ResumeStructure(job_id, tuple(units), transformation=transformation)

    def build(self, job_id: str, claim_ids: tuple[str, ...], *, idempotency_key: str, dry_run: bool = False,
              questions: object = (), layout: object = None,
              max_database_bytes: int | None = None,
              commit_guard: Callable[[], None] | None = None,
              expected_plan_sha256: str | None = None) -> dict[str, Any]:
        if type(dry_run) is not bool:
            raise ValueError("Dry run must be boolean")
        if commit_guard is not None and not callable(commit_guard):
            raise ValueError("Material commit guard must be callable")
        if expected_plan_sha256 is not None and (type(expected_plan_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256) is None):
            raise ValueError("Expected material plan must be a SHA-256 digest")
        if max_database_bytes is not None:
            from grounded_apply.services.backup import MAX_SNAPSHOT_BYTES
            if type(max_database_bytes) is not int or not 1 <= max_database_bytes <= MAX_SNAPSHOT_BYTES:
                raise ValueError("Material storage limit must fit the supported database bound")
        styles = validate_layout(layout, claim_ids)
        transformation: str | None = None
        # A retry retains its original registered transformation, including v1.
        with self._repository.read_transaction():
            previous = self._repository.get_workflow_run_by_idempotency_key("material_build", request_input(idempotency_key, {})["idempotency_sha256"])
            if previous is not None:
                try:
                    previous_input = json.loads(previous["input_json"])
                    validate_workflow(previous, "material_build", previous_input)
                    transformation = previous_input["transformation"]
                    if transformation not in TRANSFORMATIONS:
                        raise ValueError("Unsupported stored material transformation")
                except (KeyError, TypeError, ValueError):
                    raise MaterialValidationError("Material replay audit is invalid") from None
        structure = self.plan(job_id, claim_ids, layout=layout, transformation=transformation)
        transformation = structure.transformation
        question_specs = validate_question_specs(questions)
        answers = QuestionnaireService(self._repository).prepare(job_id, question_specs)
        if expected_plan_sha256 is not None and expected_plan_sha256 != digest({"structure": asdict(structure), "answers": answers}):
            raise MaterialBlocked([{"kind": "need_info", "intent": "batch_preparation",
                "reason": "preparation_inputs_changed",
                "question": "Approved preparation inputs changed. Create a new batch to prepare a fresh version."}])
        payload = request_input(idempotency_key, {"job_id": job_id, "selected_claim_ids": list(claim_ids),
            "structure_sha256": digest(asdict(structure)), "transformation": structure.transformation,
            "question_specs_sha256": digest(question_specs), "answers_sha256": digest(answers)})
        if transformation in PRESENTATION_TRANSFORMATIONS:
            payload["presentations"] = styles
        with self._repository.read_transaction():
            existing = existing_workflow(self._repository, "material_build", payload)
            if existing is not None:
                ids = json.loads(existing["generated_artifacts_json"])
                if type(ids) is not list or len(ids) != 1:
                    raise RepositoryError("Material workflow result is invalid")
                data = self.get(ids[0])
                return {"material_id": ids[0], "bundle_sha256": data["bundle_sha256"], "replayed": True,
                        "dry_run": dry_run, "ready": self.is_approved(ids[0])}
        if dry_run:
            return {"dry_run": True, "structure": asdict(structure), "answers": answers, "ready": False, "requires_approval": True}
        try:
            rendered = self._renderer.render(structure)
            self._renderer.validate(structure, rendered)
        except MaterialValidationError as error:
            message = str(error)
            dependencies = {
                "PDF generation requires a local pdflatex installation",
                "PDF verification requires the optional grounded-apply[materials] dependency",
            }
            if message in dependencies:
                raise MaterialDependencyError(message) from None
            reasons = {
                "Resume layout overflows; shorten the selected content": "layout_overflow",
                "PDF extraction failed or resume exceeds two pages": "layout_overflow",
                "LaTeX could not render the selected text; check unsupported characters or missing TeX packages": "unsupported_text",
                "PDF is missing a critical factual unit or contains unsupported glyphs": "unsupported_text",
                "Local PDF rendering failed or exceeded its time limit": "render_timeout",
            }
            raise MaterialRenderError(reasons.get(message, "render_failed")) from None
        with self._repository.transaction():
            if commit_guard is not None:
                commit_guard()
            # Prevent a fact retired or changed while the compiler ran from
            # entering a new material version.
            if self.plan(job_id, claim_ids, layout=layout, transformation=transformation) != structure:
                raise MaterialValidationError("Approved facts changed during rendering")
            if QuestionnaireService(self._repository).prepare(job_id, question_specs) != answers:
                raise MaterialValidationError("Questionnaire evidence changed during rendering")
            existing = existing_workflow(self._repository, "material_build", payload)
            if existing is not None:
                ids = json.loads(existing["generated_artifacts_json"])
                data = self.get(ids[0])
                return {"material_id": ids[0], "bundle_sha256": data["bundle_sha256"], "replayed": True, "dry_run": False, "ready": self.is_approved(ids[0])}
            at, material_id = timestamp(), str(uuid4())
            job = JobService(self._repository).get(job_id)
            manifest = {"schema_version": 1, "renderer": rendered.renderer,
                "transformation": structure.transformation, "job_id": job_id,
                "job_source_sha256": job.source_sha256, "created_at": at,
                "pdf_sha256": hash_bytes(rendered.pdf), "latex_sha256": hash_bytes(rendered.latex.encode()),
                "text_sha256": hash_bytes(rendered.extracted_text.encode()),
                "structure_sha256": digest(asdict(structure)), "model": None, "prompt_version": None,
                "question_specs": list(question_specs), "answers": list(answers),
                "requirement_links": "inferred_shared_terms_for_review"}
            validation = {"schema_version": 1, "valid": True, "factual_units": len(structure.units),
                "page_count": rendered.page_count, "critical_fields_present": True,
                "unsupported_factual_units": 0, "human_approval_required": True}
            bundle = digest({"structure": asdict(structure), "manifest": manifest, "validation": validation})
            workflow = start_workflow(self._repository, "material_build", payload, at)
            self._repository.insert_material_version(material_id=material_id, job_id=job_id,
                structure=asdict(structure), manifest=manifest, validation=validation,
                pdf=rendered.pdf, latex=rendered.latex, extracted_text=rendered.extracted_text,
                bundle_sha256=bundle, created_at=at, workflow_run_id=workflow["id"],
                claim_ids=tuple(sorted({i for u in structure.units for i in u.packet_claim_ids}
                    | {i for a in answers for u in a["factual_units"] for i in u["claim_ids"]})))
            finish_workflow(self._repository, workflow["id"], [material_id], at)
            self.get(material_id)
            if max_database_bytes is not None and self._repository.database_size_bytes() > max_database_bytes:
                raise MaterialCapacityError("Material storage allowance reached; no new material was added")
            if commit_guard is not None:
                commit_guard()
            return {"material_id": material_id, "bundle_sha256": bundle, "replayed": False, "dry_run": False,
                    "ready": False, "requires_approval": True, "page_count": rendered.page_count}

    def get(self, material_id: str, *, require_current: bool = True) -> dict[str, Any]:
        opaque(material_id)
        record = self._repository.get_material_version(material_id)
        if record is None:
            raise ValueError("Material version does not exist")
        try:
            structure = _structure(json.loads(record["structure_json"]))
            manifest, validation = json.loads(record["manifest_json"]), json.loads(record["validation_json"])
            if record["job_id"] != structure.job_id or manifest["job_id"] != structure.job_id:
                raise ValueError
            job = JobService(self._repository).get(structure.job_id)
            rendered = RenderedResume(record["pdf_bytes"], record["latex_text"], record["extracted_text"],
                                      validation["page_count"], manifest["renderer"])
            self._renderer.validate(structure, rendered)
            if (manifest["pdf_sha256"] != hash_bytes(rendered.pdf)
                or manifest["latex_sha256"] != hash_bytes(rendered.latex.encode())
                or manifest["text_sha256"] != hash_bytes(rendered.extracted_text.encode())
                or manifest["structure_sha256"] != digest(asdict(structure))
                or manifest["transformation"] != structure.transformation
                or manifest["job_source_sha256"] != job.source_sha256
                or manifest["created_at"] != record["created_at"]
                or validation != {"schema_version": 1, "valid": True, "factual_units": len(structure.units),
                    "page_count": rendered.page_count, "critical_fields_present": True,
                    "unsupported_factual_units": 0, "human_approval_required": True}
                or record["bundle_sha256"] != digest({"structure": asdict(structure), "manifest": manifest, "validation": validation})
                or self._repository.list_material_claim_ids(material_id) != tuple(sorted({i for u in structure.units for i in u.packet_claim_ids}
                    | {i for a in manifest["answers"] for u in a["factual_units"] for i in u["claim_ids"]}))):
                raise ValueError
            workflow = self._repository.get_workflow_run(record["workflow_run_id"])
            if workflow is None:
                raise ValueError
            payload = json.loads(workflow["input_json"])
            expected = {"version": 1, "idempotency_sha256": workflow["idempotency_key"],
                "job_id": job.id, "selected_claim_ids": payload["selected_claim_ids"],
                "structure_sha256": digest(asdict(structure)), "transformation": structure.transformation,
                "question_specs_sha256": digest(manifest["question_specs"]), "answers_sha256": digest(manifest["answers"])}
            layout = None
            if structure.transformation in PRESENTATION_TRANSFORMATIONS:
                layout = {"schema_version": 1, "presentations": payload["presentations"]}
                expected["presentations"] = validate_layout(layout, tuple(payload["selected_claim_ids"]))
            validate_workflow(workflow, "material_build", expected)
            if json.loads(workflow["generated_artifacts_json"]) != [material_id] or workflow["created_at"] != record["created_at"]:
                raise ValueError
            if require_current and self.plan(job.id, tuple(payload["selected_claim_ids"]), layout=layout,
                                             transformation=structure.transformation) != structure:
                raise MaterialBlocked([_missing("claim_review", "Facts changed; generate and review a new material version.")])
            if require_current and list(QuestionnaireService(self._repository).prepare(job.id, manifest["question_specs"])) != manifest["answers"]:
                raise MaterialBlocked([_missing("answer_review", "Answer evidence changed; prepare and review the material again.")])
        except MaterialBlocked:
            raise
        except (KeyError, ValueError, TypeError):
            raise MaterialValidationError("Material version failed provenance or PDF validation") from None
        return {**record, "structure": asdict(structure), "manifest": manifest, "validation": validation}

    def validate_historical_facts(self, material_id: str) -> None:
        """Audit one saved material at creation time, without granting current use.

        This explicitly requested internal audit includes existing PDF/bundle
        checks. It neither approves material nor admits a whole-home conversion.
        """
        try:
            with self._repository.read_transaction():
                material = self.get(material_id, require_current=False)
                if material["id"] != material_id:
                    raise ValueError
                self._validate_historical_fact_snapshot(material)
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise MaterialHistoryIntegrityError("Material historical facts failed integrity checks") from None

    def _validated_historical_material(self, material_id: str) -> dict[str, tuple[Record, ...]]:
        """Audit one material in the caller's snapshot and return checked ownership."""
        material = self.get(material_id, require_current=False)
        if material["id"] != material_id:
            raise ValueError
        approval = self._repository.get_material_approval(material_id)
        if approval is None:
            self._validate_historical_fact_snapshot(material)
        else:
            self._validate_historical_approved_snapshot(material, approval, require_complete=True)
        claim_ids = {identifier for unit in material["structure"]["units"] for identifier in unit["packet_claim_ids"]}
        claim_ids |= {identifier for answer in material["manifest"]["answers"]
                      for unit in answer["factual_units"] for identifier in unit["claim_ids"]}
        workflows = [{"id": material["workflow_run_id"], "workflow_type": "material_build"}]
        if approval is not None:
            workflows.append({"id": approval["workflow_run_id"], "workflow_type": "material_approval"})
        return {
            "materials": ({"id": material_id, "workflow_run_id": material["workflow_run_id"]},),
            "claims": tuple({"material_id": material_id, "claim_id": identifier} for identifier in sorted(claim_ids)),
            "approvals": () if approval is None else
                ({"material_id": material_id, "workflow_run_id": approval["workflow_run_id"]},),
            "workflows": tuple(workflows),
        }

    def validate_historical_inventory(self) -> None:
        """Audit every material and account for all claim, approval and build links.

        Present approvals require historical factual and required-answer eligibility;
        absent approvals retain valid unapproved history. This grants no current use.
        """
        columns = {
            "materials": ("id", "workflow_run_id"),
            "claims": ("material_id", "claim_id"),
            "approvals": ("material_id", "workflow_run_id"),
            "workflows": ("id", "workflow_type"),
        }
        try:
            with self._repository.read_transaction():
                inventory = self._repository.material_history_inventory()
                if type(inventory) is not dict or set(inventory) != set(columns):
                    raise ValueError
                actual: dict[str, tuple[tuple[str, ...], ...]] = {}
                for kind, fields in columns.items():
                    rows = inventory[kind]
                    if type(rows) is not tuple:
                        raise ValueError
                    values = []
                    for row in rows:
                        if type(row) is not dict or set(row) != set(fields):
                            raise ValueError
                        values.append(tuple(opaque(row[field]) for field in fields))
                    identities = set(values) if kind == "claims" else {row[0] for row in values}
                    if len(identities) != len(values):
                        raise ValueError
                    actual[kind] = tuple(sorted(values))
                expected: dict[str, list[tuple[str, ...]]] = {kind: [] for kind in columns}
                for material_id, _ in actual["materials"]:
                    checked = self._validated_historical_material(material_id)
                    for kind, fields in columns.items():
                        expected[kind].extend(tuple(row[field] for field in fields) for row in checked[kind])
                if any(actual[kind] != tuple(sorted(expected[kind])) for kind in columns):
                    raise ValueError
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise MaterialHistoryIntegrityError("Material historical inventory failed integrity checks") from None

    def _validate_historical_fact_snapshot(self, material: dict[str, Any], *, approval_at: str | None = None,
                                         use_at: str | None = None) -> None:
        """Validate an already checked bundle within the caller's read snapshot."""
        from grounded_apply.services.material_build_history import validate_material_build_record
        from grounded_apply.services.material_history import validate_material_history
        from grounded_apply.services.profile_lifecycle import project_retirements

        workflow = self._repository.get_workflow_run(opaque(material["workflow_run_id"]))
        payload = validate_material_build_record(material, workflow)
        claims, evidence = ProfileService(self._repository).validated_profile(apply_retirements=False)
        project_retirements(self._repository, claims, evidence)
        validate_material_history(
            material, payload,
            JobService(self._repository).get(material["job_id"]), claims, evidence,
            evidence_records={item.id: self._repository.get_evidence(item.id) for item in evidence},
            support_links={(row["evidence_id"], row["claim_id"]): row
                           for row in self._repository.list_claim_evidence(relationship="supports")},
            retirements={row["claim_id"]: row["retired_at"] for row in self._repository.list_claim_retirements()},
            approval_at=approval_at, use_at=use_at,
        )

    def validate_historical_approval_facts(self, material_id: str) -> None:
        """Audit emitted facts at creation and saved approval, without readiness.

        An absent approval needs only bundle validation here; creation facts for
        unapproved materials have their separate audit. Unanswered questions stay
        unanswered; this does not establish a valid historical approval decision.
        """
        try:
            self._validate_historical_approval_snapshot(material_id, require_complete=False)
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise MaterialHistoryIntegrityError("Material historical approval facts failed integrity checks") from None

    def validate_historical_approval_eligibility(self, material_id: str) -> None:
        """Check saved approval facts and required answers, without current use.

        This verifies recorded questionnaire completeness after both factual
        clocks. It does not fill answers, authenticate an actor or reconstruct
        unknown historical states. Absence remains unapproved bundle history.
        """
        try:
            self._validate_historical_approval_snapshot(material_id, require_complete=True)
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise MaterialHistoryIntegrityError("Material historical approval eligibility failed integrity checks") from None

    def _validate_historical_approval_snapshot(self, material_id: str, *, require_complete: bool) -> None:
        """Own one snapshot for the two fixed historical approval audit modes."""
        opaque(material_id)
        with self._repository.read_transaction():
            material = self.get(material_id, require_current=False)
            if material["id"] != material_id:
                raise ValueError
            approval = self._repository.get_material_approval(material_id)
            if approval is None:
                return
            self._validate_historical_approved_snapshot(material, approval, require_complete=require_complete)

    def _validate_historical_approved_snapshot(self, material: dict[str, Any], approval: dict[str, Any], *,
                                             require_complete: bool, used_at: str | None = None) -> None:
        """Check an already validated bundle and approval in the caller's snapshot."""
        from grounded_apply.services.material_approval_history import validate_approval_record

        workflow = self._repository.get_workflow_run(opaque(approval["workflow_run_id"]))
        validate_approval_record(material, approval, workflow)
        self._validate_historical_fact_snapshot(material, approval_at=approval["approved_at"], use_at=used_at)
        # The factual audit first proves closed answer/spec shapes, exact
        # boolean flags and draft mappings. Completeness adds no resolution.
        if require_complete and any(answer["required"] and answer["status"] != "draft"
                                    for answer in material["manifest"]["answers"]):
            raise ValueError("Historical approval has an unanswered required question")

    def validate_historical_use(self, material_id: str, *, used_at: str) -> None:
        """Audit approved output at a supplied past use time, without current use.

        Approval must exist and recorded required answers must be complete.
        Creation retains authority; approval and use each check factual policy
        and retirement. Binding this time to an event belongs to the caller.
        """
        try:
            opaque(material_id)
            if type(used_at) is not str or not used_at:
                raise ValueError
            with self._repository.read_transaction():
                material = self.get(material_id, require_current=False)
                if material["id"] != material_id:
                    raise ValueError
                approval = self._repository.get_material_approval(material_id)
                if approval is None:
                    raise ValueError
                self._validate_historical_approved_snapshot(material, approval, require_complete=True, used_at=used_at)
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise MaterialHistoryIntegrityError("Material historical use failed integrity checks") from None

    def validate_historical_approval_record(self, material_id: str) -> None:
        """Audit an optional saved approval record without granting readiness.

        This checks historical bundle and approval/workflow bindings in one read
        transaction. Absence is valid unapproved history. Factual eligibility at
        creation/approval time and complete conversion admission remain separate.
        """
        from grounded_apply.services.material_approval_history import validate_approval_record

        try:
            opaque(material_id)
            with self._repository.read_transaction():
                material = self.get(material_id, require_current=False)
                if material["id"] != material_id:
                    raise ValueError
                approval = self._repository.get_material_approval(material_id)
                if approval is None:
                    return
                workflow = self._repository.get_workflow_run(opaque(approval["workflow_run_id"]))
                validate_approval_record(material, approval, workflow)
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise MaterialHistoryIntegrityError("Material historical approval record failed integrity checks") from None

    def is_approved(self, material_id: str, *, require_current: bool = True) -> bool:
        """Read readiness and a strictly validated approval in one snapshot."""
        from grounded_apply.services.material_approval_history import validate_approval_record

        try:
            with self._repository.read_transaction():
                material = self.get(material_id, require_current=require_current)
                if material["id"] != material_id:
                    raise ValueError
                approval = self._repository.get_material_approval(material_id)
                if approval is None:
                    return False
                workflow = self._repository.get_workflow_run(opaque(approval["workflow_run_id"]))
                validate_approval_record(material, approval, workflow)
                return not any(a["required"] and a["status"] != "draft" for a in material["manifest"]["answers"])
        except MaterialBlocked:
            raise
        except (RepositoryError, sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            raise MaterialApprovalIntegrityError("Material approval failed integrity checks") from None

    def list(self, job_id: str | None = None) -> tuple[dict[str, Any], ...]:
        """Find saved versions without promoting historical facts to current use."""
        with self._repository.read_transaction():
            if job_id is not None:
                JobService(self._repository).get(job_id)
            summaries = []
            for material_id in self._repository.list_material_ids(job_id):
                material = self.get(material_id, require_current=False)
                try:
                    ready = self.is_approved(material_id)
                    status = "approved" if ready else "draft"
                except MaterialBlocked:
                    ready, status = False, "needs_review"
                summaries.append({"material_id": material_id, "job_id": material["job_id"],
                    "created_at": material["created_at"], "bundle_sha256": material["bundle_sha256"],
                    "status": status, "ready": ready})
            return tuple(summaries)

    def approve(self, material_id: str, *, bundle_sha256: str, actor_id: str,
                idempotency_key: str, confirm: bool = False) -> dict[str, Any]:
        if type(confirm) is not bool:
            raise ValueError("Confirmation must be boolean")
        opaque(actor_id)
        with (self._repository.transaction() if confirm else self._repository.read_transaction()):
            material = self.get(material_id)
            unanswered = [issue for a in material["manifest"]["answers"] if a["required"] for issue in a["need_info"]]
            if unanswered:
                raise MaterialBlocked(unanswered)
            if material["bundle_sha256"] != bundle_sha256:
                raise MaterialValidationError("Material digest does not match the reviewed version")
            payload = request_input(idempotency_key, {"material_id": material_id, "bundle_sha256": bundle_sha256, "actor_id": actor_id})
            if not confirm:
                return {"material_id": material_id, "dry_run": True, "ready": False, "requires_confirmation": True}
            existing = existing_workflow(self._repository, "material_approval", payload)
            if existing is not None:
                if not self.is_approved(material_id):
                    raise MaterialValidationError("Approval replay is incomplete")
                return {"material_id": material_id, "ready": True, "replayed": True}
            if self._repository.get_material_approval(material_id) is not None:
                raise MaterialValidationError("Material was already approved; replay the original request")
            at = timestamp()
            workflow = start_workflow(self._repository, "material_approval", payload, at)
            self._repository.insert_material_approval(material_id=material_id, bundle_sha256=bundle_sha256,
                actor_id=actor_id, approved_at=at, workflow_run_id=workflow["id"])
            finish_workflow(self._repository, workflow["id"], [material_id], at)
            self.is_approved(material_id)
            return {"material_id": material_id, "ready": True, "replayed": False}
