"""Dependency-free command line interface for the first Grounded Apply slice."""

from __future__ import annotations

import argparse
import json
import os
import platform
import stat
import sys
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from grounded_apply import __version__
from grounded_apply.config import (
    HOME_ENV_VAR,
    RuntimePaths,
    require_initialized_profile_storage,
    require_runtime_outside_repository,
    resolve_runtime_paths,
)
from grounded_apply.json_support import dumps


Command = Callable[[argparse.Namespace], int]
_MAX_SOURCE_INPUT_BYTES = 16 * 1024 * 1024
_MAX_PROPOSAL_INPUT_BYTES = 4 * 1024 * 1024
_IDEMPOTENCY_KEY_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-"
)


class CliInputError(ValueError):
    """A file/stdin CLI payload does not match the public input contract."""


class ProfileStorageNotInitializedError(RuntimeError):
    """A profile data command requires an initialized private database."""


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

    return SQLiteRepository(
        paths.database,
        read_only=read_only,
        existing_only=existing_only,
    )


def _read_schema_version(paths: RuntimePaths) -> int | None:
    if not paths.database.exists():
        return None
    from grounded_apply.repositories import inspect_schema

    version = inspect_schema(paths.database)
    return None if version == 0 else version


def _command_doctor(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    try:
        require_runtime_outside_repository(paths)
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
    try:
        schema_version = _read_schema_version(paths)
        checks["database"] = {
            "initialized": schema_version is not None,
            "ok": True,
            "schema_version": schema_version,
        }
        if schema_version is None:
            warnings.append("Profile storage is not initialized; run `gapply profile init`.")
    except Exception as error:  # fail closed and report without leaking database contents
        checks["database"] = {
            "error": f"{type(error).__name__}: {error}",
            "initialized": paths.database.exists(),
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


_DEFAULT_CONFIG = """# Grounded Apply local configuration.
# Personal data belongs under the runtime data directory, never in this repository.

[privacy]
telemetry = false

[automation]
allow_submission = false
visible_browser = true
"""


def _ensure_default_config(paths: RuntimePaths) -> bool:
    if paths.config_file.exists():
        return False
    paths.config_file.write_text(_DEFAULT_CONFIG, encoding="utf-8")
    paths.config_file.chmod(0o600)
    return True


def _command_profile_init(args: argparse.Namespace) -> int:
    paths = resolve_runtime_paths()
    require_runtime_outside_repository(paths)
    if args.dry_run:
        _emit(
            args,
            command="profile.init",
            data={
                "database": str(paths.database),
                "dry_run": True,
                "would_create_config": not paths.config_file.exists(),
                "would_initialize_database": not paths.database.exists(),
            },
            message=f"Would initialize private profile storage at {paths.database}",
        )
        return 0
    paths.ensure_private_directories()
    config_created = _ensure_default_config(paths)
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
            file_stat = path.stat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise CliInputError(f"{label.capitalize()} must be a regular file")
            if file_stat.st_size > max_bytes:
                raise CliInputError(f"{label.capitalize()} exceeds the size limit")
            with path.open("rb") as stream:
                raw = stream.read(max_bytes + 1)
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
        ProposedImportClaim,
        TextSourceSpan,
    )

    data = _load_proposal_object(proposal_text)
    _check_fields(
        data,
        required=frozenset(
            {
                "schema_version",
                "source_ref",
                "extraction_method",
                "span_index_base",
                "span_unit",
                "span_end",
                "proposals",
            }
        ),
        label="Proposal input",
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise CliInputError("Proposal input schema_version must be 1")
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
        source_ref=_required_text(
            data["source_ref"], label="source_ref", max_length=512
        ),
        source_text=source_text,
        proposals=tuple(proposals),
        extraction_method=_required_text(
            data["extraction_method"], label="extraction_method", max_length=128
        ),
    )


_REVIEW_WARNING = (
    "Imported facts remain unverified and unusable until a separate explicit "
    "review decision is implemented."
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
    require_initialized_profile_storage(paths)
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
    from grounded_apply.services import ProfileService

    paths = resolve_runtime_paths()
    require_runtime_outside_repository(paths)
    if args.source_file == "-" and args.proposals_file == "-":
        raise CliInputError("Only one profile import input may read from stdin")
    idempotency_key = _validated_idempotency_key(args.idempotency_key)

    source_text = _read_utf8_input(
        args.source_file,
        label="source input",
        max_bytes=_MAX_SOURCE_INPUT_BYTES,
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
                "profile proposals; storage was not checked and no files or records "
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
    _emit(
        args,
        command="profile.import",
        data={
            "claim_count": len(result.claims),
            "claim_ids": [claim.id for claim in result.claims],
            "dry_run": False,
            "evidence_count": len(result.evidence),
            "evidence_ids": [evidence.id for evidence in result.evidence],
            "review_required": True,
            "source_sha256": result.source_sha256,
            "workflow_run_id": result.workflow_run_id,
        },
        message=(
            f"The import has {len(result.claims)} review-only profile proposals. "
            "Run `gapply profile review` to inspect them."
        ),
        warnings=(_REVIEW_WARNING,),
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
    lines.append("No records were changed; approval requires a separate explicit action.")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gapply",
        description="Local-first, evidence-backed job application workflows.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    paths_parser = commands.add_parser("paths", help="Show resolved private runtime paths.")
    _add_json_flag(paths_parser)
    paths_parser.set_defaults(handler=_command_paths, command_name="paths")

    doctor_parser = commands.add_parser("doctor", help="Check the local installation safely.")
    _add_json_flag(doctor_parser)
    doctor_parser.set_defaults(handler=_command_doctor, command_name="doctor")

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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Command = args.handler
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as error:
        if getattr(args, "json", False):
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
        else:
            print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
