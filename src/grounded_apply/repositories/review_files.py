"""Private, bounded copies of an already validated saved-search review."""

from __future__ import annotations

import html
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

from grounded_apply.config import RuntimePaths
from grounded_apply.repositories.backup_files import (
    _private_directory, _safe_path, _sync_directory, read_private_file, write_private_file,
)
from grounded_apply.repositories.material_files import export_material
from grounded_apply.services.backup import BackupError
from grounded_apply.services.discovery import netflix_job_url_id
from grounded_apply.services.jobs import validate_job_input
from grounded_apply.services.review_exports import ReviewMaterial, SearchReviewSnapshot
from grounded_apply.services.workflow import canonical, hash_bytes


MAX_MATERIAL_FILE_BYTES = 2 * 1024 * 1024
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_REVIEW_BYTES = 32 * 1024 * 1024
MAX_REVIEW_FILES = 403
_ROOT_FILES = {"review.md", "review.json", "export-receipt.json"}


class ReviewExportError(ValueError):
    """A review destination or immutable export packet failed validation."""


def _uuid(value: object) -> str:
    if type(value) is not str or str(UUID(value)) != value:
        raise ReviewExportError("Review identifiers must be canonical UUIDs")
    return value


def _text(value: object) -> str:
    """Render untrusted values as one inert Markdown text fragment."""
    raw = str(value) if value is not None else "not provided"
    raw = "".join(" " if character in "\r\n\t" else "\ufffd" if unicodedata.category(character).startswith("C")
                  else character for character in raw)
    escaped = html.escape(raw, quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|:/@-])", r"\\\1", escaped)


def _job_link(value: object) -> str | None:
    if (type(value) is not str or len(value) > 2048 or "\\" in value
        or any(character.isspace() or unicodedata.category(character).startswith("C") for character in value)):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        parsed.port  # Reject malformed authority/port spellings without fetching.
        try:
            validate_job_input(value, "Review link")
        except ValueError:
            netflix_job_url_id(value)
    except (ValueError, TypeError):
        return None
    # Preserve query/fragment semantics, but prevent Markdown delimiters or HTML
    # entity spelling in the original URL from changing the rendered target.
    return quote(value, safe="/:%?&=#").replace("&", "&amp;")


