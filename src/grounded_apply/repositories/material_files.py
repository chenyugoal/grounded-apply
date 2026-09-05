"""Explicit private copies of a validated immutable material bundle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grounded_apply.repositories.backup_files import _private_directory, _safe_path, read_private_file, write_private_file
from grounded_apply.services.material_models import MaterialValidationError
from grounded_apply.services.workflow import canonical, hash_bytes


def export_material(material: dict[str, Any], destination: Path, *, portable_root: Path | None, dry_run: bool = False) -> dict[str, Any]:
    """The application service must have validated current claims and PDF first."""
    _safe_path(destination)
    if destination.is_symlink():
        raise MaterialValidationError("Export destination cannot be a symlink")
    destination = destination.parent.resolve() / destination.name
    if portable_root is not None and destination.is_relative_to(portable_root):
        raise MaterialValidationError("Export copies must be outside the managed portable home")
    contents = {
        "resume.pdf": material["pdf_bytes"], "resume.tex": material["latex_text"].encode("utf-8"),
        "resume.txt": material["extracted_text"].encode("utf-8"),
        "resume.structure.json": canonical(material["structure"]).encode("utf-8"),
        "resume.manifest.json": canonical(material["manifest"]).encode("utf-8"),
        "resume.validation.json": canonical(material["validation"]).encode("utf-8"),
        "answers.json": canonical(material["manifest"]["answers"]).encode("utf-8"),
    }
    receipt = {"schema_version": 1, "material_id": material["id"], "bundle_sha256": material["bundle_sha256"],
               "files": {name: hash_bytes(data) for name, data in contents.items()}}
    contents["export-receipt.json"] = canonical(receipt).encode("utf-8")
    replayed = destination.exists()
    if replayed:
        _private_directory(destination)
        if {p.name for p in destination.iterdir()} != set(contents):
            raise MaterialValidationError("Export destination is incomplete or contains unrelated files")
        for name, content in contents.items():
            if read_private_file(destination / name, limit=2 * 1024 * 1024) != content:
                raise MaterialValidationError("Export destination changed; choose a new destination")
    elif not dry_run:
        try:
            destination.mkdir(mode=0o700)
            _private_directory(destination)
            for name, content in contents.items():
                write_private_file(destination / name, content)
        except Exception:
            raise MaterialValidationError("Export may be incomplete. Inspect the destination and use a new one; existing files were not overwritten") from None
    return {"material_id": material["id"], "bundle_sha256": material["bundle_sha256"],
        "dry_run": dry_run, "replayed": replayed, "files": list(contents),
        "managed_by_runtime_deletion": False, "external_action_taken": False}
