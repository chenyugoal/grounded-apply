"""Dependency-free command line interface for the first Grounded Apply slice."""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
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


def _command_doctor(args: argparse.Namespace) -> int:
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

    source_text = _read_utf8_input(
        args.source_file,
        label="source input",
        max_bytes=PROFILE_IMPORT_MAX_SOURCE_BYTES,
    )
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
    preview = ProfileService.preview_import_proposal(request)
    if args.dry_run:
        data = to_jsonable(preview)
        assert isinstance(data, dict)
        data["dry_run"] = True
        data["storage_checked"] = False
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
        if unicodedata.category(character).startswith("C"):
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


def _command_profile_review(args: argparse.Namespace) -> int:
    from grounded_apply.domain import to_jsonable
    from grounded_apply.services import ProfileService

    paths = resolve_runtime_paths()
    require_runtime_outside_repository(paths)
    repository = _open_initialized_profile_repository(paths, read_only=True)
    try:
        items = ProfileService(repository).list_review_items()
    finally:
        repository.close()
    _emit(
        args,
        command="profile.review",
        data={
            "items": to_jsonable(items),
            "pending_count": len(items),
            "read_only": True,
        },
        message=_review_message(items),
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
    _add_json_flag(doctor_parser)
    doctor_parser.set_defaults(handler=_command_doctor, command_name="doctor")

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

    profile_parser = commands.add_parser("profile", help="Manage candidate profile data.")
    profile_commands = profile_parser.add_subparsers(dest="profile_command", required=True)
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
    _add_json_flag(profile_import)
    profile_import.set_defaults(
        handler=_command_profile_import, command_name="profile.import"
    )

    profile_review = profile_commands.add_parser(
        "review", help="Display pending profile claims and evidence without changing them."
    )
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
    if argv[0] == "profile" and len(argv) > 1:
        profile_command = argv[1]
        if profile_command in {"init", "import", "review", "decide"}:
            return f"profile.{profile_command}"
    if argv[0] in {"paths", "doctor", "backup", "restore"}:
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
        elif command_name in {"profile.decide", "backup", "restore"}:
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
        if getattr(args, "command_name", None) in {"backup", "restore"}:
            if diagnostics is not None:
                diagnostics.require_backup_recovery()
            print(
                "Interrupted. Backup or restore may be incomplete or already complete. "
                "Retry the same request; incomplete output requires a new destination. "
                "No existing data was overwritten.", file=sys.stderr,
            )
        elif getattr(args, "command_name", None) == "profile.decide":
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

        if isinstance(error, BackupOutcomeUnknownError) and diagnostics is not None:
            diagnostics.require_backup_recovery()
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