def _markdown(review: dict[str, Any]) -> bytes:
    lines = ["# Saved search review", "", f"Run: `{review['run_id']}`", "",
        f"Status: {_text(review.get('status'))}. Exported packages: {review['exported_material_count']}. "
        f"Omitted saved packages: {review['omitted_material_count']}.", "",
        "Exporting does not approve a package or make an application ready. Check recorded approval and remaining blockers before use. "
        "Unknown questionnaire coverage does not mean an external application form is complete.", "",
        "## Source coverage", "", f"Complete: {'yes' if review.get('coverage_complete') is True else 'no'}.", ""]
    for source in review.get("sources", []):
        report = source.get("report") or {}
        lines.append(f"- {_text(source.get('source_id'))}: {_text(source.get('stage'))}; "
            f"source result {_text(report.get('status'))}; captured {_text(source.get('captured_count', 0))}.")
        if report.get("errors"):
            lines.append(f"  Details: {_text(canonical(report['errors']))}")
    lines.extend(["", "See review.json for complete source results, skipped counts and traversal progress.", "", "## Grouped blockers", ""])
    groups = review.get("grouped_blockers", [])
    for group in groups:
        label = group.get("question_label") or group.get("question") or "Review required"
        lines.append(f"- {_text(label)} — {_text(group.get('reason', 'needs_review'))}; "
            f"affected jobs: {len(group.get('job_ids', []))}.")
    if not groups:
        lines.append("No grouped item blockers are recorded in this snapshot.")
    lines.extend(["", "## Jobs at a glance", "", "| Job | Location | Package | Links |",
                  "|---|---|---|---|"])
    package_labels = {"current_draft": "Draft", "current_partial": "Partial; questions remain",
                      "stale_omitted": "Omitted; evidence changed", "not_prepared": "Not prepared"}
    for item in review["items"]:
        links = []
        if item["exported"]:
            folder = item["job_id"]
            links.extend([f"[PDF](./{folder}/resume.pdf)", f"[Answers](./{folder}/answers.json)"])
        url = _job_link(item.get("source_url"))
        if url is not None:
            links.append(f"[Job page]({url})")
        lines.append(f"| {_text(item.get('title') or 'Saved job')} · source: {_text(item.get('source_id'))} | {_text(item.get('location'))} | "
            f"{package_labels[item['export_status']]} | {' · '.join(links) or 'No links'} |")
    if not review["items"]:
        lines.append("| No selected jobs in this run | — | — | — |")
    lines.extend(["", "Package labels do not grant approval. Exact approval state and blockers appear below."])
    lines.extend(["", "## Jobs", ""])
    for position, item in enumerate(review["items"], 1):
        lines.extend([f"### {position}. {_text(item.get('title') or 'Saved job')}", "",
            f"Job: `{item['job_id']}`", "",
            f"Location: {_text(item.get('location'))}. Source: {_text(item.get('source_id'))}.", "",
            f"Package: {_text(item['export_status'])}. Job state: {_text(item.get('status'))}. "
            f"Questionnaire coverage: {_text(item.get('questionnaire_coverage', 'unknown'))}.", ""])
        lines.extend([f"Observed bundle approval: {'approved' if item.get('material_approved') is True else 'not approved'}. "
            f"Requires approval: {'no' if item.get('requires_approval') is False else 'yes'}. "
            f"Visual review required: {'no' if item.get('visual_review_required') is False else 'yes'}.", ""])
        if item["exported"]:
            folder = item["job_id"]
            lines.extend([f"[Review PDF](./{folder}/resume.pdf) · [Review answers](./{folder}/answers.json)", ""])
        elif item["export_status"] == "stale_omitted":
            lines.extend(["Saved material omitted because current evidence no longer validates it. Resolve the blockers and prepare a new version.", ""])
        else:
            lines.extend(["No current package is available for export.", ""])
        url = _job_link(item.get("source_url"))
        if url is not None:
            lines.extend([f"[Open job page]({url})", ""])
        else:
            lines.extend(["A validated HTTPS job link is unavailable; inspect the saved job record.", ""])
        for blocker in item.get("blockers", []):
            label = blocker.get("question_label") or blocker.get("question") or "Review required"
            lines.append(f"- {_text(label)} — {_text(blocker.get('reason', 'needs_review'))}")
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _material_files(material: dict[str, Any]) -> dict[str, bytes]:
    # Match the unchanged individual exporter, which remains the writer. Exact
    # verification below detects any future divergence in that file contract.
    files = {"resume.pdf": material["pdf_bytes"], "resume.tex": material["latex_text"].encode("utf-8"),
        "resume.txt": material["extracted_text"].encode("utf-8"),
        "resume.structure.json": canonical(material["structure"]).encode("utf-8"),
        "resume.manifest.json": canonical(material["manifest"]).encode("utf-8"),
        "resume.validation.json": canonical(material["validation"]).encode("utf-8"),
        "answers.json": canonical(material["manifest"]["answers"]).encode("utf-8")}
    receipt = {"schema_version": 1, "material_id": material["id"], "bundle_sha256": material["bundle_sha256"],
        "files": {name: hash_bytes(value) for name, value in files.items()}}
    files["export-receipt.json"] = canonical(receipt).encode("utf-8")
    if any(type(value) is not bytes or len(value) > MAX_MATERIAL_FILE_BYTES for value in files.values()):
        raise ReviewExportError("A material export file exceeds the supported two MiB bound")
    return files


def _plan(packet: SearchReviewSnapshot) -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    try:
        if (type(packet) is not SearchReviewSnapshot or type(packet.review_json) is not str
            or len(packet.review_json.encode("utf-8")) > MAX_INDEX_BYTES
            or type(packet.materials) is not tuple or len(packet.materials) > 50):
            raise ValueError
        _uuid(packet.run_id)
        review = json.loads(packet.review_json)
        if (type(review) is not dict or review.get("schema_version") != 1 or type(review.get("schema_version")) is not int
            or review.get("run_id") != packet.run_id or canonical(review) != packet.review_json
            or type(review.get("items")) is not list or len(review["items"]) > 50
            or any(review.get(key) is not False for key in ("application_ready", "approvals_recorded", "external_action_taken"))):
            raise ValueError
        items = {}
        for item in review["items"]:
            job_id = _uuid(item["job_id"])
            if (job_id in items or type(item.get("exported")) is not bool
                or item.get("export_status") not in {"current_draft", "current_partial", "stale_omitted", "not_prepared"}
                or item["exported"] != (item["export_status"] in {"current_draft", "current_partial"})):
                raise ValueError
            items[job_id] = item
        materials, files = {}, {}
        for entry in packet.materials:
            if (type(entry) is not ReviewMaterial or type(entry.material_json) is not str
                or len(entry.material_json.encode("utf-8")) > MAX_INDEX_BYTES or type(entry.pdf_bytes) is not bytes):
                raise ValueError
            job_id = _uuid(entry.job_id)
            material = json.loads(entry.material_json)
            if (job_id in materials or canonical(material) != entry.material_json or "pdf_bytes" in material
                or material["job_id"] != job_id or job_id not in items or not items[job_id]["exported"]
                or material["id"] != items[job_id].get("material_id")
                or material["bundle_sha256"] != items[job_id].get("bundle_sha256")
                or material["manifest"]["pdf_sha256"] != hash_bytes(entry.pdf_bytes)):
                raise ValueError
            _uuid(material["id"])
            material["pdf_bytes"] = entry.pdf_bytes
            materials[job_id] = material
            files.update({f"{job_id}/{name}": value for name, value in _material_files(material).items()})
        if (set(materials) != {job_id for job_id, item in items.items() if item["exported"]}
            or type(review.get("exported_material_count")) is not int or review["exported_material_count"] != len(materials)
            or type(review.get("omitted_material_count")) is not int
            or review["omitted_material_count"] != sum(item["export_status"] == "stale_omitted" for item in items.values())):
            raise ValueError
        files["review.json"] = packet.review_json.encode("utf-8")
        files["review.md"] = _markdown(review)
        if len(files["review.md"]) > MAX_INDEX_BYTES:
            raise ReviewExportError("Review index exceeds its supported size")
        receipt = {"schema_version": 1, "run_id": packet.run_id,
            "files": {name: hash_bytes(value) for name, value in files.items()}}
        files["export-receipt.json"] = canonical(receipt).encode("utf-8")
        if (len(files) > MAX_REVIEW_FILES or sum(map(len, files.values())) > MAX_REVIEW_BYTES
            or len(files["export-receipt.json"]) > MAX_RECEIPT_BYTES):
            raise ReviewExportError("Review export exceeds its bounded file or byte allowance")
        return files, materials
    except ReviewExportError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
        raise ReviewExportError("Review export packet failed validation") from None


