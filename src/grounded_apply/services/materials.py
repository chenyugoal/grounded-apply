"""Approved claims -> immutable, traceable resume versions -> explicit approval."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from grounded_apply.domain import Resolved, to_jsonable
from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.jobs import JobService
from grounded_apply.services.matching import job_policy, terms
from grounded_apply.services.material_models import (
    FactualUnit, MaterialValidationError, RenderedResume, ResumeRenderer, ResumeStructure,
)
from grounded_apply.services.profile import ProfileService
from grounded_apply.services.questionnaires import QuestionnaireService, validate_question_specs
from grounded_apply.services.workflow import (
    digest, existing_workflow, finish_workflow, hash_bytes, opaque, request_input,
    start_workflow, timestamp, validate_workflow,
)


class MaterialBlocked(MaterialValidationError):
    def __init__(self, outcomes: list[Any]) -> None:
        super().__init__("Material requires information or claim review before it can be used")
        self.outcomes = outcomes


def _missing(intent: str, question: str) -> dict[str, str]:
    return {"kind": "need_info", "intent": intent, "reason": "missing", "question": question}


def _structure(data: dict[str, Any]) -> ResumeStructure:
    if set(data) != {"job_id", "units", "schema_version", "transformation"} or data["schema_version"] != 1 or data["transformation"] != "approved_text_selection@1":
        raise MaterialValidationError("Unsupported material structure")
    units = []
    for unit in data["units"]:
        value = dict(unit)
        for key in ("packet_claim_ids", "evidence_ids", "requirement_ids"):
            value[key] = tuple(value[key])
        units.append(FactualUnit(**value))
    return ResumeStructure(data["job_id"], tuple(units))


class MaterialService:
    def __init__(self, repository: SQLiteRepository, renderer: ResumeRenderer) -> None:
        self._repository = repository
        self._renderer = renderer

    def plan(self, job_id: str, claim_ids: tuple[str, ...]) -> ResumeStructure:
        if not claim_ids or len(claim_ids) > 80 or len(set(claim_ids)) != len(claim_ids):
            raise ValueError("Select one to eighty distinct claim IDs")
        for claim_id in claim_ids:
            opaque(claim_id)
        with self._repository.read_transaction():
            job = JobService(self._repository).get(job_id)
            profile = ProfileService(self._repository)
            claims, _ = profile.validated_profile()
            by_id = {c.id: c for c in claims}
            policy = job_policy(job_id)
            selected = list(claim_ids)
            issues: list[Any] = []
            for kind in ("candidate_name", "contact_email"):
                if not any(by_id[i].claim_type == kind for i in selected if i in by_id):
                    outcome = profile.resolve(intent=kind, policy=policy)
                    if isinstance(outcome, Resolved):
                        selected.append(outcome.packet.claim_ids[0])
                    else:
                        issues.append(to_jsonable(outcome))
            units = []
            for claim_id in selected:
                outcome = profile.packet_for_claim(claim_id, policy=policy)
                if not isinstance(outcome, Resolved):
                    issues.append(to_jsonable(outcome))
                    continue
                claim = by_id[claim_id]
                if claim_id not in outcome.packet.claim_ids:
                    issues.append(_missing("selected_claim", "Review the selected claim again."))
                    continue
                packet = outcome.packet
                relevant = tuple(r.id for r in job.requirements if terms(r.quote) & terms(claim.canonical_text))
                units.append(FactualUnit(claim.id, claim.claim_type, claim.canonical_text,
                    packet.claim_ids, tuple(e.id for e in packet.evidence), relevant,
                    digest(to_jsonable(packet)),
                    "bullet" if any(re.match(r"^\s*[-*•]\s+", e.source_text or "") for e in packet.evidence)
                    else "paragraph"))
            for kind in ("candidate_name", "contact_email"):
                if sum(u.claim_type == kind for u in units) != 1:
                    issues.append(_missing(kind, "Select exactly one approved value for this resume field."))
            if not any(u.claim_type not in {"candidate_name", "contact_email", "contact_phone", "contact_location", "contact_url"} for u in units):
                issues.append(_missing("career_evidence", "Approve and select career evidence for the resume."))
            if issues:
                raise MaterialBlocked(issues)
            return ResumeStructure(job_id, tuple(units))

    def build(self, job_id: str, claim_ids: tuple[str, ...], *, idempotency_key: str, dry_run: bool = False,
              questions: object = ()) -> dict[str, Any]:
        if type(dry_run) is not bool:
            raise ValueError("Dry run must be boolean")
        structure = self.plan(job_id, claim_ids)
        question_specs = validate_question_specs(questions)
        answers = QuestionnaireService(self._repository).prepare(job_id, question_specs)
        payload = request_input(idempotency_key, {"job_id": job_id, "selected_claim_ids": list(claim_ids),
            "structure_sha256": digest(asdict(structure)), "transformation": structure.transformation,
            "question_specs_sha256": digest(question_specs), "answers_sha256": digest(answers)})
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
        rendered = self._renderer.render(structure)
        self._renderer.validate(structure, rendered)
        with self._repository.transaction():
            # Prevent a fact retired or changed while the compiler ran from
            # entering a new material version.
            if self.plan(job_id, claim_ids) != structure:
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
            validate_workflow(workflow, "material_build", expected)
            if json.loads(workflow["generated_artifacts_json"]) != [material_id] or workflow["created_at"] != record["created_at"]:
                raise ValueError
            if require_current and self.plan(job.id, tuple(payload["selected_claim_ids"])) != structure:
                raise MaterialBlocked([_missing("claim_review", "Facts changed; generate and review a new material version.")])
            if require_current and list(QuestionnaireService(self._repository).prepare(job.id, manifest["question_specs"])) != manifest["answers"]:
                raise MaterialBlocked([_missing("answer_review", "Answer evidence changed; prepare and review the material again.")])
        except MaterialBlocked:
            raise
        except (KeyError, ValueError, TypeError):
            raise MaterialValidationError("Material version failed provenance or PDF validation") from None
        return {**record, "structure": asdict(structure), "manifest": manifest, "validation": validation}

    def is_approved(self, material_id: str, *, require_current: bool = True) -> bool:
        material = self.get(material_id, require_current=require_current)
        if any(a["required"] and a["status"] != "draft" for a in material["manifest"]["answers"]):
            return False
        approval = self._repository.get_material_approval(material_id)
        if approval is None:
            return False
        workflow = self._repository.get_workflow_run(approval["workflow_run_id"])
        if workflow is None:
            raise MaterialValidationError("Material approval audit is missing")
        expected = {"version": 1, "material_id": material_id, "bundle_sha256": material["bundle_sha256"],
            "actor_id": approval["actor_id"], "idempotency_sha256": workflow["idempotency_key"]}
        validate_workflow(workflow, "material_approval", expected)
        if (approval["bundle_sha256"] != material["bundle_sha256"] or approval["approved_at"] != workflow["created_at"]
            or json.loads(workflow["generated_artifacts_json"]) != [material_id]):
            raise MaterialValidationError("Material approval does not match this version")
        return True

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
