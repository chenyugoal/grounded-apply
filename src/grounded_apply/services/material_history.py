"""Pure reconstruction of saved factual output from its recorded approved packets.

Callers supply one provenance-validated snapshot, including original retirement
records. This checks historical support, not present-day approval or full profile
conversion custody. Hashes detect consistency; they do not authenticate a writer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from grounded_apply.domain import Claim, ClaimPacket, Evidence, Resolved, to_jsonable
from grounded_apply.services.jobs import JobSnapshot
from grounded_apply.services.material_models import PRESENTATION_TRANSFORMATIONS, TRANSFORMATIONS, selected_presentation, validate_layout
from grounded_apply.services.matching import job_policy, terms
from grounded_apply.services.profile import resolve_selected_claim
from grounded_apply.services.questionnaire_history import validate_historical_answers
from grounded_apply.services.workflow import digest, opaque

_UNIT_KEYS = {"claim_id", "claim_type", "text", "packet_claim_ids", "evidence_ids",
              "requirement_ids", "claim_sha256", "presentation"}
_MANIFEST_KEYS = {"schema_version", "renderer", "transformation", "job_id", "job_source_sha256",
                  "created_at", "pdf_sha256", "latex_sha256", "text_sha256", "structure_sha256",
                  "model", "prompt_version", "question_specs", "answers", "requirement_links"}
_CONTACT_TYPES = {"candidate_name", "contact_email", "contact_phone", "contact_location", "contact_url"}


def _time(value: object) -> datetime:
    if type(value) is not str:
        raise ValueError("Historical time is invalid")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Historical time is invalid")
    return result


def _ids(value: object, *, nonempty: bool = False) -> tuple[str, ...]:
    if type(value) is not list or (nonempty and not value):
        raise ValueError("Historical identifiers are invalid")
    for item in value:
        opaque(item)
    if len(set(value)) != len(value):
        raise ValueError("Historical identifiers are invalid")
    return tuple(value)


def validate_material_history(
    material: Mapping[str, Any], payload: Mapping[str, Any], job: JobSnapshot,
    claims: tuple[Claim, ...], evidence: tuple[Evidence, ...], *,
    evidence_records: Mapping[str, Mapping[str, Any] | None],
    support_links: Mapping[tuple[str, str], Mapping[str, Any]],
    retirements: Mapping[str, str],
    approval_at: str | None = None,
    use_at: str | None = None,
) -> None:
    """Require exact recorded facts at creation and optional approval/use times.

    Later profile additions never expand a historical packet. Generic records
    changed since creation cannot reconstruct the original and fail closed.
    Historical unanswered questionnaire blockers retain their closed shape;
    this does not reconstruct every past resolver decision from mutable records.
    Approval and use add policy/context clocks; original output authority must
    still predate creation. A use clock requires an approval clock. A successful
    audit does not establish required-answer completeness or current readiness.
    """
    structure = json.loads(material["structure_json"])
    manifest = material["manifest"]
    validation = material["validation"]
    if (type(structure) is not dict or set(structure) != {"job_id", "units", "schema_version", "transformation"}
        or type(structure["schema_version"]) is not int or structure["schema_version"] != 1
        or structure["transformation"] not in TRANSFORMATIONS
        or type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS
        or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
        or manifest["model"] is not None or manifest["prompt_version"] is not None
        or manifest["requirement_links"] != "inferred_shared_terms_for_review"
        or type(validation["schema_version"]) is not int or validation["schema_version"] != 1
        or type(validation["factual_units"]) is not int
        or type(validation["page_count"]) is not int or validation["page_count"] not in {1, 2}
        or type(validation["unsupported_factual_units"]) is not int
        or any(validation[key] is not True for key in ("valid", "critical_fields_present", "human_approval_required"))
        or type(payload["version"]) is not int or payload["version"] != 1):
        raise ValueError("Historical material format is invalid")
    at = _time(material["created_at"])
    approval_time = None if approval_at is None else _time(approval_at)
    use_time = None if use_at is None else _time(use_at)
    if approval_time is not None and approval_time < at:
        raise ValueError("Historical approval predates the material")
    if use_time is not None and (approval_time is None or use_time < approval_time):
        raise ValueError("Historical use lacks an earlier approval")
    if _time(job.captured_at) > at:
        raise ValueError("Historical job was captured after the material")
    selected = _ids(payload["selected_claim_ids"], nonempty=True)
    if len(selected) > 80:
        raise ValueError("Historical selection exceeds its bound")
    transformation = structure["transformation"]
    styles = validate_layout({"schema_version": 1, "presentations": payload["presentations"]}, selected) if transformation in PRESENTATION_TRANSFORMATIONS else {}
    by_id = {claim.id: claim for claim in claims}
    if len(by_id) != len(claims) or len({(item.id, item.claim_id) for item in evidence}) != len(evidence):
        raise ValueError("Historical profile identities are invalid")
    policy = job_policy(job.id, now=at)

    def check_context(selected_id: str, recorded_ids: tuple[str, ...], *, at_time: datetime) -> None:
        # Only unchanged peers known at this instant can reconstruct context.
        # This checks selected-fact context, not automatic field selection or
        # the unreconstructible past of later generic edits.
        selected_claim = by_id[selected_id]
        historical_peers = tuple(
            claim for claim in claims
            if (claim.claim_type, claim.subject_type, claim.subject_id)
               == (selected_claim.claim_type, selected_claim.subject_type, selected_claim.subject_id)
            and claim.created_at <= at_time and claim.updated_at <= at_time
            and (claim.verified_at is None or claim.verified_at <= at_time)
            and (claim.id not in retirements or _time(retirements[claim.id]) > at_time)
        )
        contextual = resolve_selected_claim(selected_id, historical_peers, evidence, job_policy(job.id, now=at_time))
        if not isinstance(contextual, Resolved) or contextual.packet.claim_ids != recorded_ids:
            raise ValueError("Historical packet omits its claim context")

    def packet_for(selected_id: str, recorded_ids: tuple[str, ...]) -> ClaimPacket:
        if (type(recorded_ids) is not tuple or not recorded_ids
            or selected_id not in recorded_ids or len(set(recorded_ids)) != len(recorded_ids)):
            raise ValueError("Historical packet membership is invalid")
        for claim_id in recorded_ids:
            opaque(claim_id)
        subset = tuple(claim for claim in claims if claim.id in recorded_ids)
        if tuple(claim.id for claim in subset) != recorded_ids:
            raise ValueError("Historical packet ordering is invalid")
        for claim in subset:
            if (claim.created_at > at or claim.updated_at > at or claim.verified_at is None
                or claim.verified_at > at
                or type(claim.verified_by) is not str or not claim.verified_by.strip()
                or claim.id in retirements and _time(retirements[claim.id]) <= at):
                raise ValueError("Historical claim was not approved at creation")
        outcome = resolve_selected_claim(selected_id, subset, evidence, policy)
        if not isinstance(outcome, Resolved) or outcome.packet.claim_ids != recorded_ids:
            raise ValueError("Historical packet is not supported")
        check_context(selected_id, recorded_ids, at_time=at)
        for item in outcome.packet.evidence:
            record = evidence_records[item.id]
            link = support_links[(item.id, item.claim_id)]
            if (record is None or item.captured_at > at
                or type(record["confirmed_by"]) is not str or not record["confirmed_by"].strip()
                or any(_time(record[key]) > at for key in ("confirmed_at", "created_at", "updated_at"))
                or _time(link["created_at"]) > at):
                raise ValueError("Historical evidence was not confirmed at creation")
        for label, policy_time in (("approval", approval_time), ("use", use_time)):
            if policy_time is None:
                continue
            if any(claim.id in retirements and _time(retirements[claim.id]) <= policy_time for claim in subset):
                raise ValueError(f"Historical packet member was retired at {label}")
            checked = resolve_selected_claim(selected_id, subset, evidence, job_policy(job.id, now=policy_time))
            if (not isinstance(checked, Resolved)
                    or digest(to_jsonable(checked.packet)) != digest(to_jsonable(outcome.packet))):
                raise ValueError(f"Historical packet was not supported at {label}")
            check_context(selected_id, recorded_ids, at_time=policy_time)
        return outcome.packet

    units = structure["units"]
    if type(units) is not list or not len(selected) <= len(units) <= len(selected) + 2:
        raise ValueError("Historical factual unit selection is invalid")
    for unit in units:
        if type(unit) is not dict or set(unit) != _UNIT_KEYS:
            raise ValueError("Historical factual unit format is invalid")
    unit_ids = tuple(unit["claim_id"] for unit in units)
    if len(set(unit_ids)) != len(unit_ids) or unit_ids[:len(selected)] != selected:
        raise ValueError("Historical factual unit ordering is invalid")
    appended_types = tuple(kind for kind in ("candidate_name", "contact_email")
                           if not any(by_id[claim_id].claim_type == kind for claim_id in selected))
    if tuple(unit["claim_type"] for unit in units[len(selected):]) != appended_types:
        raise ValueError("Historical automatic fields are invalid")
    for kind in ("candidate_name", "contact_email"):
        if sum(unit["claim_type"] == kind for unit in units) != 1:
            raise ValueError("Historical identity field is ambiguous")
    if not any(unit["claim_type"] not in _CONTACT_TYPES for unit in units):
        raise ValueError("Historical material lacks career evidence")
    for unit in units:
        packet = packet_for(unit["claim_id"], _ids(unit["packet_claim_ids"], nonempty=True))
        claim = by_id[unit["claim_id"]]
        # Evidence identifiers can repeat when one source supports two claims.
        if (type(unit["evidence_ids"]) is not list
            or tuple(unit["evidence_ids"]) != tuple(item.id for item in packet.evidence)
            or unit["claim_type"] != claim.claim_type or unit["text"] != claim.canonical_text
            or unit["claim_sha256"] != digest(to_jsonable(packet))
            or _ids(unit["requirement_ids"]) != tuple(r.id for r in job.requirements if terms(r.quote) & terms(claim.canonical_text))
            or unit["presentation"] != selected_presentation(claim, packet, transformation, styles)):
            raise ValueError("Historical factual unit does not match its packet")
    validate_historical_answers(manifest["question_specs"], manifest["answers"], packet_for)