def _entries(directory: Path, expected: set[str]) -> None:
    _private_directory(directory)
    found = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in expected or entry.name in found:
                raise ReviewExportError("Review destination has unknown or incomplete contents; use a new destination")
            found.add(entry.name)
    if found != expected:
        raise ReviewExportError("Review destination has unknown or incomplete contents; use a new destination")


def _verify(destination: Path, files: dict[str, bytes], jobs: set[str]) -> None:
    _entries(destination, _ROOT_FILES | jobs)
    for job_id in jobs:
        _entries(destination / job_id, {name.split("/", 1)[1] for name in files if name.startswith(job_id + "/")})
    for name, value in files.items():
        limit = MAX_RECEIPT_BYTES if name == "export-receipt.json" else MAX_INDEX_BYTES if name in {"review.md", "review.json"} else MAX_MATERIAL_FILE_BYTES
        if read_private_file(destination / name, limit=limit) != value:
            raise ReviewExportError("Review destination changed; retry the exact unchanged request or use a new destination")


def export_search_review(packet: SearchReviewSnapshot, destination: Path, *, paths: RuntimePaths,
                         dry_run: bool = False) -> dict[str, Any]:
    """Copy a fully validated snapshot; never approve, overwrite or repair."""
    if type(dry_run) is not bool or not isinstance(destination, Path) or type(paths) is not RuntimePaths:
        raise ReviewExportError("Review export arguments are invalid")
    files, materials = _plan(packet)
    try:
        _safe_path(destination)
        if destination.is_symlink():
            raise ReviewExportError("Review destination cannot be a symlink")
        destination = destination.parent.resolve() / destination.name
        roots = (paths.config_dir, paths.data_dir, paths.cache_dir, paths.state_dir)
        if paths.portable_root is not None:
            roots += (paths.portable_root,)
        if any(destination.is_relative_to(root.resolve()) or root.resolve().is_relative_to(destination) for root in roots):
            raise ReviewExportError("Review export must be outside every managed runtime directory")
        parent_identity = (destination.parent.stat().st_dev, destination.parent.stat().st_ino)
        replayed = destination.exists()
        if replayed:
            _verify(destination, files, set(materials))
        elif not dry_run:
            destination.mkdir(mode=0o700)
            _private_directory(destination)
            _sync_directory(destination.parent)
            identity = (destination.stat().st_dev, destination.stat().st_ino)
            def guard() -> None:
                _private_directory(destination.parent)
                _private_directory(destination)
                if (parent_identity != (destination.parent.stat().st_dev, destination.parent.stat().st_ino)
                    or identity != (destination.stat().st_dev, destination.stat().st_ino)):
                    raise ReviewExportError("Review destination identity changed")
            for job_id, material in materials.items():
                guard()
                export_material(material, destination / job_id, portable_root=paths.portable_root)
            for name in ("review.md", "review.json", "export-receipt.json"):
                guard()
                write_private_file(destination / name, files[name])
            guard()
            _verify(destination, files, set(materials))
        return {"run_id": packet.run_id, "dry_run": dry_run, "replayed": replayed,
            "material_count": len(materials), "file_count": len(files), "total_bytes": sum(map(len, files.values())),
            "index_file": "review.md", "receipt_sha256": hash_bytes(files["export-receipt.json"]),
            "application_ready": False, "approvals_recorded": False, "external_action_taken": False,
            "managed_by_runtime_deletion": False}
    except ReviewExportError:
        raise
    except (BackupError, OSError, ValueError):
        raise ReviewExportError("Review export failed or may be incomplete. Inspect the destination; retry the exact unchanged request or use a new destination. Existing files were not overwritten") from None
