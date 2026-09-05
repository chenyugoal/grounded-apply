"""Immutable user-supplied job snapshots and quoted requirements."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from grounded_apply.repositories import RepositoryError, SQLiteRepository
from grounded_apply.services.workflow import (
    digest, existing_workflow, finish_workflow, hash_bytes, opaque, request_input,
    start_workflow, timestamp, validate_workflow,
)

EXTRACTOR = "grounded-apply.job-text@1"
_HEADINGS = {"requirements": "required", "required qualifications": "required",
    "minimum qualifications": "required", "preferred qualifications": "preferred",
    "preferred": "preferred", "nice to have": "preferred", "responsibilities": "responsibility",
    "what you will do": "responsibility", "qualifications": "qualification"}
_PROMPT = re.compile(r"\b(ignore|disregard|override)\b.*\b(instructions?|previous|system)\b|\b(system|assistant|developer)\s*:|\b(send|upload|reveal)\b.*\b(secret|password|token|resume|candidate)\b", re.I)


@dataclass(frozen=True, slots=True)
class Requirement:
    id: str
    position: int
    start: int
    end: int
    quote: str
    category: str
    classification_basis: str
    explicit: bool = True


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    id: str
    source_url: str
    source_text: str
    source_sha256: str
    captured_at: str
    requirements: tuple[Requirement, ...]
    suspicious_lines: int
    capture_method: str = "user_supplied_text"
    content_trust: str = "untrusted"
    live_page_verified: bool = False


def validate_job_input(url: str, source: str) -> None:
    if type(url) is not str or len(url) > 2048 or any(ord(c) <= 32 for c in url):
        raise ValueError("Job URL must be a bounded HTTPS URL without credentials, query, or fragment")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("Job URL must be HTTPS without credentials, query, or fragment")
    if type(source) is not str or not source.strip() or len(source.encode("utf-8")) > 1024 * 1024 or "\x00" in source:
        raise ValueError("Job snapshot requires bounded UTF-8 text")


def extract_requirements(job_id: str, source: str) -> tuple[tuple[Requirement, ...], int]:
    results: list[Requirement] = []
    section: str | None = None
    offset = 0
    suspicious = 0
    for raw in source.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        text = line.strip()
        start = offset + len(line) - len(line.lstrip())
        offset += len(raw)
        heading = text.casefold().rstrip(":")
        if not text:
            continue
        if _PROMPT.search(text):
            suspicious += 1
            continue
        if heading in _HEADINGS:
            section = _HEADINGS[heading]
            continue
        prefix = re.match(r"^(Required|Preferred|Responsibility):\s+", text, re.I)
        if prefix:
            category = prefix[1].casefold()
            basis = "explicit_label"
        elif section is not None and len(text) <= 2048:
            if text.isupper() and not text.startswith(("-", "*", "•")):
                section = None
                continue
            category, basis = section, "section_heading"
        else:
            continue
        if re.search(r"\b(not required|optional|may be|equivalent)\b", text, re.I):
            category, basis = "ambiguous", "qualification_or_negation_requires_review"
        index = len(results)
        results.append(Requirement(str(uuid5(NAMESPACE_URL, f"{job_id}/requirement/{index}")), index,
            start, start + len(text), text, category, basis))
        if len(results) > 200:
            raise ValueError("Job snapshot has too many requirement lines")
    return tuple(results), suspicious


class JobService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    def add(self, url: str, source: str, *, idempotency_key: str, dry_run: bool = False) -> dict[str, object]:
        validate_job_input(url, source)
        payload = request_input(idempotency_key, {"source_sha256": hash_bytes(source.encode()),
            "url_sha256": hash_bytes(url.encode()), "extractor": EXTRACTOR})
        if type(dry_run) is not bool:
            raise ValueError("Dry run must be boolean")
        if dry_run:
            requirements, suspicious = extract_requirements("preview", source)
            return {"dry_run": True, "requirement_count": len(requirements), "suspicious_lines": suspicious}
        with self._repository.transaction():
            existing = existing_workflow(self._repository, "job_capture", payload)
            if existing is not None:
                ids = json.loads(existing["generated_artifacts_json"])
                if not isinstance(ids, list) or len(ids) != 1:
                    raise RepositoryError("Job capture result failed integrity checks")
                job = self.get(ids[0])
                if job.source_url != url or job.source_text != source:
                    raise RepositoryError("Job replay does not match the snapshot")
                return {"job_id": job.id, "source_sha256": job.source_sha256, "replayed": True, "dry_run": False}
            at, job_id = timestamp(), str(uuid4())
            requirements, _ = extract_requirements(job_id, source)
            workflow = start_workflow(self._repository, "job_capture", payload, at)
            self._repository.insert_job_snapshot(job_id=job_id, source_url=url, source_text=source,
                source_sha256=payload["source_sha256"], extractor_version=EXTRACTOR, captured_at=at,
                workflow_run_id=workflow["id"])
            for r in requirements:
                self._repository.insert_job_requirement(requirement_id=r.id, job_id=job_id, position=r.position,
                    start_offset=r.start, end_offset=r.end, quoted_text=r.quote,
                    category=r.category, classification_basis=r.classification_basis)
            finish_workflow(self._repository, workflow["id"], [job_id], at)
            self.get(job_id)
            return {"job_id": job_id, "source_sha256": payload["source_sha256"], "replayed": False, "dry_run": False}

    def get(self, job_id: str) -> JobSnapshot:
        opaque(job_id)
        record = self._repository.get_job_snapshot(job_id)
        if record is None:
            raise ValueError("Job snapshot does not exist")
        try:
            validate_job_input(record["source_url"], record["source_text"])
            if record["source_sha256"] != hash_bytes(record["source_text"].encode()) or record["extractor_version"] != EXTRACTOR:
                raise ValueError
            requirements, suspicious = extract_requirements(job_id, record["source_text"])
            rows = self._repository.list_job_requirements(job_id)
            expected = [{"id": r.id, "job_id": job_id, "position": r.position, "start_offset": r.start,
                "end_offset": r.end, "quoted_text": r.quote, "category": r.category,
                "classification_basis": r.classification_basis} for r in requirements]
            if rows != expected:
                raise ValueError
            workflow = self._repository.get_workflow_run(record["workflow_run_id"])
            if workflow is None:
                raise ValueError
            payload = {"version": 1, "source_sha256": record["source_sha256"],
                "url_sha256": hash_bytes(record["source_url"].encode()), "extractor": EXTRACTOR,
                "idempotency_sha256": workflow["idempotency_key"]}
            validate_workflow(workflow, "job_capture", payload)
            if json.loads(workflow["generated_artifacts_json"]) != [job_id] or workflow["created_at"] != record["captured_at"]:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise RepositoryError("Job snapshot failed integrity checks") from None
        return JobSnapshot(job_id, record["source_url"], record["source_text"], record["source_sha256"], record["captured_at"], requirements, suspicious)

    def list(self) -> tuple[JobSnapshot, ...]:
        return tuple(self.get(r["id"]) for r in self._repository.list_job_snapshots())
