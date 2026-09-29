"""Dependency-free command line interface for the first Grounded Apply slice."""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import re
import shlex
import stat
import sys
import unicodedata
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any

from grounded_apply import __version__
from grounded_apply.config import (
    DEFAULT_CONFIG,
    HOME_ENV_VAR,
    RuntimePaths,
    UnsafeRuntimePathError,
    require_initialized_profile_storage,
    require_runtime_outside_repository,
    resolve_runtime_paths,
)
from grounded_apply.diagnostics import CommandDiagnostics
from grounded_apply.json_support import dumps
from grounded_apply.repositories.resume_documents import ResumeDocument


Command = Callable[[argparse.Namespace], int]
_MAX_PROPOSAL_INPUT_BYTES = 4 * 1024 * 1024
_IDEMPOTENCY_KEY_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
)
_POST_COMMIT_DECISION_MESSAGE = (
    "The profile review decision may already be recorded. Retry the exact same "
    "confirmed request and idempotency key to recover its result."
)


class CliInputError(ValueError):
    """A file/stdin CLI payload does not match the public input contract."""


class ProfileStorageNotInitializedError(RuntimeError):
    """A profile data command requires an initialized private database."""


class PostCommitOutputError(RuntimeError):
    """A committed operation could not report its terminal result."""


class BatchOutcomeUnknownError(RuntimeError):
    """A durable batch may have progressed before its result was reported."""


class SearchOutcomeUnknownError(RuntimeError):
    """A configured search may have progressed before output was reported."""


class ScheduleOutcomeUnknownError(RuntimeError):
    """Daily scheduling state may have changed before output was reported."""


_SCHEDULE_RECOVERY = (
    "Daily schedule state or run progress may already be saved. Inspect schedules show/list; "
    "retry configuration or changes with the same key, and tick the same schedule. "
    "Reuse the same notification ID when acknowledging delivery."
)


_SEARCH_RECOVERY = (
    "Search configuration or run progress may already be saved. Retry the same "
    "request and idempotency key, or resume the saved run ID. Inspect searches scopes/list."
)


_BATCH_RECOVERY = (
    "Batch progress may already be saved. Retry preparation with the same spec and "
    "idempotency key, or resume the same batch ID. Saved drafts still require review."
)


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output.")


def _emit(
    args: argparse.Namespace,
    *,
    command: str,
    data: Any,
    message: str,
    ok: bool = True,
    warnings: Sequence[str] = (),
    error: dict[str, str] | None = None,
) -> None:
    if getattr(args, "json", False):
        print(
            dumps(
                {
                    "command": command,
                    "data": data,
                    "error": error,
                    "ok": ok,
                    "version": __version__,
                    "warnings": list(warnings),
                }
            )
        )
        return
    print(message)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)


def _path_payload(paths: RuntimePaths) -> dict[str, str]:
    return {
        "artifacts": str(paths.artifacts),
        "cache": str(paths.cache_dir),
        "config": str(paths.config_file),
        "data": str(paths.data_dir),
        "database": str(paths.database),
        "generated": str(paths.generated),
        "state": str(paths.state_dir),
    }


def _command_paths(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    _emit(
        args,
        command="paths",
        data=_path_payload(paths),
        message="\n".join(f"{key}: {value}" for key, value in _path_payload(paths).items()),
    )
    return 0


def _repository_for(
    paths: RuntimePaths,
    *,
    read_only: bool = False,
    existing_only: bool = False,
):  # type: ignore[no-untyped-def]
    from grounded_apply.repositories import SQLiteRepository

    def validate() -> None:
        if existing_only or read_only:
            require_initialized_profile_storage(paths, read_only=read_only)
        else:
            require_runtime_outside_repository(paths)

    validate()
    before_identity: tuple[int, int] | None = None
    if paths.database.exists():
        before = paths.database.stat()
        before_identity = (before.st_dev, before.st_ino)

    repository = None
    try:
        repository = SQLiteRepository(
            paths.database,
            read_only=read_only,
            existing_only=existing_only,
        )
        validate()
        after = paths.database.stat()
        if before_identity is not None and (after.st_dev, after.st_ino) != before_identity:
            raise UnsafeRuntimePathError(
                "Profile database identity changed while SQLite was opening"
            )
    except Exception:
        if repository is not None:
            repository.close()
        raise
    return repository


def _read_schema_version(paths: RuntimePaths) -> int | None:
    if not paths.database.exists():
        return None
    from grounded_apply.repositories import inspect_schema

    require_initialized_profile_storage(paths, read_only=True)
    before = paths.database.stat()
    version = inspect_schema(paths.database)
    require_initialized_profile_storage(paths, read_only=True)
    after = paths.database.stat()
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        raise UnsafeRuntimePathError(
            "Profile database identity changed while SQLite was inspecting it"
        )
    return None if version == 0 else version


def _command_materials_preflight(args: argparse.Namespace) -> int:
    import importlib.util
    import shutil

    try:
        pypdf = "present" if importlib.util.find_spec("pypdf") is not None else "missing"
    except Exception:
        pypdf = "unknown"
    try:
        pdflatex = "present" if shutil.which("pdflatex") is not None else "missing"
    except Exception:
        pdflatex = "unknown"
    states = (pypdf, pdflatex)
    materials = "missing" if "missing" in states else "unknown" if "unknown" in states else "present"
    data = {
        "schema_version": 1,
        "check_method": "dependency_presence@1",
        "dependencies": {"pypdf": pypdf, "pdflatex": pdflatex},
        "pdf_intake": {"prerequisites": ["pypdf"], "status": pypdf},
        "pdf_materials": {"prerequisites": ["pypdf", "pdflatex"], "status": materials},
        "functional_tests_run": False,
        "profile_read": False,
        "read_only": True,
    }
    ok = materials == "present"
    message = "\n".join((
        "Optional PDF prerequisites (presence only):",
        f"pypdf: {pypdf}",
        f"pdflatex: {pdflatex}",
        f"PDF intake prerequisites: {pypdf}",
        f"PDF material output prerequisites: {materials}",
    ))
    warnings = [
        "Presence does not verify PDF parsing, rendering, fonts, TeX packages or binary compatibility.",
        "This checks optional PDF prerequisites only; base CLI health is not assessed.",
    ]
    error = None
    if not ok:
        guidance = "Optional PDF prerequisites are missing or unknown. See docs/QUICKSTART.md#start for setup guidance."
        warnings.append(guidance)
        error = {"type": "MaterialsPrerequisitesUnavailable", "message": guidance}
    try:
        _emit(args, command="doctor", data=data, message=message, ok=ok,
              warnings=warnings, error=error)
    except Exception:
        raise CliInputError("Materials prerequisite output failed; no profile state was changed") from None
    return 0 if ok else 2


def _command_doctor(args: argparse.Namespace) -> int:
    if getattr(args, "materials", False):
        return _command_materials_preflight(args)
    paths = resolve_runtime_paths()
    runtime_safe = False
    try:
        require_runtime_outside_repository(paths)
        runtime_safe = True
        runtime_check: dict[str, Any] = {
            "configured_by": HOME_ENV_VAR if os.environ.get(HOME_ENV_VAR) else "xdg_defaults",
            "ok": True,
            "paths": _path_payload(paths),
        }
    except Exception as error:
        runtime_check = {
            "configured_by": HOME_ENV_VAR if os.environ.get(HOME_ENV_VAR) else "xdg_defaults",
            "error": f"{type(error).__name__}: {error}",
            "ok": False,
            "paths": _path_payload(paths),
        }
    checks: dict[str, Any] = {
        "python": {
            "ok": sys.version_info >= (3, 12),
            "version": platform.python_version(),
        },
        "runtime_home": runtime_check,
    }
    warnings: list[str] = []
    if not runtime_safe:
        checks["database"] = {
            "error": "Database inspection was skipped because runtime paths are unsafe",
            "initialized": None,
            "ok": False,
        }
    else:
        try:
            schema_version = _read_schema_version(paths)
            checks["database"] = {
                "initialized": schema_version is not None,
                "ok": True,
                "schema_version": schema_version,
            }
            if schema_version is None:
                warnings.append(
                    "Profile storage is not initialized; run `gapply profile init`."
                )
            else:
                from dataclasses import asdict
                from grounded_apply.services.storage_capacity import storage_capacity

                require_initialized_profile_storage(paths, read_only=True)
                before = paths.database.stat()
                require_initialized_profile_storage(paths, read_only=True)
                after = paths.database.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
                ):
                    raise UnsafeRuntimePathError("Profile database changed during storage inspection")
                capacity = storage_capacity(after.st_size)
                checks["database"]["storage"] = asdict(capacity)
                if capacity.status != "available":
                    warnings.append(
                        f"Profile database uses {capacity.database_file_bytes} of {capacity.snapshot_limit_bytes} "
                        f"supported bytes; {capacity.remaining_bytes} bytes of file headroom remain. "
                        "Daily packages and history consume this allowance. This is not a backup validation "
                        "or a guarantee that the next operation will fit."
                    )
                if capacity.status == "exceeded":
                    checks["database"]["ok"] = False
        except Exception as error:  # fail closed without leaking database contents
            checks["database"] = {
                "error": f"{type(error).__name__}: {error}",
                "initialized": None,
                "ok": False,
            }

    ok = all(check.get("ok", True) for check in checks.values() if isinstance(check, dict))
    _emit(
        args,
        command="doctor",
        data={"checks": checks},
        message="Grounded Apply is healthy." if ok else "Grounded Apply needs attention.",
        ok=ok,
        warnings=warnings,
    )
    return 0 if ok else 2


_DEFAULT_CONFIG = DEFAULT_CONFIG


def _existing_private_config(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError) as error:
        raise UnsafeRuntimePathError(
            "Private configuration metadata could not be validated safely"
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeRuntimePathError(
            "Existing private configuration must be a regular non-symlink file"
        )
    if metadata.st_nlink != 1:
        raise UnsafeRuntimePathError(
            "Existing private configuration must have exactly one hard link"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise UnsafeRuntimePathError(
            "Existing private configuration must have no group or other access"
        )
    return metadata


def _ensure_default_config(paths: RuntimePaths) -> bool:
    if _existing_private_config(paths.config_file) is not None:
        return False
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(paths.config_file, flags, 0o600)
    except FileExistsError:
        if _existing_private_config(paths.config_file) is not None:
            return False
        raise UnsafeRuntimePathError(
            "Private configuration appeared during exclusive creation"
        ) from None
    except OSError as error:
        raise UnsafeRuntimePathError(
            "Private configuration could not be created safely"
        ) from error

    encoded = _DEFAULT_CONFIG.encode("utf-8")
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("private configuration write made no progress")
            remaining = remaining[written:]
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_size != len(encoded)
        ):
            raise UnsafeRuntimePathError(
                "Private configuration changed during creation"
            )
    except UnsafeRuntimePathError:
        raise
    except OSError as error:
        raise UnsafeRuntimePathError(
            "Private configuration could not be written safely"
        ) from error
    finally:
        os.close(descriptor)

    current = _existing_private_config(paths.config_file)
    if current is None or (current.st_dev, current.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        raise UnsafeRuntimePathError(
            "Private configuration identity changed during creation"
        )
    return True


def _command_profile_init(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    require_runtime_outside_repository(paths)
    config_exists = _existing_private_config(paths.config_file) is not None
    if args.dry_run:
        _emit(
            args,
            command="profile.init",
            data={
                "database": str(paths.database),
                "dry_run": True,
                "would_create_config": not config_exists,
                "would_initialize_database": not paths.database.exists(),
            },
            message=f"Would initialize private profile storage at {paths.database}",
        )
        return 0
    paths.ensure_private_directories()
    config_created = _ensure_default_config(paths)
    require_runtime_outside_repository(paths)
    repository = _repository_for(paths)
    try:
        repository.initialize()
        schema_version = repository.schema_version
    finally:
        repository.close()
    paths.database.chmod(0o600)
    data = {
        "config_created": config_created,
        "database": str(paths.database),
        "schema_version": schema_version,
    }
    _emit(
        args,
        command="profile.init",
        data=data,
        message=f"Initialized private profile storage at {paths.database}",
    )
    return 0


def _read_utf8_input(location: str, *, label: str, max_bytes: int) -> str:
    try:
        if location == "-":
            binary_stream = getattr(sys.stdin, "buffer", None)
            if binary_stream is not None:
                raw = binary_stream.read(max_bytes + 1)
            else:
                text = sys.stdin.read(max_bytes + 1)
                raw = text.encode("utf-8")
        else:
            path = Path(location)
            path_stat = path.lstat()
            if not stat.S_ISREG(path_stat.st_mode):
                raise CliInputError(f"{label.capitalize()} must be a regular file")
            nofollow = getattr(os, "O_NOFOLLOW", None)
            nonblock = getattr(os, "O_NONBLOCK", None)
            if (
                not isinstance(nofollow, int)
                or nofollow <= 0
                or not isinstance(nonblock, int)
                or nonblock <= 0
            ):
                raise CliInputError(
                    f"Secure {label} capture is unavailable on this platform"
                )
            flags = os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(path, flags)
            try:
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or (opened_stat.st_dev, opened_stat.st_ino)
                    != (path_stat.st_dev, path_stat.st_ino)
                ):
                    raise CliInputError(
                        f"{label.capitalize()} changed before it could be read"
                    )
                if opened_stat.st_size > max_bytes:
                    raise CliInputError(f"{label.capitalize()} exceeds the size limit")
                chunks: list[bytes] = []
                remaining = max_bytes + 1
                while remaining:
                    chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                final_stat = os.fstat(descriptor)
                if (
                    (final_stat.st_dev, final_stat.st_ino, final_stat.st_size)
                    != (opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_size)
                    or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
                    or final_stat.st_ctime_ns != opened_stat.st_ctime_ns
                    or len(raw) != final_stat.st_size
                ):
                    raise CliInputError(
                        f"{label.capitalize()} changed while it was being read"
                    )
            finally:
                os.close(descriptor)
        if len(raw) > max_bytes:
            raise CliInputError(f"{label.capitalize()} exceeds the size limit")
        return raw.decode("utf-8")
    except CliInputError:
        raise
    except (OSError, UnicodeError) as error:
        raise CliInputError(f"Could not read {label} as UTF-8 text") from error


def _reject_nonfinite_json(value: str) -> Any:
    del value
    raise CliInputError("Proposal input must contain only finite JSON numbers")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CliInputError("Proposal input contains a duplicate object field")
        result[key] = value
    return result


def _load_proposal_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(
            text,
            parse_constant=_reject_nonfinite_json,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except json.JSONDecodeError as error:
        raise CliInputError(
            "Proposal input is not valid JSON "
            f"at line {error.lineno}, column {error.colno}"
        ) from None
    if not isinstance(value, dict):
        raise CliInputError("Proposal input must be a JSON object")
    return value


def _check_fields(
    value: dict[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> None:
    if set(value) - required - optional:
        raise CliInputError(f"{label} contains an unexpected field")
    if required - set(value):
        raise CliInputError(f"{label} is missing a required field")


def _required_text(
    value: object,
    *,
    label: str,
    max_length: int | None = None,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CliInputError(f"{label} must be non-blank text")
    if max_length is not None and len(value) > max_length:
        raise CliInputError(f"{label} exceeds the length limit")
    return value


def _validated_idempotency_key(value: object) -> str:
    key = _required_text(value, label="idempotency key", max_length=256)
    if not key[0].isalnum() or not key.isascii() or any(
        character not in _IDEMPOTENCY_KEY_CHARACTERS for character in key
    ):
        raise CliInputError("idempotency key must be an opaque identifier")
    return key


def _import_request_from_inputs(
    *,
    source_text: str,
    proposal_text: str,
    idempotency_key: str,
):  # type: ignore[no-untyped-def]
    from grounded_apply.services import (
        CreateImportProposal,
        PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION,
        ProposedImportClaim,
        TextSourceSpan,
    )

    data = _load_proposal_object(proposal_text)
    _check_fields(
        data,
        required=frozenset(
            {
                "schema_version",
                "source_sha256",
                "span_index_base",
                "span_unit",
                "span_end",
                "proposals",
            }
        ),
        label="Proposal input",
    )
    if (
        type(data["schema_version"]) is not int
        or data["schema_version"] != PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION
    ):
        raise CliInputError(
            "Proposal input schema_version must be "
            f"{PROFILE_IMPORT_MANIFEST_SCHEMA_VERSION}"
        )
    if data["span_index_base"] != 0 or type(data["span_index_base"]) is not int:
        raise CliInputError("Proposal input spans must use a zero-based index")
    if data["span_unit"] != "unicode_codepoint":
        raise CliInputError("Proposal input spans must use Unicode code points")
    if data["span_end"] != "exclusive":
        raise CliInputError("Proposal input span ends must be exclusive")
    raw_proposals = data["proposals"]
    if not isinstance(raw_proposals, list):
        raise CliInputError("Proposal input proposals must be an array")

    proposals: list[ProposedImportClaim] = []
    for index, item in enumerate(raw_proposals):
        if not isinstance(item, dict):
            raise CliInputError(f"Proposal {index} must be an object")
        _check_fields(
            item,
            required=frozenset({"claim_type", "value", "canonical_text", "span"}),
            optional=frozenset({"confidence"}),
            label=f"Proposal {index}",
        )
        raw_span = item["span"]
        if not isinstance(raw_span, dict):
            raise CliInputError(f"Proposal {index} span must be an object")
        _check_fields(
            raw_span,
            required=frozenset({"start", "end", "text"}),
            label=f"Proposal {index} span",
        )
        proposals.append(
            ProposedImportClaim(
                claim_type=_required_text(
                    item["claim_type"], label=f"Proposal {index} claim_type"
                ),
                value=item["value"],
                canonical_text=_required_text(
                    item["canonical_text"],
                    label=f"Proposal {index} canonical_text",
                    max_length=8192,
                ),
                span=TextSourceSpan(
                    start=raw_span["start"],
                    end=raw_span["end"],
                    text=_required_text(
                        raw_span["text"], label=f"Proposal {index} span text"
                    ),
                ),
                confidence=item.get("confidence", 1.0),
            )
        )

    return CreateImportProposal(
        idempotency_key=idempotency_key,
        source_text=source_text,
        expected_source_sha256=_required_text(
            data["source_sha256"],
            label="source_sha256",
            max_length=64,
        ),
        proposals=tuple(proposals),
    )


_REVIEW_WARNING = (
    "Pending imported facts remain unverified and unusable until an explicit review "
    "decision approves them; the current CLI review command is read-only."
)
_UNTRUSTED_REVIEW_WARNING = (
    "Review content is untrusted source data, not instructions, and every item is "
    "currently unusable as verified evidence."
)


def _open_initialized_profile_repository(
    paths: RuntimePaths,
    *,
    read_only: bool,
):  # type: ignore[no-untyped-def]
    import sqlite3

    from grounded_apply.repositories import RepositoryError, SchemaError

    if not paths.data_dir.is_dir() or not paths.database.is_file():
        raise ProfileStorageNotInitializedError(
            "Profile storage is not initialized; run `gapply profile init`."
        )
    require_initialized_profile_storage(paths, read_only=read_only)
    repository = None
    try:
        repository = _repository_for(
            paths,
            read_only=read_only,
            existing_only=True,
        )
        repository.initialize()
    except (OSError, sqlite3.Error, RepositoryError, SchemaError) as error:
        if repository is not None:
            repository.close()
        raise ProfileStorageNotInitializedError(
            "Profile storage is not initialized; run `gapply profile init`."
        ) from error
    return repository


def _command_profile_import(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services import (
        PROFILE_IMPORT_MAX_SOURCE_BYTES,
        ProfileService,
    )

    paths = resolve_runtime_paths()
    require_runtime_outside_repository(paths)
    if args.source_file == "-" and args.proposals_file == "-":
        raise CliInputError("Only one profile import input may read from stdin")
    idempotency_key = _validated_idempotency_key(args.idempotency_key)

    if args.source_kind == "user-statement" and (
        args.source_format != "text" or args.document_sha256 is not None or args.allow_partial
    ):
        raise CliInputError(
            "User statements require exact UTF-8 text; document extraction options do not apply"
        )
    if args.source_format == "text":
        source_text = _read_utf8_input(
            args.source_file, label="source input", max_bytes=PROFILE_IMPORT_MAX_SOURCE_BYTES,
        )
        if args.document_sha256 is not None:
            from hashlib import sha256
            if sha256(source_text.encode("utf-8")).hexdigest() != args.document_sha256:
                raise CliInputError("Resume document changed since extraction; extract and review again")
    else:
        document = _read_resume_input(args)
        _validate_resume_document_selection(args, document)
        source_text = document.text
    proposal_text = _read_utf8_input(
        args.proposals_file,
        label="proposal input",
        max_bytes=_MAX_PROPOSAL_INPUT_BYTES,
    )
    request = _import_request_from_inputs(
        source_text=source_text,
        proposal_text=proposal_text,
        idempotency_key=idempotency_key,
    )
    if args.source_kind == "user-statement":
        from dataclasses import replace
        from grounded_apply.domain import SourceType
        request = replace(request, source_type=SourceType.USER_STATEMENT)
    if args.retain_all_facts:
        from dataclasses import replace
        from grounded_apply.services.profile_import_validation import PROFILE_IMPORT_COMPLETE_FACTS_POLICY_VERSION
        request = replace(request, content_policy_version=PROFILE_IMPORT_COMPLETE_FACTS_POLICY_VERSION)
    preview = ProfileService.preview_import_proposal(request)
    if args.dry_run:
        data = to_jsonable(preview)
        assert isinstance(data, dict)
        data["dry_run"] = True
        data["storage_checked"] = False
        if args.source_kind == "user-statement":
            data["source_type"] = "user_statement"
        _emit(
            args,
            command="profile.import",
            data=data,
            message=(
                f"Validated a request plan for {preview.proposal_count} review-only "
                "profile proposals; runtime containment plus database/sidecar type, "
                "link, and orphan safety were checked, but the repository, schema, "
                "and database permissions were not checked, and no files or records "
                "were changed."
            ),
            warnings=(_REVIEW_WARNING,),
        )
        return 0

    repository = _open_initialized_profile_repository(paths, read_only=False)
    try:
        result = ProfileService(repository).create_import_proposal(request)
    finally:
        repository.close()
    review_required = result.review_required
    pending_count = sum(
        claim.status.value == "needs_review"
        and claim.approval_status.value == "pending"
        for claim in result.claims
    )
    _emit(
        args,
        command="profile.import",
        data={
            "claim_count": len(result.claims),
            "claim_ids": [claim.id for claim in result.claims],
            "dry_run": False,
            "evidence_count": len(result.evidence),
            "evidence_ids": [evidence.id for evidence in result.evidence],
            "extractor_id": result.extractor_id,
            "review_required": review_required,
            "source_artifact_id": result.source_artifact_id,
            "source_ref": result.source_ref,
            "source_sha256": result.source_sha256,
            "workflow_run_id": result.workflow_run_id,
            **({"source_type": "user_statement"} if args.source_kind == "user-statement" else {}),
        },
        message=(
            (
                f"The import has {pending_count} of {len(result.claims)} profile "
                "proposal(s) awaiting review. Run `gapply profile review` to "
                "inspect them."
            )
            if review_required
            else (
                f"The existing import has {len(result.claims)} profile proposal(s); "
                "none are awaiting review."
            )
        ),
        warnings=(_REVIEW_WARNING,) if review_required else (),
    )
    return 0


def _terminal_safe(value: str) -> str:
    result: list[str] = []
    for character in value:
        category = unicodedata.category(character)
        if category.startswith("C") or category in {"Zl", "Zp"}:
            codepoint = ord(character)
            escape = f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}"
            result.append(escape)
        else:
            result.append(character)
    return "".join(result)


def _review_message(items: Sequence[Any]) -> str:
    if not items:
        return "No profile claims are awaiting review. No records were changed."
    lines = [f"{len(items)} profile claim(s) await review (read-only):"]
    for item in items:
        claim = item.claim
        value = json.dumps(
            claim.value_json,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        lines.append(
            f"- [{_terminal_safe(claim.id)}] {_terminal_safe(claim.claim_type)}: "
            f"{_terminal_safe(claim.canonical_text)}"
        )
        lines.append(f"  Value: {_terminal_safe(value)}")
        scope = claim.scope.type.value
        if claim.scope.id is not None:
            scope = f"{scope}:{claim.scope.id}"
        lines.append(
            "  Review metadata: "
            f"confidence={claim.confidence:.3f}; "
            f"sensitivity={_terminal_safe(claim.sensitivity.value)}; "
            f"scope={_terminal_safe(scope)}; "
            f"source={_terminal_safe(claim.source_type.value)}"
        )
        if claim.source_ref is not None:
            lines.append(f"  Source reference: {_terminal_safe(claim.source_ref)}")
        for evidence in item.evidence:
            if evidence.source_text is not None:
                lines.append(f"  Evidence: {_terminal_safe(evidence.source_text)}")
    lines.append("No records were changed; this CLI command does not record decisions.")
    return "\n".join(lines)


def _command_profile_review_selected(args: argparse.Namespace, claim_id: str) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services import ProfileService

    try:
        paths = resolve_runtime_paths()
        require_runtime_outside_repository(paths)
        with _open_initialized_profile_repository(paths, read_only=True) as repository:
            selection = ProfileService(repository).get_review_item(claim_id)
            items = (selection.item,)
            data = {"items": to_jsonable(items), "pending_count": selection.pending_count,
                    "read_only": True}
            message = "\n".join([
                f"Showing one selected fact of {selection.pending_count} pending profile claim(s) (read-only).",
                *_review_message(items).splitlines()[1:],
            ])
    except Exception:
        raise CliInputError(
            "Selected pending fact could not be reviewed. Refresh profile inventory or "
            "the pending review queue; no state was changed"
        ) from None
    try:
        _emit(args, command="profile.review", data=data, message=message,
              warnings=(_UNTRUSTED_REVIEW_WARNING, _REVIEW_WARNING))
    except Exception:
        raise CliInputError("Selected profile review output failed; no state was changed") from None
    return 0


def _command_profile_review(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services import ProfileService
    from grounded_apply.services.profile import (
        validate_profile_review_page_request, validate_profile_review_selection_request,
    )

    if args.claim_id is not None or args.claim_id_json is not None:
        if args.limit is not None or args.after is not None or args.after_json is not None:
            raise CliInputError("Choose a single pending fact or a review page, not both")
        claim_id = args.claim_id
        if args.claim_id_json is not None:
            try:
                claim_id = json.loads(args.claim_id_json)
            except (ValueError, UnicodeError, RecursionError):
                raise CliInputError("Review selection JSON must contain one nonblank string") from None
        try:
            validate_profile_review_selection_request(claim_id=claim_id)
        except Exception:
            raise CliInputError("Review selection requires one nonblank UTF-8 claim ID") from None
        return _command_profile_review_selected(args, claim_id)

    if (args.after is not None or args.after_json is not None) and args.limit is None:
        raise CliInputError("Pass --limit with a continuation anchor to continue a profile review page")
    anchor = args.after
    if args.after_json is not None:
        try:
            anchor = json.loads(args.after_json)
        except (ValueError, UnicodeError, RecursionError):
            raise CliInputError("Review continuation JSON must contain one nonblank string") from None
        if type(anchor) is not str:
            raise CliInputError("Review continuation JSON must contain one nonblank string")
    if args.limit is not None:
        validate_profile_review_page_request(limit=args.limit, after_claim_id=anchor)

    paths = resolve_runtime_paths()
    require_runtime_outside_repository(paths)
    repository = _open_initialized_profile_repository(paths, read_only=True)
    try:
        service = ProfileService(repository)
        if args.limit is None:
            items = service.list_review_items()
            data = {"items": to_jsonable(items), "pending_count": len(items), "read_only": True}
            message = _review_message(items)
        else:
            page = service.list_review_page(limit=args.limit, after_claim_id=anchor)
            data = {"items": to_jsonable(page.items), "pending_count": page.pending_count,
                    "read_only": True, "page": {
                        "limit": page.limit, "returned_count": page.returned_count,
                        "pending_before_count": page.pending_before_count,
                        "pending_after_count": page.pending_after_count,
                        "next_after": page.next_after,
                    }}
            lines = [f"Showing {page.returned_count} of {page.pending_count} pending profile claim(s) (read-only).",
                     f"{page.pending_before_count} earlier pending; {page.pending_after_count} later pending."]
            if page.items:
                lines.extend(_review_message(page.items).splitlines()[1:])
            else:
                lines.append("No records were changed; this CLI command does not record decisions.")
            if page.next_after is not None:
                if _terminal_safe(page.next_after) == page.next_after:
                    lines.append(f"Continue with gapply profile review --limit {page.limit} --after={shlex.quote(page.next_after)}")
                else:
                    encoded_anchor = json.dumps(page.next_after, ensure_ascii=True)
                    lines.append(f"Continue with gapply profile review --limit {page.limit} --after-json={shlex.quote(encoded_anchor)}")
            elif page.pending_before_count:
                lines.append(f"End of this pass; {page.pending_before_count} earlier fact(s) still await review. "
                             "Restart without --after or --after-json to revisit them.")
            elif page.pending_count == 0:
                lines.append("No profile claims are awaiting review.")
            message = "\n".join(lines)
    finally:
        repository.close()
    _emit(
        args,
        command="profile.review",
        data=data,
        message=message,
        warnings=(_UNTRUSTED_REVIEW_WARNING, _REVIEW_WARNING),
    )
    return 0


def _profile_review_decision_request(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from grounded_apply.domain import ApprovalStatus
    from grounded_apply.services import CreateProfileReviewDecision

    decisions = {
        "approve": ApprovalStatus.APPROVED,
        "reject": ApprovalStatus.REJECTED,
    }
    decision = decisions.get(args.decision)
    if decision is None:
        raise CliInputError("decision must be approve or reject")
    return CreateProfileReviewDecision(
        claim_id=args.claim_id,
        review_token=args.review_token,
        decision=decision,
        actor_id=args.actor_id,
        idempotency_key=args.idempotency_key,
    )


def _command_profile_decide(args: argparse.Namespace) -> int:
    from grounded_apply.domain import Contradiction, to_jsonable
    from grounded_apply.services import ProfileService, ProfileReviewDecisionResult

    request = _profile_review_decision_request(args)
    if not args.confirm:
        _emit(
            args,
            command="profile.decide",
            data={
                "actor_id": request.actor_id,
                "claim_id": request.claim_id,
                "decision": request.decision.value,
                "decision_recorded": False,
                "dry_run": True,
                "requires_confirmation": True,
                "storage_checked": False,
            },
            message=(
                f"Validated only the input shape for a {request.decision.value} "
                f"review-decision preview for claim {request.claim_id}. Storage was "
                "not opened, so inspect the item with `gapply profile review`; no "
                "decision or external action was recorded. Re-run with --confirm to "
                "perform storage and policy checks and record this decision."
            ),
        )
        return 0

    paths = resolve_runtime_paths()
    require_runtime_outside_repository(paths)
    repository = _open_initialized_profile_repository(paths, read_only=False)
    outcome = None
    try:
        outcome = ProfileService(repository).decide_review_item(request)
    finally:
        try:
            repository.close()
        except (Exception, KeyboardInterrupt) as error:
            if isinstance(outcome, ProfileReviewDecisionResult):
                raise PostCommitOutputError(_POST_COMMIT_DECISION_MESSAGE) from error
            raise

    if isinstance(outcome, Contradiction):
        _emit(
            args,
            command="profile.decide",
            data={
                "conflicting_claim_ids": list(outcome.conflicting_claim_ids),
                "decision_recorded": False,
                "detail": outcome.detail,
                "external_action_taken": False,
                "intent": outcome.intent,
                "kind": outcome.kind.value,
                "question": outcome.question,
                "scope": to_jsonable(outcome.requested_scope),
            },
            message=(
                f"The review decision was not recorded: {outcome.question} "
                "No external action was taken."
            ),
            ok=False,
            error={
                "message": "Profile review approval is blocked by a contradiction",
                "type": "Contradiction",
            },
        )
        return 2

    if not isinstance(outcome, ProfileReviewDecisionResult):
        raise RuntimeError("Profile review decision returned an unsupported result")
    try:
        decided_at = to_jsonable(outcome.decided_at)
        _emit(
            args,
            command="profile.decide",
            data={
                "actor_id": outcome.actor_id,
                "claim_approval_status": outcome.claim.approval_status.value,
                "claim_id": outcome.claim.id,
                "claim_status": outcome.claim.status.value,
                "decided_at": decided_at,
                "decision": outcome.decision.value,
                "decision_recorded": True,
                "decision_workflow_run_id": outcome.decision_workflow_run_id,
                "dry_run": False,
                "evidence_confirmation_status": (
                    outcome.evidence.confirmation_status.value
                ),
                "evidence_id": outcome.evidence.id,
                "external_action_taken": False,
                "import_workflow_run_id": outcome.import_workflow_run_id,
                "proposal_index": outcome.proposal_index,
            },
            message=(
                f"Recorded the {outcome.decision.value} review decision for claim "
                f"{outcome.claim.id}. No external action was taken."
            ),
        )
    except (Exception, KeyboardInterrupt) as error:
        raise PostCommitOutputError(_POST_COMMIT_DECISION_MESSAGE) from error
    return 0


def _read_backup_passphrase(args: argparse.Namespace, *, repeat: bool) -> bytes:
    import getpass
    import warnings
    from grounded_apply.services.backup import BackupError, validate_passphrase

    if args.passphrase_stdin:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read(1027)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        if len(raw) > 1026:
            raise BackupError("Passphrase input exceeds its size limit")
        passphrase = raw[:-1] if raw.endswith(b"\n") else raw
        if passphrase.endswith(b"\r"):
            passphrase = passphrase[:-1]
    else:
        if not sys.stdin.isatty():
            raise BackupError("Use a no-echo terminal or explicit --passphrase-stdin")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            passphrase = getpass.getpass("Backup passphrase: ").encode("utf-8")
            if repeat and passphrase != getpass.getpass("Repeat backup passphrase: ").encode("utf-8"):
                raise BackupError("Backup passphrases do not match")
    validate_passphrase(passphrase)
    return passphrase


def _command_backup(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from grounded_apply.repositories.backup_crypto import FernetBackupCipher
    from grounded_apply.repositories.backup_files import LocalBackupStorage
    from grounded_apply.services.backup import BackupError, BackupOutcomeUnknownError, BackupService

    try:
        # Resolve the provider before requesting a secret or touching storage.
        cipher = FernetBackupCipher()
        service = BackupService(LocalBackupStorage(resolve_runtime_paths()), cipher)
        passphrase = _read_backup_passphrase(args, repeat=not args.dry_run)
        try:
            result = service.create(Path(args.encrypt), passphrase, dry_run=args.dry_run)
        finally:
            del passphrase
    except BackupError:
        raise
    except Exception:
        raise BackupOutcomeUnknownError(
            "Backup may be incomplete or already complete. Retry the same request; "
            "a partial archive requires a new output path. No existing file was overwritten."
        ) from None
    try:
        _emit(
            args, command="backup", data=asdict(result),
            message="Profile backup validated." if result.dry_run else "Encrypted profile backup ready.",
        )
    except Exception:
        raise BackupOutcomeUnknownError(
            "Backup may already be complete. Retry the same request to verify it."
        ) from None
    return 0


def _command_restore(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from grounded_apply.repositories.backup_crypto import FernetBackupCipher
    from grounded_apply.repositories.backup_files import LocalBackupStorage
    from grounded_apply.services.backup import BackupError, BackupOutcomeUnknownError, BackupService

    try:
        cipher = FernetBackupCipher()
        service = BackupService(LocalBackupStorage(), cipher)
        passphrase = _read_backup_passphrase(args, repeat=False)
        try:
            result = service.restore(
                Path(args.archive), Path(args.target_home), passphrase,
                confirm=args.confirm, expected_archive_sha256=args.archive_sha256,
            )
        finally:
            del passphrase
    except BackupError:
        raise
    except Exception:
        raise BackupOutcomeUnknownError(
            "Restore failed; no existing profile was overwritten. "
            "Retry the same request; an incomplete target requires a new target."
        ) from None
    try:
        _emit(
            args, command="restore", data={**asdict(result), "requires_confirmation": result.dry_run},
            message=(
                f"Archive validated: {result.archive_sha256}. Confirm with --archive-sha256 and --confirm."
                if result.dry_run else "Profile restored into the new private home."
            ),
        )
    except Exception:
        raise BackupOutcomeUnknownError(
            "Restore may already be complete. Retry the same request to verify it."
        ) from None
    return 0


def _command_export(args: argparse.Namespace) -> int:
    from grounded_apply.repositories.backup_files import _safe_path, read_private_file, write_private_file
    from grounded_apply.services.support import SupportService
    from grounded_apply.services.workflow import canonical, hash_bytes
    paths = resolve_runtime_paths()
    destination = Path(args.redacted)
    _safe_path(destination)
    if paths.portable_root is not None and destination.resolve().is_relative_to(paths.portable_root):
        raise CliInputError("Support exports must be outside the managed portable home")
    with _open_initialized_profile_repository(paths, read_only=True) as repository:
        report = SupportService(repository).report()
    content = (canonical(report) + "\n").encode("utf-8")
    existing = read_private_file(destination, limit=8192)
    if existing is not None and existing != content:
        raise CliInputError("Support export destination already contains different data")
    if existing is None and not args.dry_run:
        write_private_file(destination, content)
    _emit(args, command="export", data={"sha256": hash_bytes(content), "dry_run": args.dry_run,
        "replayed": existing is not None, "redacted": True},
        message="Support export preview validated." if args.dry_run else "Fixed-schema support report exported without personal values, identifiers, paths, or logs.")
    return 0


def _question_input(location: str | None) -> object:
    if location is None:
        return ()
    value = _load_proposal_object(_read_utf8_input(location, label="questions input", max_bytes=256 * 1024))
    if set(value) != {"questions"}:
        raise CliInputError("Question input requires exactly a questions array")
    return value["questions"]


def _layout_input(location: str | None) -> object:
    if location is None:
        return None
    return _load_proposal_object(_read_utf8_input(location, label="layout input", max_bytes=32 * 1024))


def _selected_ids(value: str) -> tuple[str, ...]:
    if len(value) > 22000:
        raise CliInputError("Too many selected claim IDs")
    ids = tuple(value.split(","))
    if not ids or len(ids) > 80 or len(set(ids)) != len(ids):
        raise CliInputError("Select distinct comma-separated claim IDs")
    for claim_id in ids:
        _validated_idempotency_key(claim_id)
    return ids


def _command_materials(args: argparse.Namespace) -> int:
    from grounded_apply.repositories.latex_renderer import LatexResumeRenderer
    from grounded_apply.repositories.material_files import export_material
    from grounded_apply.services.materials import MaterialBlocked, MaterialService
    action = args.materials_command
    paths = resolve_runtime_paths()
    mutating = (action == "build" and not args.dry_run) or (action == "approve" and args.confirm)
    try:
        with _open_initialized_profile_repository(paths, read_only=not mutating) as repository:
            service = MaterialService(repository, LatexResumeRenderer())
            if action == "list":
                data = {"materials": service.list(args.job_id)}
                message = "\n".join(f"{m['material_id']} [{m['status']}] job={m['job_id']}" for m in data["materials"]) or "No saved materials."
            elif action == "build":
                if args.layout_file == "-" and args.questions_file == "-":
                    raise CliInputError("Only one material input may read stdin")
                data = service.build(args.job_id, _selected_ids(args.claim_ids),
                    idempotency_key=args.idempotency_key, dry_run=args.dry_run,
                    questions=_question_input(args.questions_file), layout=_layout_input(args.layout_file))
                message = "Material plan ready for review." if args.dry_run else f"Draft material: {data['material_id']}\nBundle SHA-256: {data['bundle_sha256']}\nExport and review before materials approve."
            elif action == "approve":
                data = service.approve(args.material_id, bundle_sha256=args.bundle_sha256,
                    actor_id=args.actor_id, idempotency_key=args.idempotency_key, confirm=args.confirm)
                message = "Material approved for use." if args.confirm else "Approval preview validated; repeat with --confirm after reviewing the material."
            else:
                with repository.read_transaction():
                    material = service.get(args.material_id)
                    if action == "export":
                        data = export_material(material, Path(args.output_dir), portable_root=paths.portable_root, dry_run=args.dry_run)
                        message = "Export destination validated." if args.dry_run else "Private resume and answer copies exported. These copies are outside managed backup/deletion."
                    else:
                        data = {"material_id": material["id"], "bundle_sha256": material["bundle_sha256"],
                            "structure": material["structure"], "manifest": material["manifest"],
                            "validation": material["validation"], "ready": service.is_approved(args.material_id)}
                        message = f"Bundle SHA-256: {material['bundle_sha256']}\n" + _terminal_safe(material["extracted_text"])
    except MaterialBlocked as blocked:
        _emit(args, command="materials." + action, data={"kind": "need_info", "outcomes": blocked.outcomes, "ready": False},
            message="Material needs information or claim review. " + _terminal_safe(json.dumps(blocked.outcomes, ensure_ascii=False)))
        return 3
    try:
        _emit(args, command="materials." + action, data=data, message=message)
    except Exception:
        if mutating:
            raise PostCommitOutputError(_POST_COMMIT_DECISION_MESSAGE) from None
        raise ValueError("Material output failed") from None
    return 0


def _batch_message(data: dict[str, Any], *,
                   recovery_action: str = "Resume this batch to continue.",
                   published_locations: dict[str, str | None] | None = None) -> str:
    counts = data.get("counts", {})
    lines = [f"Batch {data.get('batch_id', 'preview')}: {data.get('status', 'preview')}",
             f"{counts.get('draft', 0)} drafts; {counts.get('blocked', 0)} blocked; "
             f"{data.get('remaining_count', 0)} remaining."]
    for item in data.get("items", ()):
        detail = f"[{item['status']}] job={item['job_id']}"
        if item.get("title"):
            detail += f" {item['title']}"
        if item.get("material_id"):
            detail += f" material={item['material_id']}"
            if item.get("material_approved"):
                detail += " (existing exact-bundle approval)"
        if item.get("questionnaire_coverage") == "unknown":
            detail += " — application questions not supplied"
        lines.append(detail)
        if item.get("source_url"):
            lines.append(f"  {item['source_url']}")
        if published_locations is not None and item["job_id"] in published_locations:
            location = published_locations[item["job_id"]]
            lines.append(f"  Published location: {location if location is not None else 'not provided; review required'}")
    for group in data.get("grouped_blockers", ()):
        detail = f"Needs review: {group.get('reason', group.get('kind', 'need_info'))}"
        if group.get("question_label"):
            detail += f" — {group['question_label']}"
        lines.append(detail)
    if data.get("stop_reason"):
        lines.append(f"Stopped: {data['stop_reason']}. {recovery_action}")
    lines.append("Unapproved drafts require review. This run recorded no approvals or application submissions.")
    return "\n".join(_terminal_safe(line) for line in lines)


def _search_message(data: dict[str, Any], *, scheduled: bool = False) -> str:
    lines = [f"Search run {data.get('run_id', 'preview')}: {data.get('status', 'configured')}"]
    for source in data.get("sources", ()):
        report = source.get("report") or {}
        status = report.get("status", source.get("stage", "pending"))
        detail = f"{source['source_id']}: {status}; {source.get('captured_count', 0)} captured"
        if report.get("remaining_count") is not None:
            detail += f"; {report['remaining_count']} indexed postings unread"
        lines.append(detail)
    filter_labels = {"title_excluded": "title excluded", "location_excluded": "location excluded",
        "location_not_matched": "location did not match", "location_unknown_excluded": "missing location excluded"}
    skipped = data.get("skipped", {})
    for reason, label in filter_labels.items():
        if skipped.get(reason, 0):
            lines.append(f"Saved preparation filter: {skipped[reason]} {label}.")
    if data.get("coverage_complete") is False:
        lines.append("Source coverage is incomplete; source results show the gaps.")
    if data.get("stop_reason"):
        lines.append(f"Stopped: {data['stop_reason']}. Saved progress remains available.")
    message = "\n".join(_terminal_safe(line) for line in lines)
    batch = data.get("batch")
    if isinstance(batch, dict):
        locations = {item["job_id"]: item["location"] for item in data.get("selection", ()) if "location" in item}
        message += "\n" + _batch_message(batch,
            recovery_action="Tick this schedule to continue." if scheduled else "Resume this saved search run to continue.",
            published_locations=locations or None)
    elif data.get("review_available", True):
        message += f"\n{len(data.get('selection', ()))} jobs selected; no material batch prepared."
    return message


def _schedule_message(data: dict[str, Any]) -> str:
    lines = [f"Schedule {data.get('schedule_id', 'preview')}: {data.get('status', 'validated')}"]
    next_due = data.get("next_due")
    if next_due is not None:
        lines.append(f"Next due: {next_due.get('due_at') if isinstance(next_due, dict) else next_due}")
    if data.get("notifications_pending"):
        lines.append(f"{data['notifications_pending']} notification(s) awaiting delivery acknowledgment.")
    notification = data.get("notification")
    if isinstance(notification, dict):
        lines.append(f"Pending notification: {notification.get('id')}")
        if notification.get("run_id") is not None:
            lines.append(f"Notification review run: {notification['run_id']}")
    if data.get("stop_reason"):
        lines.append(f"Stopped: {data['stop_reason']}.")
    message = "\n".join(_terminal_safe(line) for line in lines)
    if isinstance(data.get("child"), dict):
        message += "\n" + _search_message(data["child"], scheduled=True)
    return message


def _command_schedules(args: argparse.Namespace) -> int:
    from grounded_apply.repositories.discovery_http import PublicJobHTTPTransport
    from grounded_apply.repositories.latex_renderer import LatexResumeRenderer
    from grounded_apply.services.schedule_policy import validate_schedule_manifest
    from grounded_apply.services.schedules import ScheduleLeaseActiveError, ScheduleService

    action = args.schedules_command
    dry_run = bool(getattr(args, "dry_run", False))
    mutating = action in {"configure", "tick", "pause", "resume", "ack"} and not dry_run
    manifest = None
    if action == "configure":
        manifest = _load_proposal_object(_read_utf8_input(
            args.spec_file, label="daily schedule specification", max_bytes=1024 * 1024))
        validate_schedule_manifest(manifest)
    for name in ("idempotency_key", "schedule_id", "notification_id"):
        if getattr(args, name, None) is not None:
            _validated_idempotency_key(getattr(args, name))

    def repository_factory(read_only: bool):
        return _open_initialized_profile_repository(resolve_runtime_paths(), read_only=read_only)

    service = ScheduleService(repository_factory, PublicJobHTTPTransport(), LatexResumeRenderer())
    execution_error = None
    if action == "configure":
        data = service.configure(manifest, idempotency_key=args.idempotency_key, dry_run=dry_run)
        message = "Daily schedule specification validated." if dry_run else _schedule_message(data)
        message += "\nA separate wake-up mechanism must invoke schedules tick; this command installs none."
    elif action == "list":
        data = {"schedules": service.list(), "read_only": True, "external_action_taken": False}
        message = "\n".join(_terminal_safe(f"{item['schedule_id']} [{item['status']}]")
                            for item in data["schedules"]) or "No daily schedules."
    elif action == "show":
        data = service.get(args.schedule_id)
        message = _schedule_message(data)
    elif action in {"pause", "resume"}:
        data = service.set_enabled(args.schedule_id, action == "resume", idempotency_key=args.idempotency_key)
        message = _schedule_message(data)
    elif action == "ack":
        data = service.ack(args.schedule_id, args.notification_id, idempotency_key=args.idempotency_key)
        message = _schedule_message(data)
    else:
        try:
            data = service.tick(args.schedule_id, dry_run=dry_run)
        except ScheduleLeaseActiveError:
            data = {**service.get(args.schedule_id), "stop_reason": "lease_active", "executed": False}
        except Exception:
            try:
                data = service.get(args.schedule_id)
            except Exception:
                data = {"schedule_id": args.schedule_id, "review_available": False,
                        "external_action_taken": False, "application_ready": False}
            data = {**data, "status": "failed", "stop_reason": "shared_failure", "recovery": _SCHEDULE_RECOVERY}
            execution_error = {"type": "ScheduleExecutionError", "message": "Daily execution failed. " + _SCHEDULE_RECOVERY}
        message = _schedule_message(data)
        if execution_error is not None:
            message = execution_error["message"] + "\n" + message
    code = 2 if execution_error is not None else 0
    if action == "tick" and not dry_run:
        child = data.get("child") or {}
        if execution_error is not None or data.get("stop_reason") == "lease_active":
            code = 2
        elif data.get("executed"):
            if (data.get("status") in {"failed", "budget_exhausted", "retry_limit"}
                or child.get("remaining_count", 0) or child.get("stop_reason")
                or child.get("coverage_complete") is False):
                code = 2
            elif child.get("blockers_count", 0):
                code = 3
    try:
        _emit(args, command="schedules." + action, data=data, message=message, ok=code == 0,
              error=execution_error)
    except Exception:
        if mutating:
            raise ScheduleOutcomeUnknownError(_SCHEDULE_RECOVERY) from None
        raise ValueError("Daily schedule output failed") from None
    return code


def _command_searches(args: argparse.Namespace) -> int:
    from grounded_apply.repositories.discovery_http import PublicJobHTTPTransport
    from grounded_apply.repositories.latex_renderer import LatexResumeRenderer
    from grounded_apply.services.searches import SearchLeaseActiveError, SearchService, validate_search_manifest

    action = args.searches_command
    dry_run = bool(getattr(args, "dry_run", False))
    mutating = action in {"configure", "run", "resume"} and not dry_run
    manifest = None
    if action == "configure":
        manifest = _load_proposal_object(_read_utf8_input(
            args.spec_file, label="search specification", max_bytes=4 * 1024 * 1024))
        validate_search_manifest(manifest)
        _validated_idempotency_key(args.idempotency_key)
    if action == "run":
        _validated_idempotency_key(args.idempotency_key)
    if getattr(args, "search_id", None) is not None:
        _validated_idempotency_key(args.search_id)
    if getattr(args, "run_id", None) is not None:
        _validated_idempotency_key(args.run_id)
    if action in {"run", "resume"} and (not 1 <= args.max_items <= 50 or not 1 <= args.max_seconds <= 3600):
        raise CliInputError("Search invocation budgets require 1–50 items and 1–3600 seconds")

    def repository_factory(read_only: bool):
        return _open_initialized_profile_repository(resolve_runtime_paths(), read_only=read_only)

    service = SearchService(repository_factory, PublicJobHTTPTransport(), LatexResumeRenderer())
    execution_error = None
    if action == "configure":
        data = service.configure(manifest, idempotency_key=args.idempotency_key, dry_run=dry_run)
        message = "Search scope validated." if dry_run else f"Search scope saved: {data['search_id']}"
    elif action == "scopes":
        data = {"searches": service.list_searches(), "read_only": True, "external_action_taken": False}
        message = "\n".join(_terminal_safe(item["search_id"]) for item in data["searches"]) or "No saved search scopes."
    elif action == "list":
        data = {"runs": service.list(search_id=args.search_id), "read_only": True, "external_action_taken": False}
        message = "\n".join(_terminal_safe(f"{item['run_id']} [{item['status']}]") for item in data["runs"]) or "No saved search runs."
    elif action == "show":
        data = service.get(args.run_id)
        message = _search_message(data)
    elif action == "export":
        from grounded_apply.repositories.review_files import export_search_review

        snapshot = service.export_snapshot(args.run_id)
        data = export_search_review(snapshot, Path(args.output_dir),
                                    paths=resolve_runtime_paths(), dry_run=dry_run)
        message = ("Review folder validated." if dry_run else "Review folder exported: " +
                   _terminal_safe(str(Path(args.output_dir) / "review.md")))
        message += ("\nDrafts and blockers still require review. These private copies are outside "
                    "managed runtime backup and deletion.")
    else:
        try:
            if action == "run":
                data = service.run(args.search_id, idempotency_key=args.idempotency_key,
                                   max_items=args.max_items, max_seconds=args.max_seconds)
            else:
                data = service.resume(args.run_id, max_items=args.max_items, max_seconds=args.max_seconds)
        except SearchLeaseActiveError as error:
            data = {**service.get(error.run_id), "stop_reason": "lease_active"}
        except Exception as error:
            run_id = getattr(args, "run_id", None) or getattr(error, "run_id", None)
            try:
                data = service.get(run_id) if run_id is not None else {}
            except Exception:
                data = {}
            if not data:
                data = {"run_id": run_id, "review_available": False,
                        "external_action_taken": False, "application_ready": False}
            data = {**data, "status": "failed", "stop_reason": "shared_failure", "recovery": _SEARCH_RECOVERY}
            execution_error = {"type": "SearchExecutionError", "message": "Search execution failed. " + _SEARCH_RECOVERY}
        message = _search_message(data)
        if execution_error is not None:
            message = execution_error["message"] + "\n" + message
    code = 0
    if action in {"run", "resume"}:
        batch = data.get("batch") or {}
        if (data.get("remaining_count", 0) or data.get("stop_reason") or data.get("status") == "failed"
            or data.get("coverage_complete") is False or batch.get("remaining_count", 0)):
            code = 2
        elif data.get("blockers_count", batch.get("blockers_count", 0)):
            code = 3
    try:
        _emit(args, command="searches." + action, data=data, message=message, ok=code == 0,
              error=execution_error)
    except Exception:
        if mutating:
            raise SearchOutcomeUnknownError(_SEARCH_RECOVERY) from None
        if action == "export" and not dry_run:
            raise ValueError("Review export output failed. Retry the same run and destination to verify "
                             "a completed copy. If that destination is incomplete or changed, choose "
                             "a new destination; existing files are not overwritten.") from None
        raise ValueError("Search output failed") from None
    return code


def _command_batches(args: argparse.Namespace) -> int:
    from grounded_apply.repositories.latex_renderer import LatexResumeRenderer
    from grounded_apply.services.batches import BatchLeaseActiveError, BatchService, validate_batch_manifest
    from grounded_apply.services.materials import MaterialDependencyError, MaterialService

    action = args.batches_command
    dry_run = bool(getattr(args, "dry_run", False))
    mutating = action in {"prepare", "resume"} and not dry_run
    manifest = None
    execution_error = None
    if action == "prepare":
        manifest = _load_proposal_object(_read_utf8_input(
            args.spec_file, label="batch specification", max_bytes=4 * 1024 * 1024))
        validate_batch_manifest(manifest)
        _validated_idempotency_key(args.idempotency_key)
    if action in {"prepare", "resume"}:
        if not 1 <= args.max_items <= 50 or not 1 <= args.max_seconds <= 3600:
            raise CliInputError("Batch budgets require 1–50 items and 1–3600 seconds")
    if action in {"resume", "show"}:
        _validated_idempotency_key(args.batch_id)
    with _open_initialized_profile_repository(resolve_runtime_paths(), read_only=not mutating) as repository:
        service = BatchService(repository, MaterialService(repository, LatexResumeRenderer()))
        if action == "list":
            data = {"batches": service.list(), "read_only": True, "external_action_taken": False}
            message = "\n".join(
                f"{item['batch_id']} [{item['status']}]" for item in data["batches"]
            ) or "No saved batches."
        elif action == "show":
            data = service.get(args.batch_id)
            message = _batch_message(data)
        else:
            if action == "prepare":
                data = service.create(manifest, idempotency_key=args.idempotency_key, dry_run=dry_run)
                batch_id = data.get("batch_id")
            else:
                batch_id = args.batch_id
            if not dry_run:
                try:
                    data = service.run(batch_id, max_items=args.max_items, max_seconds=args.max_seconds)
                except BatchLeaseActiveError:
                    data = {**service.get(batch_id), "stop_reason": "lease_active"}
                except Exception as error:
                    # Shared failures stop execution. Only return saved items if
                    # the normal integrity-validating read can still prove them.
                    try:
                        data = service.get(batch_id)
                    except Exception:
                        data = {"batch_id": batch_id, "review_available": False,
                                "external_action_taken": False, "application_ready": False}
                    data = {**data, "status": "failed", "stop_reason": "shared_failure",
                            "recovery": _BATCH_RECOVERY}
                    failure = ("The PDF environment is unavailable; run doctor --materials and check the PDF prerequisites. "
                               if isinstance(error, MaterialDependencyError)
                               else "Shared batch preparation failed; saved progress must be checked before reuse. ")
                    execution_error = {"type": "BatchExecutionError", "message": failure + _BATCH_RECOVERY}
            message = _batch_message(data)
            if execution_error is not None:
                message = execution_error["message"] + "\n" + (
                    message if data.get("review_available", True)
                    else f"Batch {batch_id}: validated review is unavailable.")
    code = 0
    if action in {"prepare", "resume"}:
        if dry_run:
            code = 3 if data.get("blockers_count", data.get("counts", {}).get("blocked", 0)) else 0
        elif data.get("remaining_count", 0) or data.get("stop_reason") or data.get("status") == "failed":
            code = 2
        elif data.get("blockers_count", data.get("counts", {}).get("blocked", 0)):
            code = 3
    try:
        _emit(args, command="batches." + action, data=data, message=message,
              ok=code == 0, error=execution_error)
    except Exception:
        if mutating:
            raise BatchOutcomeUnknownError(_BATCH_RECOVERY) from None
        raise ValueError("Batch output failed") from None
    return code


def _command_answers(args: argparse.Namespace) -> int:
    from grounded_apply.services.questionnaires import QuestionnaireService
    with _open_initialized_profile_repository(resolve_runtime_paths(), read_only=True) as repository:
        answers = QuestionnaireService(repository).prepare(args.job_id, _question_input(args.questions_file))
    _emit(args, command="answers", data={"answers": answers, "stored": False, "external_action_taken": False},
        message="\n\n".join(_terminal_safe(a["question"]) + "\n" + (
            _terminal_safe(a["answer"]) if a["answer"] is not None else "NeedInfo: answer this question yourself or select supporting career evidence."
        ) for a in answers))
    return 0


def _command_brief(args: argparse.Namespace) -> int:
    from grounded_apply.repositories.latex_renderer import LatexResumeRenderer
    from grounded_apply.services.briefing import BriefingService
    from grounded_apply.services.materials import MaterialService

    with _open_initialized_profile_repository(resolve_runtime_paths(), read_only=True) as repository:
        data = BriefingService(repository, MaterialService(repository, LatexResumeRenderer())).brief(
            job_id=args.job_id, follow_up_days=args.follow_up_days)
    profile = data["profile"]
    lines = [f"{data['job_count']} saved jobs; {data['application_count']} applications; "
             f"{profile['pending_review_count']} facts awaiting review.",
             "Action order follows workflow stage, not fit. Response checks send no messages."]
    if not data["items"]:
        lines.append("Review your career facts and supply the text and URL of a job to begin.")
    for item in data["items"]:
        lines.append(f"[{item['state']}] {item['source_url']}\n"
                     f"  {item['next_action']['kind']}: {item['next_action']['reason']}\n"
                     f"  {item['requirements_without_retrieved_evidence']} requirements without retrieved evidence; "
                     f"job={item['job_id']}")
    _emit(args, command="brief", data=data, message=_terminal_safe("\n".join(lines)))
    return 0


def _command_applications(args: argparse.Namespace) -> int:
    from grounded_apply.repositories.latex_renderer import LatexResumeRenderer
    from grounded_apply.services.applications import ApplicationService
    from grounded_apply.services.materials import MaterialBlocked, MaterialService
    action = args.applications_command
    mutating = (action == "add" and not args.dry_run) or (action == "transition" and args.confirm)
    try:
        with _open_initialized_profile_repository(resolve_runtime_paths(), read_only=not mutating) as repository:
            service = ApplicationService(repository, MaterialService(repository, LatexResumeRenderer()))
            if action == "add":
                data = service.add(args.job_id, actor_id=args.actor_id, idempotency_key=args.idempotency_key, dry_run=args.dry_run)
            elif action == "transition":
                data = service.transition(args.application_id, args.to, actor_id=args.actor_id,
                    idempotency_key=args.idempotency_key, material_id=args.material_id,
                    confirm_submitted=args.confirm_submitted, confirm=args.confirm, preview_token=args.preview_token)
            else:
                with repository.read_transaction():
                    data = {"applications": service.list()} if action == "list" else service.get(args.application_id)
    except MaterialBlocked as blocked:
        _emit(args, command="applications." + action, data={"kind": "need_info", "outcomes": blocked.outcomes, "ready": False},
            message="Application materials need claim review before this transition.")
        return 3
    try:
        _emit(args, command="applications." + action, data=data,
            message=_terminal_safe(json.dumps(data, ensure_ascii=False, indent=2)))
    except Exception:
        if mutating:
            raise PostCommitOutputError(_POST_COMMIT_DECISION_MESSAGE) from None
        raise ValueError("Application output failed") from None
    return 0


def _command_jobs_plan_search(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services.discovery_plan import build_public_search_plan

    plan = build_public_search_plan(roles=tuple(args.role), locations=tuple(args.location))
    lines = [
        f"Public search plan: {plan.query_count} queries; review up to {plan.max_distinct_links} distinct result links.",
        "Result domains: " + ", ".join(plan.searches[0].domains),
        *(f"{row.position}. Terms: {_terminal_safe(json.dumps(row.terms, ensure_ascii=False))}" for row in plan.searches),
        "No sites checked, profile read or state saved. Terms are data, not search-engine syntax.",
        "Use available public browsing to inspect actual links, then jobs sources and jobs discover --dry-run.",
        "Search results can be incomplete or stale; this plan establishes no job-market coverage.",
    ]
    try:
        _emit(args, command="jobs.plan-search", data=to_jsonable(plan), message="\n".join(lines))
    except Exception:
        raise ValueError("Search plan output failed; no state was changed") from None
    return 0


def _command_jobs_sources(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from grounded_apply.services.discovery_sources import (
        build_source_manifest, build_source_manifest_keep_valid,
    )

    urls = args.url or []
    if args.urls_file is not None:
        source = _read_utf8_input(args.urls_file, label="source links", max_bytes=1024 * 1024)
        urls = [line.strip() for line in source.splitlines() if line.strip()]
    partial = build_source_manifest_keep_valid(urls) if args.keep_valid else None
    report = partial.accepted if partial is not None else build_source_manifest(urls)
    retained_sources = report.sources if report is not None else ()
    data: dict[str, object] = {"manifest": report.manifest() if report is not None else None,
            "inputs": [asdict(item) for item in report.inputs] if report is not None else [],
            "automatic_source_count": sum(source.provider != "manual" for source in retained_sources),
            "manual_source_count": sum(source.provider == "manual" for source in retained_sources),
            "network_requests": 0, "storage_changed": False, "live_boards_verified": False,
            "scope": "entire_boards"}
    lines = [f"{source.id}: {'manual gap' if source.provider == 'manual' else 'automatic board'}"
             for source in retained_sources]
    incomplete = partial is not None and bool(partial.rejected_inputs)
    if partial is not None:
        data.update({"setup_status": partial.setup_status, "input_count": partial.input_count,
                     "accepted_input_count": partial.accepted_input_count,
                     "rejected_inputs": [asdict(item) for item in partial.rejected_inputs]})
        lines.insert(0, f"Source setup {partial.setup_status}: {partial.accepted_input_count} of {partial.input_count} links accepted.")
        lines.extend(f"Input {item.position}: {item.error}" for item in partial.rejected_inputs)
        if report is None:
            lines.append("No usable source manifest; correct or replace the rejected links before discovery.")
    try:
        _emit(args, command="jobs.sources", data=data, ok=not incomplete,
              error={"type": "IncompleteSourceSetup", "message": "Some source links were rejected; review their positions and reasons."} if incomplete else None,
              message="\n".join(lines) + "\nSource setup only; no sites checked or profile saved. "
              "Recognized links select entire boards; original URL filters do not carry over.")
    except Exception:
        raise ValueError("Source setup output failed; no state was changed") from None
    return 2 if incomplete else 0


def _command_jobs_discover(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from grounded_apply.repositories.discovery_http import PublicJobHTTPTransport
    from grounded_apply.services.discovery import (
        DiscoveryService, builtin_sources, validate_location_discovery_request, validate_source_manifest,
    )
    from grounded_apply.services.jobs import DiscoveryCapacityError, DiscoveryRecordError, JobService

    location_filters = None
    if args.location_contains is not None or args.missing_location is not None:
        try:
            location_filters = validate_location_discovery_request(
                title_contains=tuple(args.title_contains), location_contains=tuple(args.location_contains or ()),
                missing_location=args.missing_location or "include", limit_per_source=args.limit_per_source)
        except Exception:
            raise ValueError("Location-filtered discovery requires bounded title/location terms, an include or exclude missing-location policy, and a selected-job limit from one to one thousand") from None
    if args.preset:
        sources = builtin_sources(args.preset)
    else:
        sources = validate_source_manifest(_load_proposal_object(_read_utf8_input(
            args.sources_file, label="discovery sources", max_bytes=64 * 1024)))
    # Validate local storage before contacting sources. Close it before network
    # work; repeat the full runtime/open checks before any subsequent capture.
    paths = None
    if not args.dry_run:
        paths = resolve_runtime_paths()
        with _open_initialized_profile_repository(paths, read_only=True):
            pass
    service = DiscoveryService(PublicJobHTTPTransport())
    if location_filters is None:
        report = service.discover(
            sources, title_contains=tuple(args.title_contains), limit_per_source=args.limit_per_source)
        data = asdict(report)
    else:
        try:
            report = service.discover_with_locations(
                sources, title_contains=tuple(args.title_contains), limit_per_source=args.limit_per_source,
                location_contains=location_filters.location_contains,
                missing_location=location_filters.missing_location)
            data = asdict(report)
        except Exception:
            raise ValueError("Location-filtered discovery failed; no snapshots were saved") from None
    data.update({"schema_version": 1 if location_filters is None else 2, "dry_run": args.dry_run,
                 "storage_checked": paths is not None, "external_submission_taken": False,
                 "content_trust": "untrusted", "captures": [], "capture_blockers": []})
    storage_error = None
    if paths is not None:
        try:
            with _open_initialized_profile_repository(paths, read_only=False) as repository:
                service = JobService(repository)
                for job in report.jobs:
                    try:
                        saved = service.capture_discovered(job)
                    except DiscoveryCapacityError:
                        storage_error = "capacity_reached"
                        break
                    except DiscoveryRecordError:
                        data["capture_blockers"].append({"provider": job.provider,
                            "board": job.board, "external_id": job.external_id,
                            "reason": "snapshot_rejected"})
                        continue
                    data["captures"].append({**saved, "provider": job.provider,
                        "board": job.board, "external_id": job.external_id,
                        "title": job.title, "location": job.location,
                        "source_url": job.source_url, "content_sha256": job.content_sha256})
        except Exception:
            # The fixed response preserves already reported captures and tells
            # callers to replay stable identities if a later commit is uncertain.
            storage_error = "capture_failed_retry_discovery"
        # Captured snapshots are available through jobs show; avoid repeating an
        # entire board of job descriptions in the routine private response.
        data["jobs"] = [{"provider": job.provider, "board": job.board,
                         "external_id": job.external_id, "title": job.title,
                         "location": job.location, "source_url": job.source_url,
                         "content_sha256": job.content_sha256} for job in report.jobs]
    data["storage_error"] = storage_error
    data["uncaptured_count"] = 0 if paths is None else len(report.jobs) - len(data["captures"])
    incomplete = any(source.status != "successful" for source in report.sources)
    warnings = ["Coverage is limited to configured feeds. Unchecked employers and failed sources may contain other openings."]
    if incomplete:
        warnings.append("Coverage is incomplete; inspect each source's status before concluding there are no openings.")
    if storage_error or data["capture_blockers"]:
        warnings.append("Some snapshots were not captured. Completed captures remain; repeating discovery reuses unchanged versions.")
    lines = ["Discovery preview (network reads; no local writes)." if args.dry_run else "Discovery capture results."]
    if location_filters is not None:
        lines.append("Published location text: " + _terminal_safe(json.dumps(location_filters.location_contains, ensure_ascii=False))
                     + f"; missing locations: {location_filters.missing_location}.")
        lines.append("Title and location filters apply before the selected-job limit. Text matches do not establish geographic eligibility.")
    for source in report.sources:
        lines.append(f"{_terminal_safe(source.source_id)}: {source.status}; {source.count} selected")
        if source.error is not None:
            lines.append(f"  Reason: {source.error}")
        if source.careers_url:
            lines.append(f"  Careers: {_terminal_safe(source.careers_url)}")
    if location_filters is not None:
        for selection in report.selections:
            lines.append(f"{_terminal_safe(selection.source_id)} selection: {selection.valid_count} valid; "
                f"{selection.title_filtered_count} title exclusions; {selection.location_filtered_count} location exclusions; "
                f"{selection.unknown_excluded_count} unknown locations excluded; {selection.limit_deferred_count} deferred by limit; "
                f"{selection.selected_unknown_count} selected with unknown location.")
    for job in report.jobs:
        lines.append(f"{_terminal_safe(job.title)} — {_terminal_safe(job.source_url)}")
        if location_filters is not None:
            lines.append("  Published location: " + (_terminal_safe(job.location) if job.location is not None else "unknown (included by policy)"))
    if paths is not None:
        lines.append(f"{len(data['captures'])} captured/reused; {data['uncaptured_count']} not captured.")
    # A nonzero exit preserves the report; partial success is never a clean sync.
    code = 2 if incomplete or storage_error or data["capture_blockers"] else 0
    try:
        _emit(args, command="jobs.discover", data=data, message="\n".join(lines),
              ok=code == 0, warnings=warnings,
              error={"type": "IncompleteDiscovery", "message": "Inspect source coverage and capture results."} if code else None)
    except Exception:
        if not args.dry_run:
            raise RuntimeError("Discovery output failed; snapshots may already be saved. Repeat discovery to recover unchanged captures.") from None
        raise RuntimeError("Discovery preview output failed; no snapshots were saved.") from None
    return code


def _command_jobs(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services.jobs import JobService, extract_requirements, validate_job_input
    from grounded_apply.services.matching import MatchingService
    if args.jobs_command == "add":
        source = _read_utf8_input(args.source_file, label="job text", max_bytes=1024 * 1024)
        validate_job_input(args.url, source)
        _validated_idempotency_key(args.idempotency_key)
        if args.dry_run:
            requirements, suspicious = extract_requirements("preview", source)
            _emit(args, command="jobs.add", data={"dry_run": True, "storage_checked": False,
                "requirement_count": len(requirements), "suspicious_lines": suspicious}, message="Job capture preview validated.")
            return 0
    with _open_initialized_profile_repository(resolve_runtime_paths(), read_only=args.jobs_command != "add") as repository:
        service = JobService(repository)
        if args.jobs_command == "add":
            result = service.add(args.url, source, idempotency_key=args.idempotency_key)
            message = f"Job saved: {result['job_id']}. Run jobs show or jobs assess."
        else:
            with repository.read_transaction():
                if args.jobs_command == "list":
                    result = {"jobs": [{"job_id": j.id, "source_url": j.source_url, "captured_at": j.captured_at,
                        "capture_method": j.capture_method} for j in service.list()]}
                    message = "\n".join(f"{j['job_id']} {_terminal_safe(j['source_url'])}" for j in result["jobs"]) or "No saved jobs."
                elif args.jobs_command == "show":
                    job = service.get(args.job_id)
                    result = to_jsonable(job)
                    message = f"{_terminal_safe(job.source_url)}\n" + "\n".join(
                        f"{r.id} [{r.category}] {_terminal_safe(r.quote)}" for r in job.requirements)
                else:
                    result = MatchingService(repository).assess(args.job_id)
                    message = result["limitation"] + "\n" + "\n".join(
                        f"{_terminal_safe(r['requirement']['quote'])}\n" + (
                            "\n".join(f"  {e['claim_id']}: {_terminal_safe(e['quote'])}" for e in r["candidate_evidence"])
                            or "  NeedInfo: " + r["question"])
                        for r in result["rows"])
    _emit(args, command="jobs." + args.jobs_command, data=result, message=message)
    return 0


def _command_profile_interview(args: argparse.Namespace) -> int:
    from grounded_apply.services.profile_interview import build_interview
    data = build_interview(topic=args.topic, depth=args.depth, after=args.after, limit=args.limit)
    questions = data["questions"]
    lines = [f"{question['prompt']}\n  {question['why']}" for question in questions]
    lines.extend(data["guidance"])
    if data["next_cursor"] is not None:
        lines.append(f"Continue this round with --after {data['next_cursor']}.")
    _emit(args, command="profile.interview", data=data, message="\n\n".join(lines))
    return 0


def _read_resume_input(args: argparse.Namespace) -> ResumeDocument:
    from grounded_apply.repositories.resume_documents import (
        MAX_DOCUMENT_BYTES, read_resume_document, resume_document_from_bytes,
    )
    if args.source_file != "-":
        return read_resume_document(args.source_file, format=args.source_format)
    binary = getattr(sys.stdin, "buffer", None)
    if binary is not None:
        raw = binary.read(MAX_DOCUMENT_BYTES + 1)
    else:
        raw = sys.stdin.read(MAX_DOCUMENT_BYTES + 1).encode("utf-8")
    return resume_document_from_bytes(raw, format=args.source_format)


def _resume_document_metadata(document: ResumeDocument) -> dict[str, Any]:
    from grounded_apply.domain import to_jsonable
    return {"format": document.format, "document_sha256": document.raw_source_sha256,
            "extracted_text_sha256": document.text_sha256, "extractor": document.extractor,
            "warnings": list(document.warnings), "issues": to_jsonable(document.issues),
            "incomplete": document.incomplete, "page_count": document.page_count,
            "span_basis": "extracted_text", "original_document_provenance_stored": False}


def _validate_resume_document_selection(args: argparse.Namespace, document: ResumeDocument) -> None:
    if document.format != "text" and args.document_sha256 is None:
        raise CliInputError("Pass the --document-sha256 shown by profile extract for PDF or LaTeX intake")
    if args.document_sha256 is not None and args.document_sha256 != document.raw_source_sha256:
        raise CliInputError("Resume document changed since extraction; extract and review again")
    if document.incomplete and not args.allow_partial:
        raise CliInputError("Document extraction is incomplete; review its issues or use --allow-partial for explicitly chosen partial retention")


def _command_profile_extract(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services.resume_extraction import extract_resume
    document = _read_resume_input(args)
    result = extract_resume(document.text, version=args.extractor_version)
    data = to_jsonable(result)
    data["document"] = _resume_document_metadata(document)
    attention = [line for line in result.inventory if line.status in {"unclassified", "blocked"}]
    _emit(args, command="profile.extract", data=data,
          message=f"Source SHA-256: {result.source_sha256}\nDocument SHA-256: {document.raw_source_sha256}\nExtractor version: {result.extractor_version}\n" + "\n".join(
              f"{i}: {p.claim_type}: {_terminal_safe(p.canonical_text)}" for i, p in enumerate(result.proposals)
          ) + "\n" + "\n".join(
              f"Line {line.line_number}: {line.status}: {_terminal_safe(line.text or line.reason)}"
              for line in attention
          ) + f"\n{result.skipped_lines} lines need attention. Every nonblank source line is in the inventory. "
          "Use profile onboard --select all to retain every proposed fact, or select indexes. "
          "Pass the displayed --extractor-version and --document-sha256; every fact still needs approval.",
          warnings=(*document.warnings, *(f"Source lines {issue.line}–{issue.end_line}: {issue.message}" for issue in document.issues)))
    return 0


def _command_profile_onboard(args: argparse.Namespace) -> int:
    from grounded_apply.services import ProfileService
    from grounded_apply.services.resume_extraction import extract_resume
    paths = resolve_runtime_paths()
    require_runtime_outside_repository(paths)
    document = _read_resume_input(args)
    source = document.text
    _validate_resume_document_selection(args, document)
    if args.extractor_version is None and args.select != "all":
        raise CliInputError("Pass the --extractor-version shown by profile extract; use 1 for an existing legacy selection")
    extraction = extract_resume(source, version=args.extractor_version or 2)
    if args.source_sha256 != extraction.source_sha256:
        raise CliInputError("Resume changed since extraction; extract and select again")
    retain_all = args.select == "all" or args.retain_all_facts
    if args.select == "all":
        indexes = tuple(range(len(extraction.proposals)))
    else:
        if not re.fullmatch(r"\d+(?:,\d+)*", args.select) or len(args.select) > 6000:
            raise CliInputError("Select 'all' or comma-separated displayed proposal indexes")
        indexes = tuple(int(i) for i in args.select.split(","))
    request = extraction.selected_request(indexes, source,
        _validated_idempotency_key(args.idempotency_key), retain_all_facts=retain_all)
    ProfileService.preview_import_proposal(request)
    if args.dry_run:
        data = {"proposal_count": len(request.proposals), "dry_run": True, "storage_checked": False, "review_required": True}
    else:
        with _open_initialized_profile_repository(paths, read_only=False) as repository:
            result = ProfileService(repository).create_import_proposal(request)
        data = {"claim_ids": [c.id for c in result.claims], "workflow_run_id": result.workflow_run_id,
                "dry_run": False, "review_required": result.review_required}
    data.update({"proposed_count": len(extraction.proposals), "selected_count": len(indexes),
                 "unselected_proposal_count": len(extraction.proposals) - len(indexes),
                 "lines_needing_attention": extraction.skipped_lines,
                 "content_policy_version": request.content_policy_version,
                 "document": _resume_document_metadata(document)})
    _emit(args, command="profile.onboard", data=data,
          message="Selected proposals validated." if args.dry_run else "Selected facts imported for review. Run gapply profile review.")
    return 0


def _command_profile_group_publication(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.repositories.resume_documents import ResumeDocumentError
    from grounded_apply.services.publication_grouping import (
        PublicationGroupingError, build_publication_group, build_publication_groups,
        validate_publication_group_request, validate_publication_groups_request,
    )

    if len(args.indexes) > 500 or (len(args.indexes) > 1 and sum(map(len, args.indexes)) > 6000):
        raise CliInputError("Pass at most 500 publication groups within 6000 total index characters")
    if any(len(value) > 6000 or re.fullmatch(r"[0-9]{1,4}(?:,[0-9]{1,4})+", value) is None
           for value in args.indexes):
        raise CliInputError("Pass at least two consecutive displayed publication indexes, separated by commas")
    groups = tuple(tuple(int(value) for value in group.split(",")) for group in args.indexes)
    if len(groups) == 1:
        validate_publication_group_request(expected_source_sha256=args.source_sha256,
                                           extractor_version=args.extractor_version, indexes=groups[0])
    else:
        validate_publication_groups_request(expected_source_sha256=args.source_sha256,
                                            extractor_version=args.extractor_version, groups=groups)
    if re.fullmatch(r"[0-9a-f]{64}", args.document_sha256) is None:
        raise CliInputError("Pass the original document SHA-256 shown by profile extract")
    try:
        document = _read_resume_input(args)
        _validate_resume_document_selection(args, document)
        if len(groups) == 1:
            result = build_publication_group(document.text,
                expected_source_sha256=args.source_sha256,
                extractor_version=args.extractor_version, indexes=groups[0])
        else:
            result = build_publication_groups(document.text,
                expected_source_sha256=args.source_sha256,
                extractor_version=args.extractor_version, groups=groups)
    except (PublicationGroupingError, ResumeDocumentError, CliInputError):
        raise
    except Exception:
        raise CliInputError("Publication grouping failed; no state was changed") from None
    try:
        data = to_jsonable(result)
        data["document"] = _resume_document_metadata(document)
        if len(groups) == 1:
            lines = [f"Grouped {len(result.grouped_indexes)} publication fragments into manifest proposal {result.grouped_manifest_index}."]
        else:
            lines = [f"Grouped {len(result.groups)} explicitly chosen publications; each group remains a separate proposal."]
            lines.extend(f"Original indexes {','.join(map(str, group.indexes))} → manifest proposal {group.manifest_index}."
                         for group in result.groups)
        lines.extend([
            f"{result.original_proposal_count} supported source proposals become {result.manifest_proposal_count} manifest proposals; all other proposals remain.",
            f"Extractor version: {result.extractor_version}; source SHA-256: {result.source_sha256}",
            f"Document SHA-256: {document.raw_source_sha256}",
        ])
        lines.extend(f"{i}: {proposal['claim_type']}: {_terminal_safe(proposal['canonical_text'])}"
                     for i, proposal in enumerate(data["manifest"]["proposals"]))
        lines.append("Original inventory indexes are preserved; original → manifest mapping: " + ", ".join(
            f"{row['original_index']} → {row['manifest_index']}" for row in data["index_mapping"]))
        lines.extend(f"Line {line.line_number}: {line.status}: {_terminal_safe(line.text or line.reason)}"
                     for line in result.inventory if line.status != "proposed")
        lines.extend([
            f"{result.unclassified_count} unclassified and {result.blocked_count} blocked lines still need attention.",
            "No profile was read, facts retained or decisions recorded. Grouping does not verify that the fragments describe one work.",
            "Review the complete manifest and source gaps. To retain chosen facts, pass only data.manifest to profile import with --retain-all-facts; every fact still needs explicit approval.",
        ])
        _emit(args, command="profile.group-publication", data=data, message="\n".join(lines),
              warnings=(_UNTRUSTED_REVIEW_WARNING, _REVIEW_WARNING, *document.warnings,
                        *(f"Source lines {issue.line}–{issue.end_line}: {issue.message}" for issue in document.issues)))
    except Exception:
        raise CliInputError("Publication grouping output failed; no state was changed") from None
    return 0


def _command_profile_inventory(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services.profile_inventory import (
        ProfileInventoryService, validate_profile_inventory_request,
    )

    anchor = args.after
    if args.after_json is not None:
        try:
            anchor = json.loads(args.after_json)
        except (ValueError, UnicodeError, RecursionError):
            raise CliInputError("Inventory continuation JSON must contain one nonblank string") from None
        if type(anchor) is not str:
            raise CliInputError("Inventory continuation JSON must contain one nonblank string")
    validate_profile_inventory_request(topic=args.topic, limit=args.limit, after_claim_id=anchor)
    with _open_initialized_profile_repository(resolve_runtime_paths(), read_only=True) as repository:
        report = ProfileInventoryService(repository).read(
            topic=args.topic, limit=args.limit, after_claim_id=anchor,
        )
    lines = [f"Retained profile inventory: {report.total_claim_count} claim(s) (read-only)."]
    for topic in report.topics:
        states = ", ".join(f"{state.status}/{state.approval_status}: {state.count}" for state in topic.states)
        lines.append(f"{topic.topic}: {topic.claim_count}" + (f" ({states})" if states else ""))
    if report.page is not None:
        page = report.page
        lines.append(f"Showing {page.returned_count} of {page.total_count} retained {report.selected_topic} claim(s); "
                     f"{page.before_count} before, {page.after_count} after.")
        for item in report.items:
            lines.append(f"{_terminal_safe(item.id)} [{item.status}/{item.approval_status}] "
                         f"{_terminal_safe(item.canonical_text)}")
            lines.append(f"  Type: {_terminal_safe(item.claim_type)}; origin: {item.source_type}; "
                         f"sensitivity: {item.sensitivity}; scope: {_terminal_safe(dumps(to_jsonable(item.scope)))}")
        if page.next_after is not None:
            if _terminal_safe(page.next_after) == page.next_after:
                continuation = "--after=" + shlex.quote(page.next_after)
            else:
                continuation = "--after-json=" + shlex.quote(json.dumps(page.next_after, ensure_ascii=True))
            lines.append(f"Continue with gapply profile inventory --topic {report.selected_topic} --limit {page.limit} {continuation}")
        else:
            lines.append("End of this topic pass. Restart without --after or --after-json to see earlier or newer records.")
    lines.append("Counts describe retained records and recorded decisions; usability, profile completeness and interview progress are not assessed.")
    lines.append("Viewing this inventory records no decision. Use profile review for evidence and explicit fact approval.")
    try:
        _emit(args, command="profile.inventory", data=to_jsonable(report), message="\n".join(lines))
    except Exception:
        raise ValueError("Profile inventory output failed; no state was changed") from None
    return 0


def _command_profile_show(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services import ProfileService
    with _open_initialized_profile_repository(resolve_runtime_paths(), read_only=True) as repository:
        with repository.read_transaction():
            claims, evidence = ProfileService(repository).validated_profile()
    _emit(
        args, command="profile.show",
        data={"claims": to_jsonable(claims), "evidence": to_jsonable(evidence), "read_only": True, "content_trust": "untrusted"},
        message="\n".join(f"{c.id} [{c.status.value}/{c.approval_status.value}] {_terminal_safe(c.canonical_text)}" for c in claims) or "No profile claims yet.",
    )
    return 0


def _command_profile_retire(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from grounded_apply.services.profile_lifecycle import ProfileLifecycleService
    try:
        with _open_initialized_profile_repository(resolve_runtime_paths(), read_only=not args.confirm) as repository:
            result = ProfileLifecycleService(repository).retire(
                args.claim_id, replacement_claim_id=args.replacement_claim_id,
                actor_id=args.actor_id, idempotency_key=args.idempotency_key,
                confirm=args.confirm, preview_token=args.preview_token,
            )
    except ValueError:
        raise
    except Exception:
        if args.confirm:
            raise PostCommitOutputError(_POST_COMMIT_DECISION_MESSAGE) from None
        raise ValueError("Retirement preview failed; no decision was recorded") from None
    try:
        _emit(args, command="profile.retire", data={**asdict(result), "requires_confirmation": result.dry_run},
              message=f"Retirement preview: {result.preview_token}. Confirm with --preview-token and --confirm."
              if result.dry_run else "Claim retired from future use; original evidence and approval history preserved.")
    except Exception:
        if args.confirm:
            raise PostCommitOutputError(_POST_COMMIT_DECISION_MESSAGE) from None
        raise ValueError("Retirement preview output failed; no decision was recorded") from None
    return 0


def _command_delete(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from grounded_apply.repositories.deletion_files import LocalDeletionStorage
    from grounded_apply.services.deletion import RECOVERY, DeletionOutcomeUnknownError, DeletionService

    result = DeletionService(LocalDeletionStorage()).delete(
        Path(args.target_home), Path(args.receipt),
        confirm=args.confirm, preview_token=args.preview_token,
    )
    try:
        _emit(
            args, command="delete", data={**asdict(result), "requires_confirmation": result.dry_run},
            message=(
                f"Preview: {result.files} files and {result.directories} directories. "
                f"Token: {result.preview_token}. Confirm with --preview-token and --confirm. "
                "External backups, source files, exported copies, and the receipt are retained. This is not secure erasure."
                if result.dry_run else "Portable runtime deleted. External receipt retained."
            ),
        )
    except Exception:
        if not result.dry_run:
            raise DeletionOutcomeUnknownError(RECOVERY) from None
        raise ValueError("Deletion preview output failed; no deletion was performed") from None
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gapply",
        description="Local-first, evidence-backed job application workflows.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--log-events",
        action="store_true",
        help="Emit content-free JSONL diagnostics on stderr; place before the command. "
        "Use command --json for errors and warnings on stdout.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    paths_parser = commands.add_parser("paths", help="Show resolved private runtime paths.")
    _add_json_flag(paths_parser)
    paths_parser.set_defaults(handler=_command_paths, command_name="paths")

    doctor_parser = commands.add_parser("doctor", help="Check the local installation safely.")
    doctor_parser.add_argument("--materials", action="store_true",
        help="Check optional PDF prerequisite presence without reading a profile or running PDF tools.")
    _add_json_flag(doctor_parser)
    doctor_parser.set_defaults(handler=_command_doctor, command_name="doctor")

    brief_parser = commands.add_parser("brief", help="Review saved jobs and next actions without changing state.", allow_abbrev=False)
    brief_parser.add_argument("--job-id", help="Limit the briefing to one saved job.")
    brief_parser.add_argument("--follow-up-days", type=int, default=7,
                              help="Days before suggesting a response check (1–90; default 7). No messages or reminders are sent.")
    _add_json_flag(brief_parser)
    brief_parser.set_defaults(handler=_command_brief, command_name="brief")

    backup_parser = commands.add_parser(
        "backup", help="Encrypt a bounded profile database snapshot.", allow_abbrev=False,
    )
    backup_parser.add_argument("--encrypt", required=True, metavar="ABSOLUTE_PATH")
    backup_parser.add_argument("--dry-run", action="store_true", help="Validate without writing.")
    backup_parser.add_argument(
        "--passphrase-stdin", action="store_true",
        help="Read a passphrase from bounded UTF-8 stdin instead of a no-echo terminal prompt.",
    )
    _add_json_flag(backup_parser)
    backup_parser.set_defaults(handler=_command_backup, command_name="backup")

    restore_parser = commands.add_parser(
        "restore", help="Inspect or restore a profile backup into a new private home.",
        allow_abbrev=False,
    )
    restore_parser.add_argument("--archive", required=True, metavar="ABSOLUTE_PATH")
    restore_parser.add_argument("--target-home", required=True, metavar="NEW_ABSOLUTE_PATH")
    restore_parser.add_argument("--archive-sha256", metavar="HASH_FROM_PREVIEW")
    restore_parser.add_argument(
        "--confirm", action="store_true", help="Create the new home after archive preview.",
    )
    restore_parser.add_argument("--passphrase-stdin", action="store_true")
    _add_json_flag(restore_parser)
    restore_parser.set_defaults(handler=_command_restore, command_name="restore")

    delete_parser = commands.add_parser(
        "delete", help="Preview or confirm deletion of an explicit portable home.", allow_abbrev=False,
    )
    delete_parser.add_argument("--target-home", required=True, metavar="ABSOLUTE_PATH")
    delete_parser.add_argument("--receipt", required=True, metavar="EXTERNAL_ABSOLUTE_PATH")
    delete_parser.add_argument("--preview-token")
    delete_parser.add_argument("--confirm", action="store_true")
    _add_json_flag(delete_parser)
    delete_parser.set_defaults(handler=_command_delete, command_name="delete")

    export_parser = commands.add_parser("export", help="Write a fixed-schema redacted support report.", allow_abbrev=False)
    export_parser.add_argument("--redacted", required=True, metavar="ABSOLUTE_PATH")
    export_parser.add_argument("--dry-run", action="store_true")
    _add_json_flag(export_parser)
    export_parser.set_defaults(handler=_command_export, command_name="export")

    schedules_parser = commands.add_parser("schedules", help="Configure daily search policy and execute one bounded due occurrence.", allow_abbrev=False)
    schedule_commands = schedules_parser.add_subparsers(dest="schedules_command", required=True)
    for name in ("configure", "tick", "show", "list", "pause", "resume", "ack"):
        item = schedule_commands.add_parser(name, allow_abbrev=False)
        if name == "configure":
            item.add_argument("--spec-file", required=True, help="Closed daily schedule JSON file, or - for stdin.")
        elif name != "list":
            item.add_argument("--schedule-id", required=True)
        if name in {"configure", "pause", "resume", "ack"}:
            item.add_argument("--idempotency-key", required=True)
        if name in {"configure", "tick"}:
            item.add_argument("--dry-run", action="store_true")
        if name == "ack":
            item.add_argument("--notification-id", required=True)
        _add_json_flag(item)
        item.set_defaults(handler=_command_schedules, command_name="schedules." + name)

    searches_parser = commands.add_parser("searches", help="Save a search scope and discover jobs into a draft review queue.", allow_abbrev=False)
    search_commands = searches_parser.add_subparsers(dest="searches_command", required=True)
    for name in ("configure", "run", "resume", "show", "list", "scopes", "export"):
        item = search_commands.add_parser(name, allow_abbrev=False)
        if name == "configure":
            item.add_argument("--spec-file", required=True, help="Closed search/preparation JSON file, or - for stdin.")
            item.add_argument("--idempotency-key", required=True)
            item.add_argument("--dry-run", action="store_true")
        elif name == "run":
            item.add_argument("--search-id", required=True)
            item.add_argument("--idempotency-key", required=True)
        elif name in {"resume", "show", "export"}:
            item.add_argument("--run-id", required=True)
        elif name == "list":
            item.add_argument("--search-id")
        if name in {"run", "resume"}:
            item.add_argument("--max-items", type=int, default=20)
            item.add_argument("--max-seconds", type=int, default=900)
        if name == "export":
            item.add_argument("--output-dir", required=True, help="New absolute private directory outside runtime storage.")
            item.add_argument("--dry-run", action="store_true", help="Validate the full review copy without writing files.")
        _add_json_flag(item)
        item.set_defaults(handler=_command_searches, command_name="searches." + name)

    batches_parser = commands.add_parser("batches", help="Prepare and resume a queue of draft application packages.", allow_abbrev=False)
    batch_commands = batches_parser.add_subparsers(dest="batches_command", required=True)
    for name in ("prepare", "resume", "show", "list"):
        item = batch_commands.add_parser(name, allow_abbrev=False)
        if name == "prepare":
            item.add_argument("--spec-file", required=True, help="Versioned batch specification; use - for stdin.")
            item.add_argument("--idempotency-key", required=True)
            item.add_argument("--dry-run", action="store_true", help="Read-only evidence preview; no rendering or writes.")
        if name in {"resume", "show"}:
            item.add_argument("--batch-id", required=True)
        if name in {"prepare", "resume"}:
            item.add_argument("--max-items", type=int, default=20)
            item.add_argument("--max-seconds", type=int, default=900)
        _add_json_flag(item)
        item.set_defaults(handler=_command_batches, command_name="batches." + name)

    materials_parser = commands.add_parser("materials", help="Build, inspect, approve, and export traceable resumes.", allow_abbrev=False)
    material_commands = materials_parser.add_subparsers(dest="materials_command", required=True)
    for name in ("build", "list", "show", "approve", "export"):
        item = material_commands.add_parser(name, allow_abbrev=False)
        if name == "build":
            for option in ("job-id", "claim-ids", "idempotency-key"):
                item.add_argument("--" + option, required=True)
            item.add_argument("--questions-file")
            item.add_argument("--layout-file", help="Presentation-only JSON; use - for stdin.")
            item.add_argument("--dry-run", action="store_true")
        elif name == "list":
            item.add_argument("--job-id")
        else:
            item.add_argument("--material-id", required=True)
        if name == "approve":
            for option in ("bundle-sha256", "actor-id", "idempotency-key"):
                item.add_argument("--" + option, required=True)
            item.add_argument("--confirm", action="store_true")
        if name == "export":
            item.add_argument("--output-dir", required=True)
            item.add_argument("--dry-run", action="store_true")
        _add_json_flag(item)
        item.set_defaults(handler=_command_materials, command_name="materials." + name)
    answers_parser = commands.add_parser("answers", help="Draft career answers or return NeedInfo; no storage.", allow_abbrev=False)
    answers_parser.add_argument("--job-id", required=True)
    answers_parser.add_argument("--questions-file", required=True)
    _add_json_flag(answers_parser)
    answers_parser.set_defaults(handler=_command_answers, command_name="answers")
    applications_parser = commands.add_parser("applications", help="Track manual applications with immutable history.", allow_abbrev=False)
    application_commands = applications_parser.add_subparsers(dest="applications_command", required=True)
    for name in ("add", "list", "show", "transition"):
        item = application_commands.add_parser(name, allow_abbrev=False)
        if name == "add":
            item.add_argument("--job-id", required=True)
            item.add_argument("--dry-run", action="store_true")
        elif name != "list":
            item.add_argument("--application-id", required=True)
        if name in {"add", "transition"}:
            item.add_argument("--actor-id", required=True)
            item.add_argument("--idempotency-key", required=True)
        if name == "transition":
            from grounded_apply.domain.application_states import ApplicationState
            item.add_argument("--to", required=True, choices=[s.value for s in ApplicationState])
            item.add_argument("--material-id")
            item.add_argument("--confirm-submitted", action="store_true")
            item.add_argument("--preview-token")
            item.add_argument("--confirm", action="store_true")
        _add_json_flag(item)
        item.set_defaults(handler=_command_applications, command_name="applications." + name)

    jobs_parser = commands.add_parser("jobs", help="Discover public jobs, save snapshots, and inspect evidence.", allow_abbrev=False)
    job_commands = jobs_parser.add_subparsers(dest="jobs_command", required=True)
    plan_search = job_commands.add_parser("plan-search", help="Plan bounded public searches from explicit role/location terms; no network or storage.", allow_abbrev=False)
    plan_search.add_argument("--role", action="append", required=True, metavar="TERM",
                             help="Explicit role term; repeat for 1–3 roles, each at most 128 characters.")
    plan_search.add_argument("--location", action="append", default=[], metavar="TERM",
                             help="Optional location term; repeat for up to two locations. A role-only search is always included.")
    _add_json_flag(plan_search)
    plan_search.set_defaults(handler=_command_jobs_plan_search, command_name="jobs.plan-search")
    sources = job_commands.add_parser("sources", help="Build a source manifest from board links without network or storage.", allow_abbrev=False)
    source_input = sources.add_mutually_exclusive_group(required=True)
    source_input.add_argument("--url", action="append", help="Explicit company board or posting URL; repeat for more boards.")
    source_input.add_argument("--urls-file", help="UTF-8 file with one board link per line, or - for stdin.")
    sources.add_argument("--keep-valid", action="store_true",
                         help="Keep valid sources and report rejected input positions; incomplete setup exits 2.")
    _add_json_flag(sources)
    sources.set_defaults(handler=_command_jobs_sources, command_name="jobs.sources")
    discover = job_commands.add_parser("discover", help="Read configured public ATS feeds and capture immutable jobs.", allow_abbrev=False)
    discovery_input = discover.add_mutually_exclusive_group(required=True)
    discovery_input.add_argument("--sources-file", help="Versioned source manifest; use - for stdin.")
    discovery_input.add_argument("--preset", choices=("major-tech",), help="Public employer catalog; unsupported career sites remain visible gaps.")
    discover.add_argument("--title-contains", action="append", default=[], metavar="TERM",
                          help="Optional title substring filter (OR); repeat for alternatives. This is not a fit score.")
    discover.add_argument("--location-contains", action="append", metavar="TERM",
                          help="Literal published-location substring (OR); repeat up to 20 terms. Applied before the selected-job limit; missing locations default to include.")
    discover.add_argument("--missing-location", choices=("include", "exclude"),
                          help="Explicit treatment of unknown locations; include is the location-filter default. Either location option selects report schema 2.")
    discover.add_argument("--limit-per-source", type=int, default=100,
                          help="Maximum selected jobs per source (1–1000; default 100); capped results report partial coverage.")
    discover.add_argument("--dry-run", action="store_true", help="Fetch and preview without opening or writing profile storage.")
    _add_json_flag(discover)
    discover.set_defaults(handler=_command_jobs_discover, command_name="jobs.discover")
    for name in ("add", "list", "show", "assess"):
        item = job_commands.add_parser(name, allow_abbrev=False)
        if name == "add":
            for option in ("url", "source-file", "idempotency-key"):
                item.add_argument("--" + option, required=True)
            item.add_argument("--dry-run", action="store_true")
        elif name != "list":
            item.add_argument("--job-id", required=True)
        _add_json_flag(item)
        item.set_defaults(handler=_command_jobs, command_name="jobs." + name)

    profile_parser = commands.add_parser("profile", help="Manage candidate profile data.")
    profile_commands = profile_parser.add_subparsers(dest="profile_command", required=True)
    from grounded_apply.services.profile_interview import INTERVIEW_TOPICS
    profile_interview = profile_commands.add_parser("interview", help="Ask optional profile questions a few at a time; no answers stored.", allow_abbrev=False)
    profile_interview.add_argument("--topic", choices=("all", *INTERVIEW_TOPICS), default="all")
    profile_interview.add_argument("--depth", type=int, choices=(1, 2, 3), default=1)
    profile_interview.add_argument("--after", help="Continue after a question ID in the same topic and depth round.")
    profile_interview.add_argument("--limit", type=int, default=3, help="Questions to show (1–10, default 3).")
    _add_json_flag(profile_interview)
    profile_interview.set_defaults(handler=_command_profile_interview, command_name="profile.interview")
    profile_extract = profile_commands.add_parser("extract", help="Propose exact resume text spans without storing them.", allow_abbrev=False)
    profile_extract.add_argument("--source-file", required=True)
    profile_extract.add_argument("--source-format", choices=("auto", "text", "latex", "pdf"), default="auto")
    profile_extract.add_argument("--extractor-version", type=int, choices=(1, 2, 3, 4), default=2,
        help="Version 4 also stops four unsupported headings from inheriting a fact type; versions 1–3 and default 2 remain compatible.")
    _add_json_flag(profile_extract)
    profile_extract.set_defaults(handler=_command_profile_extract, command_name="profile.extract")
    group_publication = profile_commands.add_parser("group-publication",
        help="Group explicitly chosen publication fragments into a complete import manifest; no storage.", allow_abbrev=False)
    for option in ("source-file", "source-sha256", "document-sha256"):
        group_publication.add_argument("--" + option, required=True)
    group_publication.add_argument("--indexes", action="append", required=True, metavar="I,J",
        help="Consecutive original proposal indexes for one publication; repeat for separate disjoint groups.")
    group_publication.add_argument("--source-format", choices=("auto", "text", "latex", "pdf"), default="auto")
    group_publication.add_argument("--extractor-version", type=int, choices=(1, 2, 3, 4), required=True,
        help="Use the version displayed by profile extract; there is no default.")
    group_publication.add_argument("--allow-partial", action="store_true",
        help="Acknowledge reported document gaps when constructing a proposal; retains nothing.")
    _add_json_flag(group_publication)
    group_publication.set_defaults(handler=_command_profile_group_publication, command_name="profile.group-publication")
    profile_onboard = profile_commands.add_parser("onboard", help="Import selected extracted facts for review.", allow_abbrev=False)
    for option in ("source-file", "source-sha256", "select", "idempotency-key"):
        profile_onboard.add_argument("--" + option, required=True)
    profile_onboard.add_argument("--dry-run", action="store_true")
    profile_onboard.add_argument("--source-format", choices=("auto", "text", "latex", "pdf"), default="auto")
    profile_onboard.add_argument("--document-sha256", help="Original document hash shown by extract; required for PDF/LaTeX.")
    profile_onboard.add_argument("--allow-partial", action="store_true",
        help="Explicitly retain selected readable facts despite reported document extraction gaps.")
    profile_onboard.add_argument("--extractor-version", type=int, choices=(1, 2, 3, 4),
        help="Required for indexed selections; use the version displayed during extraction. --select all defaults to 2.")
    profile_onboard.add_argument("--retain-all-facts", action="store_true",
        help="Allow complete fact retention for a selected index set; --select all implies this policy.")
    _add_json_flag(profile_onboard)
    profile_onboard.set_defaults(handler=_command_profile_onboard, command_name="profile.onboard")

    profile_show = profile_commands.add_parser("show", help="Show the provenance-checked effective profile.", allow_abbrev=False)
    _add_json_flag(profile_show)
    profile_show.set_defaults(handler=_command_profile_show, command_name="profile.show")
    from grounded_apply.services.profile_inventory import PROFILE_INVENTORY_TOPICS
    inventory = profile_commands.add_parser("inventory", help="Count retained claims or inspect one topic without changing the profile.", allow_abbrev=False)
    inventory.add_argument("--topic", choices=PROFILE_INVENTORY_TOPICS,
                           help="Inspect this topic only; omitted shows counts without claim text.")
    inventory.add_argument("--limit", type=int, help="Topic page size (1–50, default 20); requires --topic.")
    inventory_anchor = inventory.add_mutually_exclusive_group()
    inventory_anchor.add_argument("--after", help="Continue after a retained claim in this topic; requires --topic.")
    inventory_anchor.add_argument("--after-json", help="JSON-string continuation for claim IDs with nonprinting characters.")
    _add_json_flag(inventory)
    inventory.set_defaults(handler=_command_profile_inventory, command_name="profile.inventory")
    profile_retire = profile_commands.add_parser("retire", help="Preview or confirm withdrawal/replacement of an approved claim.", allow_abbrev=False)
    for option in ("claim-id", "actor-id", "idempotency-key"):
        profile_retire.add_argument("--" + option, required=True)
    profile_retire.add_argument("--replacement-claim-id")
    profile_retire.add_argument("--preview-token")
    profile_retire.add_argument("--confirm", action="store_true")
    _add_json_flag(profile_retire)
    profile_retire.set_defaults(handler=_command_profile_retire, command_name="profile.retire")

    profile_init = profile_commands.add_parser(
        "init", help="Initialize private profile storage idempotently."
    )
    profile_init.add_argument(
        "--dry-run", action="store_true", help="Show planned paths without writing files."
    )
    _add_json_flag(profile_init)
    profile_init.set_defaults(handler=_command_profile_init, command_name="profile.init")

    profile_import = profile_commands.add_parser(
        "import", help="Create review-only claims from structured text proposals."
    )
    profile_import.add_argument(
        "--source-file",
        required=True,
        metavar="PATH|-",
        help="Read UTF-8 source text from a file, or from stdin with '-'.",
    )
    profile_import.add_argument(
        "--proposals-file",
        required=True,
        metavar="PATH|-",
        help="Read the structured proposal JSON from a file, or stdin with '-'.",
    )
    profile_import.add_argument(
        "--idempotency-key",
        required=True,
        metavar="OPAQUE_KEY",
        help="Opaque retry key; do not include candidate data.",
    )
    profile_import.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and summarize without opening or changing profile storage.",
    )
    profile_import.add_argument("--retain-all-facts", action="store_true",
        help="Explicitly retain all proposed career facts without a source-percentage limit; approval is still required.")
    profile_import.add_argument("--source-kind", choices=("resume", "user-statement"), default="resume",
        help="Origin of these facts; user-statement retains exact text answers for separate review.")
    profile_import.add_argument("--source-format", choices=("text", "auto", "latex", "pdf"), default="text",
        help="Default exact UTF-8 text; choose auto/PDF/LaTeX to bind proposals to locally extracted text.")
    profile_import.add_argument("--document-sha256", help="Original hash from profile extract; required for PDF/LaTeX.")
    profile_import.add_argument("--allow-partial", action="store_true",
        help="Explicitly retain proposals from an extraction with reported gaps.")
    _add_json_flag(profile_import)
    profile_import.set_defaults(
        handler=_command_profile_import, command_name="profile.import"
    )

    profile_review = profile_commands.add_parser(
        "review", help="Display pending profile claims and evidence without changing them."
    )
    profile_review.add_argument("--limit", type=int,
        help="Show a page of 1–50 pending facts; omitted means the complete queue.")
    review_anchor = profile_review.add_mutually_exclusive_group()
    review_anchor.add_argument("--after", metavar="CLAIM_ID",
        help="Continue after a displayed claim, even after deciding it; requires --limit.")
    review_anchor.add_argument("--after-json", metavar="JSON_STRING",
        help="JSON-encoded continuation ID for legacy identifiers with control characters; requires --limit.")
    review_selection = profile_review.add_mutually_exclusive_group()
    review_selection.add_argument("--claim-id", metavar="CLAIM_ID",
        help="Review one chosen pending fact by its inventory ID; cannot combine with pagination.")
    review_selection.add_argument("--claim-id-json", metavar="JSON_STRING",
        help="JSON-string ID for one pending fact, including nonprinting legacy IDs; cannot combine with pagination.")
    _add_json_flag(profile_review)
    profile_review.set_defaults(
        handler=_command_profile_review, command_name="profile.review"
    )

    profile_decide = profile_commands.add_parser(
        "decide",
        help="Preview or record one explicit profile review decision.",
        allow_abbrev=False,
    )
    profile_decide.add_argument("--claim-id", required=True, metavar="CLAIM_ID")
    profile_decide.add_argument(
        "--review-token",
        required=True,
        metavar="OPAQUE_TOKEN",
        help="Opaque review token from `profile review`; do not include candidate data.",
    )
    profile_decide.add_argument(
        "--decision",
        required=True,
        metavar="approve|reject",
    )
    profile_decide.add_argument(
        "--actor-id",
        required=True,
        metavar="OPAQUE_ACTOR_ID",
        help="Opaque audit actor identifier; do not include candidate data.",
    )
    profile_decide.add_argument(
        "--idempotency-key",
        required=True,
        metavar="OPAQUE_KEY",
        help="Opaque retry key; do not include candidate data.",
    )
    profile_decide.add_argument(
        "--confirm",
        action="store_true",
        help="Record the decision in private profile storage.",
    )
    _add_json_flag(profile_decide)
    profile_decide.set_defaults(
        handler=_command_profile_decide, command_name="profile.decide"
    )

    return parser


def _command_name_from_argv(argv: Sequence[str]) -> str:
    if not argv:
        return "unknown"
    for root, names in (("schedules", {"configure", "tick", "show", "list", "pause", "resume", "ack"}),
                        ("searches", {"configure", "run", "resume", "show", "list", "scopes", "export"}),
                        ("batches", {"prepare", "resume", "show", "list"}),
                        ("materials", {"build", "list", "show", "approve", "export"}),
                        ("applications", {"add", "list", "show", "transition"})):
        if argv[0] == root and len(argv) > 1 and argv[1] in names:
            return root + "." + argv[1]
    if argv[0] == "jobs" and len(argv) > 1 and argv[1] in {"add", "list", "show", "assess", "discover", "sources", "plan-search"}:
        return "jobs." + argv[1]
    if argv[0] == "profile" and len(argv) > 1:
        profile_command = argv[1]
        if profile_command in {"init", "import", "review", "decide", "show", "retire", "extract", "onboard", "interview", "inventory", "group-publication"}:
            return f"profile.{profile_command}"
    if argv[0] in {"paths", "doctor", "brief", "backup", "restore", "delete", "answers", "export"}:
        return argv[0]
    return "unknown"


def _main(
    argv: Sequence[str] | None = None,
    *,
    diagnostics: CommandDiagnostics | None = None,
) -> int:
    parser = build_parser()
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    parse_stderr = io.StringIO()
    try:
        with redirect_stderr(parse_stderr):
            args = parser.parse_args(raw_argv)
    except SystemExit as error:
        if error.code != 2:
            captured = parse_stderr.getvalue()
            if captured:
                print(captured, file=sys.stderr, end="")
            raise
        command_name = _command_name_from_argv(raw_argv)
        if "--json" in raw_argv:
            parse_args = argparse.Namespace(json=True)
            _emit(
                parse_args,
                command=command_name,
                data=None,
                error={
                    "message": "Invalid command arguments; run the command's --help.",
                    "type": "CliUsageError",
                },
                message="Invalid command arguments.",
                ok=False,
            )
        elif command_name in {"profile.review", "profile.inventory", "profile.group-publication", "profile.decide", "profile.retire", "backup", "restore", "delete"} or command_name.startswith(("schedules.", "searches.", "batches.", "materials.", "applications.", "jobs.")):
            print(
                "Error: Invalid command arguments. No external action was taken.",
                file=sys.stderr,
            )
        else:
            print(parse_stderr.getvalue(), file=sys.stderr, end="")
        return 2
    handler: Command = args.handler
    try:
        return handler(args)
    except KeyboardInterrupt:
        if getattr(args, "command_name", None) in {"schedules.configure", "schedules.tick", "schedules.pause", "schedules.resume", "schedules.ack"}:
            if not getattr(args, "dry_run", False):
                if diagnostics is not None:
                    diagnostics.require_schedule_recovery()
                print(f"Interrupted. {_SCHEDULE_RECOVERY}", file=sys.stderr)
            else:
                print("Interrupted. Preview saved no schedule changes.", file=sys.stderr)
        elif getattr(args, "command_name", None) in {"searches.configure", "searches.run", "searches.resume"}:
            if not getattr(args, "dry_run", False):
                if diagnostics is not None:
                    diagnostics.require_search_recovery()
                print(f"Interrupted. {_SEARCH_RECOVERY}", file=sys.stderr)
            else:
                print("Interrupted. No search scope was saved.", file=sys.stderr)
        elif getattr(args, "command_name", None) in {"batches.prepare", "batches.resume"}:
            if not getattr(args, "dry_run", False):
                if diagnostics is not None:
                    diagnostics.require_batch_recovery()
                print(f"Interrupted. {_BATCH_RECOVERY}", file=sys.stderr)
            else:
                print("Interrupted. No batch was saved.", file=sys.stderr)
        elif getattr(args, "command_name", None) == "jobs.discover":
            print("Interrupted. No snapshots were saved." if getattr(args, "dry_run", False)
                  else "Interrupted. Snapshots may already be saved. Repeat discovery to recover unchanged captures.", file=sys.stderr)
        elif getattr(args, "command_name", None) == "delete":
            from grounded_apply.services.deletion import RECOVERY
            if getattr(args, "confirm", False):
                if diagnostics is not None:
                    diagnostics.require_deletion_recovery()
                print(f"Interrupted. {RECOVERY}", file=sys.stderr)
            else:
                print("Interrupted. No deletion was performed.", file=sys.stderr)
        elif getattr(args, "command_name", None) in {"backup", "restore"}:
            if diagnostics is not None:
                diagnostics.require_backup_recovery()
            print(
                "Interrupted. Backup or restore may be incomplete or already complete. "
                "Retry the same request; incomplete output requires a new destination. "
                "No existing data was overwritten.", file=sys.stderr,
            )
        elif getattr(args, "command_name", None) in {"profile.decide", "profile.retire", "materials.approve", "applications.transition"}:
            if getattr(args, "confirm", False):
                if diagnostics is not None:
                    diagnostics.require_decision_recovery()
                print(
                    f"Interrupted. {_POST_COMMIT_DECISION_MESSAGE} "
                    "No external action was taken.",
                    file=sys.stderr,
                )
            else:
                print(
                    "Interrupted. No decision or external action was recorded.",
                    file=sys.stderr,
                )
        else:
            print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as error:
        from grounded_apply.services.backup import BackupOutcomeUnknownError
        from grounded_apply.services.deletion import DeletionOutcomeUnknownError

        if isinstance(error, DeletionOutcomeUnknownError) and diagnostics is not None:
            diagnostics.require_deletion_recovery()
        if isinstance(error, BackupOutcomeUnknownError) and diagnostics is not None:
            diagnostics.require_backup_recovery()
        if isinstance(error, BatchOutcomeUnknownError) and diagnostics is not None:
            diagnostics.require_batch_recovery()
        if isinstance(error, SearchOutcomeUnknownError) and diagnostics is not None:
            diagnostics.require_search_recovery()
        if isinstance(error, ScheduleOutcomeUnknownError) and diagnostics is not None:
            diagnostics.require_schedule_recovery()
        if isinstance(error, PostCommitOutputError):
            if diagnostics is not None:
                diagnostics.require_decision_recovery()
            print(
                f"Error: {error} No external action was taken.",
                file=sys.stderr,
            )
            return 2
        if getattr(args, "json", False):
            try:
                _emit(
                    args,
                    command=getattr(
                        args, "command_name", getattr(args, "command", "unknown")
                    ),
                    data=None,
                    error={"message": str(error), "type": type(error).__name__},
                    message=f"Error: {error}",
                    ok=False,
                )
            except Exception:
                suffix = (
                    " No external action was taken."
                    if getattr(args, "command_name", None) == "profile.decide"
                    else ""
                )
                print(f"Error: {error}{suffix}", file=sys.stderr)
        else:
            suffix = (
                " No external action was taken."
                if getattr(args, "command_name", None) == "profile.decide"
                else ""
            )
            print(f"Error: {error}{suffix}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI with optional content-free diagnostics on a separate stream.

    Stdout remains a private response channel. With --log-events, stderr holds
    only fixed-schema events; human errors and warnings are discarded there.
    JSON callers receive their normal errors and warnings in stdout instead.
    """

    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    if not raw_argv or raw_argv[0] != "--log-events":
        return _main(raw_argv)

    from grounded_apply.diagnostics import (
        DiagnosticCommand,
        DiagnosticOutcome,
        DiscardDiagnostics,
    )

    command_argv = raw_argv[1:]
    events = CommandDiagnostics(
        sys.stderr, DiagnosticCommand(_command_name_from_argv(command_argv))
    )
    events.emit(DiagnosticOutcome.STARTED)
    outcome = DiagnosticOutcome.FAILED
    try:
        with redirect_stderr(DiscardDiagnostics()):
            result = _main(command_argv, diagnostics=events)
        outcome = (
            DiagnosticOutcome.SUCCEEDED if result == 0
            else DiagnosticOutcome.INTERRUPTED if result == 130
            else DiagnosticOutcome.FAILED
        )
        return result
    except SystemExit as error:
        if error.code in (None, 0):
            outcome = DiagnosticOutcome.SUCCEEDED
        raise
    except KeyboardInterrupt:
        outcome = DiagnosticOutcome.INTERRUPTED
        return 130
    except Exception:
        # Parsing/output can fail outside the normal handler error envelope.
        # Do not let an unhandled traceback enter the structured log channel.
        return 2
    finally:
        events.emit(outcome)


if __name__ == "__main__":
    raise SystemExit(main())
