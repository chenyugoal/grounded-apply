"""Fail-closed schemas and content policy for profile import proposals.

The caller-managed raw source is used only for exact-span, minimization, and
bounded selected-context checks. It is not globally classified for sensitive
patterns and is never persisted by this workflow.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import unquote_plus

from grounded_apply.domain import JsonValue


PROFILE_IMPORT_VALUE_SCHEMA_VERSION = 1
PROFILE_IMPORT_CONTENT_POLICY_VERSION = 1
PROFILE_IMPORT_MAX_SOURCE_BYTES = 16 * 1024 * 1024
PROFILE_IMPORT_MAX_SELECTED_EVIDENCE_CODEPOINTS = 4096
PROFILE_IMPORT_MAX_TOTAL_EVIDENCE_CODEPOINTS = 65_536
PROFILE_IMPORT_MAX_TOTAL_METADATA_CODEPOINTS = 1_048_576

_MAX_CANONICAL_TEXT_CODEPOINTS = 2048
_MAX_CONTENT_COMPONENT_CODEPOINTS = 8192
_MAX_FRAGMENT_BOUNDARY_CODEPOINTS = 64
_MAX_SELECTED_EVIDENCE_LINES = 16
_BROAD_DOCUMENT_PERCENT = 80
_MAX_MINUTES = 525_600
_MAX_PERCENT_DECODE_ROUNDS = 8
_DOCUMENT_WINDOW_SIZES = (16, 8, 4, 2, 1)
_DOCUMENT_MAX_SAMPLED_WINDOWS = 4096
_YEAR_MONTH_PATTERN = re.compile(r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])")
_DOCUMENT_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_SENSITIVE_LABEL_ONLY_PATTERN = re.compile(
    r"\s*(?:work[\s_-]*authori[sz]ation|employment[\s_-]*eligibility|"
    r"sponsor(?:ship)?|visa[\s_-]*status|immigration[\s_-]*status|"
    r"citizenship[\s_-]*status|nationality|social[\s_-]*security(?:[\s_-]*number)?|"
    r"ssn|passport(?:[\s_-]*(?:number|no\.?|id))?|"
    r"driver'?s?[\s_-]*licen[cs]e(?:[\s_-]*(?:number|no\.?|id))?|"
    r"national[\s_-]*id|government[\s_-]*id|tax[\s_-]*id|itin|ein|"
    r"api[\s_+-]*(?:token|key)|access[\s_-]*key(?:[\s_-]*id)?|"
    r"secret[\s_-]*access[\s_-]*key|password|passwd|pwd|token|secret|"
    r"credential(?:s)?|cookie|session[\s_-]*(?:id|token)|private[\s_-]*key)\s*:?[\s]*",
    re.IGNORECASE,
)
_OWNERSHIP_LEVELS = frozenset(
    {"supported", "contributed", "co-led", "led", "owned"}
)
_SENSITIVE_ASSIGNMENT_KEYS = (
    "workauthorization",
    "workeligibility",
    "employmenteligibility",
    "sponsorship",
    "visa",
    "visastatus",
    "immigrationstatus",
    "ead",
    "citizenship",
    "citizenshipstatus",
    "nationality",
    "ssn",
    "socialsecurity",
    "socialsecuritynumber",
    "passport",
    "passportnumber",
    "passportno",
    "passportid",
    "driverslicense",
    "driverslicensenumber",
    "driverslicenseno",
    "driverslicenseid",
    "nationalid",
    "governmentid",
    "governmentissuedid",
    "taxid",
    "taxpayerid",
    "taxpayeridentificationnumber",
    "itin",
    "ein",
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "credential",
    "credentials",
    "cookie",
    "sessionid",
    "apikey",
    "apitoken",
    "accesstoken",
    "refreshtoken",
    "authtoken",
    "bearertoken",
    "sessiontoken",
    "privatekey",
    "sshprivatekey",
    "clientsecret",
    "secretkey",
    "secretaccesskey",
    "awssecretaccesskey",
    "accesskeyid",
)
_SENSITIVE_ASSIGNMENT_KEY_SET = frozenset(_SENSITIVE_ASSIGNMENT_KEYS)

type _ValueValidator = Callable[[object], None]


@dataclass(frozen=True, slots=True)
class _ValueSchema:
    version: int
    shape: str
    validator: _ValueValidator

    def validate(self, value: object) -> None:
        self.validator(value)


def _require_atomic_text(
    value: object,
    *,
    field_name: str,
    max_codepoints: int,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-blank trimmed text")
    if len(value) > max_codepoints:
        raise ValueError(f"{field_name} exceeds its registered length limit")
    if any(
        character in "\r\n"
        or unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in value
    ):
        raise ValueError(
            f"{field_name} must be one line without control or format characters"
        )
    return value


def _scalar_text_schema(max_codepoints: int) -> _ValueSchema:
    def validate(value: object) -> None:
        _require_atomic_text(
            value,
            field_name="proposal value",
            max_codepoints=max_codepoints,
        )

    return _ValueSchema(
        version=PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
        shape="bounded non-blank text",
        validator=validate,
    )


def _require_exact_object(
    value: object,
    *,
    fields: frozenset[str],
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("proposal value must be an object")
    if set(value) - fields:
        raise ValueError("proposal value contains an unexpected field")
    if fields - set(value):
        raise ValueError("proposal value is missing a required field")
    return value


def _validate_employment_title(value: object) -> None:
    record = _require_exact_object(
        value,
        fields=frozenset({"employer", "title"}),
    )
    _require_atomic_text(
        record["employer"],
        field_name="employment employer",
        max_codepoints=512,
    )
    _require_atomic_text(
        record["title"],
        field_name="employment title",
        max_codepoints=512,
    )


def _require_year_month(
    value: object,
    *,
    field_name: str,
    allow_present: bool = False,
) -> str:
    text = _require_atomic_text(
        value,
        field_name=field_name,
        max_codepoints=7,
    )
    if allow_present and text == "present":
        return text
    if _YEAR_MONTH_PATTERN.fullmatch(text) is None:
        raise ValueError(f"{field_name} must use YYYY-MM")
    return text


def _validate_employment_dates(value: object) -> None:
    record = _require_exact_object(
        value,
        fields=frozenset({"start", "end"}),
    )
    start = _require_year_month(record["start"], field_name="employment start")
    end = _require_year_month(
        record["end"],
        field_name="employment end",
        allow_present=True,
    )
    if end != "present" and end < start:
        raise ValueError("employment end must not precede employment start")


def _require_minutes(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    if not 0 <= value <= _MAX_MINUTES:
        raise ValueError(f"{field_name} is outside the registered range")
    return value


def _validate_project_outcome(value: object) -> None:
    record = _require_exact_object(
        value,
        fields=frozenset({"activity", "before_minutes", "after_minutes"}),
    )
    _require_atomic_text(
        record["activity"],
        field_name="project outcome activity",
        max_codepoints=512,
    )
    _require_minutes(record["before_minutes"], field_name="before_minutes")
    _require_minutes(record["after_minutes"], field_name="after_minutes")


def _validate_project_contribution(value: object) -> None:
    record = _require_exact_object(
        value,
        fields=frozenset({"project", "contribution", "ownership"}),
    )
    _require_atomic_text(
        record["project"],
        field_name="project name",
        max_codepoints=512,
    )
    _require_atomic_text(
        record["contribution"],
        field_name="project contribution",
        max_codepoints=2048,
    )
    ownership = _require_atomic_text(
        record["ownership"],
        field_name="project ownership",
        max_codepoints=32,
    )
    if ownership not in _OWNERSHIP_LEVELS:
        raise ValueError("project ownership is not a registered level")


_PROFILE_IMPORT_VALUE_SCHEMAS: Mapping[str, _ValueSchema] = MappingProxyType(
    {
        "achievement": _scalar_text_schema(2048),
        "certification": _scalar_text_schema(512),
        "education": _scalar_text_schema(1024),
        "education_degree": _scalar_text_schema(512),
        "education_field": _scalar_text_schema(512),
        "employment_dates": _ValueSchema(
            version=PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
            shape="{start: YYYY-MM, end: YYYY-MM|present}",
            validator=_validate_employment_dates,
        ),
        "employment_description": _scalar_text_schema(2048),
        "employment_title": _ValueSchema(
            version=PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
            shape="{employer: text, title: text}",
            validator=_validate_employment_title,
        ),
        "language": _scalar_text_schema(128),
        "portfolio_item": _scalar_text_schema(2048),
        "project_contribution": _ValueSchema(
            version=PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
            shape="{project: text, contribution: text, ownership: enum}",
            validator=_validate_project_contribution,
        ),
        "project_outcome": _ValueSchema(
            version=PROFILE_IMPORT_VALUE_SCHEMA_VERSION,
            shape="{activity: text, before_minutes: int, after_minutes: int}",
            validator=_validate_project_outcome,
        ),
        "publication": _scalar_text_schema(2048),
        "skill_use": _scalar_text_schema(256),
    }
)

if any(
    schema.version != PROFILE_IMPORT_VALUE_SCHEMA_VERSION
    for schema in _PROFILE_IMPORT_VALUE_SCHEMAS.values()
):
    raise RuntimeError("profile import value schemas must share one version")


_DISALLOWED_CONTEXT_PATTERNS = (
    re.compile(r"\b(?:work|employment)[\s_-]+authori[sz](?:ation|ed)\b", re.IGNORECASE),
    re.compile(r"\bauthori[sz]ed\s+to\s+work\b", re.IGNORECASE),
    re.compile(
        r"\bable\s+to\s+work\s+(?:in|within|throughout)\s+(?:the\s+)?"
        r"(?:u\.?\s*s\.?|united\s+states|canada)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:immigration|visa)\s+status\b", re.IGNORECASE),
    re.compile(
        r"\b(?:require[sd]?|need(?:ed|s)?|without|seek(?:ing|s)?)\s+"
        r"(?:employment\s+)?sponsor(?:ship)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:h[\s-]?1b|l[\s-]?1|o[\s-]?1|f[\s-]?1|j[\s-]?1|visa)\s+"
        r"sponsor(?:ship)?\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:green\s+card|permanent\s+resident)\b", re.IGNORECASE),
    re.compile(r"\bcitizenship\s+status\b", re.IGNORECASE),
    re.compile(r"\bnationality\s*(?::|=)\s*\S+", re.IGNORECASE),
    re.compile(r"\bCanadian\s+citizen\b", re.IGNORECASE),
    re.compile(r"\bcitizen\s+of\s+[A-Za-z][A-Za-z .'-]{1,40}\b", re.IGNORECASE),
    re.compile(r"\b(?:u\.?\s*s\.?|united\s+states)\s+citizen\b", re.IGNORECASE),
    re.compile(r"\b(?:legal\s+(?:right|permission)|eligible)\s+to\s+work\b", re.IGNORECASE),
    re.compile(r"\blegally\s+(?:permitted|eligible|authori[sz]ed)\s+to\s+work\b", re.IGNORECASE),
    re.compile(r"\b(?:employment|work)\s+eligib(?:ility|le)\b", re.IGNORECASE),
    re.compile(r"\bwork\s+permit\b", re.IGNORECASE),
    re.compile(r"\b(?:currently\s+)?on\s+(?:OPT|CPT)\b", re.IGNORECASE),
    re.compile(r"\bEAD(?:\s+(?:card|status|holder))?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:h[\s-]?1b|l[\s-]?1|o[\s-]?1|f[\s-]?1|j[\s-]?1)\s+"
        r"(?:visa\s+)?(?:holder|status)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:hold(?:s|ing)?|on)\s+(?:an?\s+)?"
        r"(?:h[\s-]?1b|l[\s-]?1|o[\s-]?1|f[\s-]?1|j[\s-]?1)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:opt|cpt)\s+(?:eligible|holder|status)\b", re.IGNORECASE),
    re.compile(
        r"\bsponsor(?:ship)?\s+(?:will\s+be|is|would\s+be)\s+required\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:no\s+)?sponsor(?:ship)?\s+(?:is\s+)?(?:not\s+)?required\b", re.IGNORECASE),
    re.compile(r"\bsocial[\s_-]+security(?:[\s_-]+number)?\b", re.IGNORECASE),
    re.compile(r"\bs[\W_]*s[\W_]*n\b", re.IGNORECASE),
    re.compile(r"\bpassport[\s_-]+(?:number|no\.?|id)\b", re.IGNORECASE),
    re.compile(
        r"\bpassport(?:[\s_-]+(?:number|no\.?|id))?\s*(?:#|:)?\s*"
        r"(?=[A-Z0-9-]{5,}\b)(?=[A-Z0-9-]*[0-9-])"
        r"[A-Z0-9][A-Z0-9-]{4,}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdriver'?s[\s_-]+licen[cs]e[\s_-]+(?:number|no\.?|id)\b", re.IGNORECASE),
    re.compile(
        r"\bdriver'?s?[\s_-]+licen[cs]e(?:[\s_-]+(?:number|no\.?|id))?"
        r"\s*(?:#|:)?\s*(?=[A-Z0-9-]{5,}\b)(?=[A-Z0-9-]*[0-9-])"
        r"[A-Z0-9][A-Z0-9-]{4,}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bDL\s*(?:#|:|=)\s*(?=[A-Z0-9-]{5,}\b)"
        r"(?=[A-Z0-9-]*[0-9-])[A-Z0-9][A-Z0-9-]{4,}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:national|government[\s_-]*issued|taxpayer)[\s_-]+id\b", re.IGNORECASE),
    re.compile(
        r"\b(?:national|government|tax)[\s_-]+id\s*(?:#|:|=)\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:itin|taxpayer[\s_-]+identification[\s_-]+number)\b", re.IGNORECASE),
    re.compile(r"\bEIN\s*(?:#|:)?\s*\d{2}-\d{7}\b", re.IGNORECASE),
    re.compile(r"(?<!\d)\d{3}[\s-]\d{2}[\s-]\d{4}(?!\d)"),
    re.compile(
        r"(?<![A-Za-z0-9])(?:work[\s_-]*authori[sz]ation|sponsor(?:ship)?|visa|"
        r"immigration[\s_-]*status|ead|citizenship|nationality|ssn|passport|driver'?s?[\s_-]*"
        r"licen[cs]e|national[\s_-]*id|government[\s_-]*id|tax[\s_-]*id|itin|ein)"
        r"\s*(?::|=)\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[\s_-]+){0,3}"
        r"(?:password|passwd|pwd|token|secret|credential(?:s)?|cookie|session[\s_-]*id|"
        r"api[\s_-]*key|private[\s_-]*key|"
        r"secret[\s_-]*access[\s_-]*key|access[\s_-]*key(?:[\s_-]*id)?)"
        r"\s*(?::|=)\s*\S{4,}",
        re.IGNORECASE,
    ),
    re.compile(r"\bauthorization\s*:\s*(?:basic|bearer)\s+\S{8,}", re.IGNORECASE),
    re.compile(r"\bcookie\s*:\s*\S{8,}", re.IGNORECASE),
    re.compile(
        r"\b[A-Za-z][A-Za-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@",
        re.IGNORECASE,
    ),
    re.compile(r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", re.IGNORECASE),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", re.IGNORECASE),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b", re.IGNORECASE),
    re.compile(
        r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"
    ),
)
_DISALLOWED_CONTEXT_PATTERN = re.compile(
    "|".join(
        f"(?i:{pattern.pattern})"
        if pattern.flags & re.IGNORECASE
        else f"(?:{pattern.pattern})"
        for pattern in _DISALLOWED_CONTEXT_PATTERNS
    )
)


def registered_profile_import_claim_types() -> frozenset[str]:
    """Return every claim type with a versioned import value schema."""

    return frozenset(_PROFILE_IMPORT_VALUE_SCHEMAS)


def _iter_text_components(
    value: object,
    *,
    include_keys: bool = True,
) -> Iterator[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_text_components(item, include_keys=include_keys)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if include_keys and isinstance(key, str):
                yield key
            yield from _iter_text_components(item, include_keys=include_keys)


def _normalized_for_detection(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        ""
        if unicodedata.category(character).startswith("C")
        else " "
        if character.isspace() or unicodedata.category(character).startswith("Z")
        else character
        for character in normalized
    )


def _normalized_with_control_separators(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        " "
        if (
            character.isspace()
            or unicodedata.category(character).startswith(("C", "Z"))
        )
        else character
        for character in normalized
    )


def _normalized_document_text(value: str) -> str:
    return " ".join(_normalized_for_detection(value).split())


def _percent_decoded_variants(value: str) -> tuple[str, ...]:
    variants = [value]
    current = value
    for _ in range(_MAX_PERCENT_DECODE_ROUNDS):
        decoded = unquote_plus(current)
        if decoded == current:
            return tuple(variants)
        if decoded not in variants:
            variants.append(decoded)
        current = decoded
    if unquote_plus(current) != current:
        raise ValueError("proposal content uses excessive nested percent encoding")
    return tuple(variants)


def _contains_disallowed(value: str) -> bool:
    return any(
        _DISALLOWED_CONTEXT_PATTERN.search(normalized) is not None
        for normalized in (
            _normalized_for_detection(value),
            _normalized_with_control_separators(value),
        )
    )


def _compact_assignment_fragment(value: str) -> str:
    return "".join(
        character
        for character in _normalized_for_detection(value).casefold()
        if character.isalnum()
    )


def _assignment_left_fragments(value: str) -> Iterator[str]:
    maximum_key_length = max(map(len, _SENSITIVE_ASSIGNMENT_KEYS))
    normalized = _normalized_for_detection(value).casefold()
    suffix_has_nonspace = [False] * (len(normalized) + 1)
    for index in range(len(normalized) - 1, -1, -1):
        suffix_has_nonspace[index] = (
            suffix_has_nonspace[index + 1] or not normalized[index].isspace()
        )
    compact_suffixes = {""}
    for index, character in enumerate(normalized):
        if character.isalnum():
            compact_suffixes = {
                suffix + character
                for suffix in compact_suffixes
                if len(suffix) < maximum_key_length
            }
            continue
        if (
            character in "=:#"
            and suffix_has_nonspace[index + 1]
        ):
            yield from (suffix for suffix in compact_suffixes if suffix)
        compact_suffixes.add("")


def _contains_exact_sensitive_assignment(value: str) -> bool:
    return any(
        fragment in _SENSITIVE_ASSIGNMENT_KEY_SET
        for fragment in _assignment_left_fragments(value)
    )


def _contains_fragmented_assignment(components: Sequence[str]) -> bool:
    if len(components) < 2 or not any(
        "=" in component or ":" in component or "#" in component
        for component in components
    ):
        return False
    maximum_key_length = max(map(len, _SENSITIVE_ASSIGNMENT_KEYS))
    compact_components = tuple(_compact_assignment_fragment(item) for item in components)
    suffix_owners: dict[str, set[int]] = {}
    for owner, component in enumerate(compact_components):
        for length in range(1, min(len(component), maximum_key_length) + 1):
            suffix_owners.setdefault(component[-length:], set()).add(owner)
    possible_splits: dict[str, set[str]] = {}
    for prefix, assignment_fragment in (
        (key[:split], key[split:])
        for key in _SENSITIVE_ASSIGNMENT_KEYS
        for split in range(1, len(key))
        if key[:split] in suffix_owners
    ):
        possible_splits.setdefault(assignment_fragment, set()).add(prefix)
    if not possible_splits:
        return False
    for owner, component in enumerate(components):
        for compact_left in _assignment_left_fragments(component):
            for prefix in possible_splits.get(compact_left, ()):
                if any(
                    prefix_owner != owner
                    for prefix_owner in suffix_owners[prefix]
                ):
                    return True
    return False


def _bounded_fragment_component(value: str) -> str:
    """Keep only the edges that can participate in a cross-field match."""

    boundary = _MAX_FRAGMENT_BOUNDARY_CODEPOINTS
    if len(value) <= boundary * 2:
        return value
    return (
        value[:boundary]
        + " GROUNDAPPLYSAFEBOUNDARY "
        + value[-boundary:]
    )


def _reject_disallowed_components(components: tuple[str, ...]) -> None:
    if any(len(item) > _MAX_CONTENT_COMPONENT_CODEPOINTS for item in components):
        raise ValueError("proposal content exceeds the safe component limit")
    variants_by_component = tuple(
        _percent_decoded_variants(item) for item in components
    )
    for item in dict.fromkeys(
        variant
        for variants in variants_by_component
        for variant in variants
    ):
        if _contains_disallowed(item) or _contains_exact_sensitive_assignment(item):
            raise ValueError(
                "proposal content contains a disallowed sensitive or credential-like value"
            )
    semantic_components = tuple(
        variants[-1] for variants in variants_by_component
    )
    seen_aggregates: set[str] = set()
    boundary = _MAX_FRAGMENT_BOUNDARY_CODEPOINTS
    detection_component_sets = (
        tuple(_normalized_for_detection(item) for item in semantic_components),
        tuple(
            _normalized_with_control_separators(item)
            for item in semantic_components
        ),
    )
    for detection_components in detection_component_sets:
        for left_index, left in enumerate(detection_components):
            for right_index, right in enumerate(detection_components):
                if left_index == right_index:
                    continue
                pair = (left[-boundary:], right[:boundary])
                for aggregate in ("".join(pair), " ".join(pair)):
                    if aggregate in seen_aggregates:
                        continue
                    seen_aggregates.add(aggregate)
                    if _contains_disallowed(aggregate):
                        raise ValueError(
                            "proposal content contains fragmented sensitive or credential-like value"
                        )
        bounded_components = tuple(
            _bounded_fragment_component(item) for item in detection_components
        )
        for sequence in (bounded_components, tuple(reversed(bounded_components))):
            for aggregate in ("".join(sequence), " ".join(sequence)):
                if aggregate in seen_aggregates:
                    continue
                seen_aggregates.add(aggregate)
                if _contains_disallowed(aggregate):
                    raise ValueError(
                        "proposal content contains fragmented sensitive or credential-like value"
                    )
    if _contains_fragmented_assignment(semantic_components):
        raise ValueError(
            "proposal content contains fragmented sensitive or credential-like value"
        )


def validate_profile_import_proposal(
    *,
    claim_type: str,
    value: JsonValue,
    canonical_text: str,
    evidence_text: str,
    evidence_context: str,
    preceding_line: str | None,
) -> None:
    """Validate one proposal's typed value and persisted content fields."""

    schema = _PROFILE_IMPORT_VALUE_SCHEMAS.get(claim_type)
    if schema is None:
        raise ValueError("claim type is not allowed for profile import")
    if len(canonical_text) > _MAX_CANONICAL_TEXT_CODEPOINTS:
        raise ValueError("proposal canonical text exceeds the atomic claim limit")
    if len(evidence_text) > PROFILE_IMPORT_MAX_SELECTED_EVIDENCE_CODEPOINTS:
        raise ValueError("proposal selected evidence exceeds the atomic claim limit")
    if len(evidence_text.splitlines()) > _MAX_SELECTED_EVIDENCE_LINES:
        raise ValueError("proposal selected evidence contains too many lines")

    schema.validate(value)
    _reject_disallowed_components(
        (
            *tuple(_iter_text_components(value, include_keys=False)),
            canonical_text,
            evidence_text,
        )
    )
    _reject_disallowed_components((evidence_context,))
    if (
        preceding_line is not None
        and _SENSITIVE_LABEL_ONLY_PATTERN.fullmatch(preceding_line) is not None
    ):
        _reject_disallowed_components((f"{preceding_line}: {evidence_context}",))


def validate_profile_import_metadata(values: tuple[str | None, ...]) -> None:
    """Reject sensitive or credential-like text in persisted import metadata."""

    components = tuple(value for value in values if value is not None)
    _reject_disallowed_components(components)


def _decoded_components(components: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        _percent_decoded_variants(component)[-1]
        for component in components
    )


def _component_decode_layers(
    components: Sequence[str],
) -> tuple[tuple[str, ...], ...]:
    variants_by_component = tuple(
        _percent_decoded_variants(component) for component in components
    )
    depth_count = max((len(variants) for variants in variants_by_component), default=1)
    layers: list[tuple[str, ...]] = []
    for depth in range(depth_count):
        layer = tuple(
            variants[min(depth, len(variants) - 1)]
            for variants in variants_by_component
        )
        if not layers or layer != layers[-1]:
            layers.append(layer)
    return tuple(layers)


def _compact_components(components: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        "".join(_normalized_document_text(item).casefold().split())
        for item in components
    )


def _source_alphanumeric_components(
    components: Sequence[str],
    *,
    source_character_counts: Counter[str],
) -> tuple[str, ...]:
    """Project onto the source's bounded alphanumeric multiset.

    This keeps unrelated padding from selecting an over-large comparison window
    while preventing repeated padding characters from manufacturing capacity.
    """

    remaining = source_character_counts.copy()
    projected: list[str] = []
    for item in components:
        component: list[str] = []
        for character in _normalized_document_text(item).casefold():
            if not character.isalnum() or remaining[character] <= 0:
                continue
            component.append(character)
            remaining[character] -= 1
        projected.append("".join(component))
    return tuple(projected)


def _token_weight_coverage(
    *,
    source_tokens: Counter[str],
    components: Sequence[str],
) -> int:
    component_tokens = Counter(
        token
        for component in components
        for match in _DOCUMENT_TOKEN_PATTERN.finditer(
            _normalized_document_text(component).casefold()
        )
        for token in (match.group(),)
    )
    return sum(
        len(token) * min(count, component_tokens[token])
        for token, count in source_tokens.items()
    )


def _document_window_size(
    *,
    source_length: int,
    components: Sequence[str],
) -> int | None:
    candidates = tuple(
        sorted(
            {
                min(16, source_length),
                *(size for size in _DOCUMENT_WINDOW_SIZES if size <= source_length),
            },
            reverse=True,
        )
    )
    for size in candidates:
        if size <= 0:
            continue
        source_windows = source_length - size + 1
        component_windows = sum(max(0, len(item) - size + 1) for item in components)
        if component_windows * 100 >= source_windows * _BROAD_DOCUMENT_PERCENT:
            return size
    return None


def _sampled_source_window_counts(source: str, *, size: int) -> Counter[str]:
    window_count = len(source) - size + 1
    sample_count = min(window_count, _DOCUMENT_MAX_SAMPLED_WINDOWS)
    if sample_count == 1:
        sample_positions = (0,)
    else:
        sample_positions = tuple(
            index * (window_count - 1) // (sample_count - 1)
            for index in range(sample_count)
        )
    sampled_windows = {source[index : index + size] for index in sample_positions}
    return Counter(
        window
        for index in range(window_count)
        for window in (source[index : index + size],)
        if window in sampled_windows
    )


def _sampled_window_coverage(
    *,
    source_counts: Counter[str],
    components: Sequence[str],
    size: int,
) -> tuple[int, int]:
    component_counts: Counter[str] = Counter()
    for component in components:
        for index in range(max(0, len(component) - size + 1)):
            window = component[index : index + size]
            if (
                window in source_counts
                and component_counts[window] < source_counts[window]
            ):
                component_counts[window] += 1
    covered = sum(component_counts.values())
    return covered, sum(source_counts.values())


def _reject_exact_source_reconstruction(
    *,
    compact_source: str,
    compact_components: Sequence[str],
    channel: str,
) -> None:
    for sequence in (compact_components, tuple(reversed(compact_components))):
        joined = "".join(sequence)
        if compact_source and compact_source in joined:
            raise ValueError(f"profile import {channel} reconstructs the whole source")


def _reject_whole_source_reconstruction(
    *,
    compact_source: str,
    components: Sequence[str],
    compact_components: Sequence[str],
    window_components: Sequence[str],
    window_size: int | None,
    channel: str,
    source_tokens: Counter[str] | None,
    source_token_weight: int,
    source_window_counts: Mapping[int, Counter[str]],
) -> None:
    _reject_exact_source_reconstruction(
        compact_source=compact_source,
        compact_components=compact_components,
        channel=channel,
    )
    compact_length = sum(len(component) for component in compact_components)
    covered_weight = (
        _token_weight_coverage(
            source_tokens=source_tokens,
            components=components,
        )
        if source_tokens is not None
        and compact_length * 100
        >= len(compact_source) * _BROAD_DOCUMENT_PERCENT
        else 0
    )
    if (
        source_token_weight
        and covered_weight * 100
        >= source_token_weight * _BROAD_DOCUMENT_PERCENT
    ):
        raise ValueError(f"profile import {channel} is too broad for its source")
    if window_size is not None:
        covered_windows, source_windows = _sampled_window_coverage(
            source_counts=source_window_counts[window_size],
            components=window_components,
            size=window_size,
        )
        if covered_windows * 100 >= source_windows * _BROAD_DOCUMENT_PERCENT:
            raise ValueError(f"profile import {channel} is too broad for its source")


def validate_profile_import_batch(
    *,
    source_text: str,
    spans: tuple[tuple[int, int], ...],
    proposals: tuple[tuple[JsonValue, str, str], ...],
    metadata: tuple[str | None, ...],
) -> None:
    """Reject a batch that reconstructs most of its source in any content channel."""

    normalized_source = _normalized_document_text(source_text).casefold()
    compact_source = "".join(normalized_source.split())
    raw_value_components = tuple(
        component
        for value, _, _ in proposals
        for component in _iter_text_components(value, include_keys=False)
    )
    raw_canonical_components = tuple(canonical for _, canonical, _ in proposals)
    raw_evidence_components = tuple(evidence for _, _, evidence in proposals)
    value_components = _decoded_components(raw_value_components)
    canonical_components = _decoded_components(raw_canonical_components)
    evidence_components = _decoded_components(raw_evidence_components)
    raw_content_components = tuple(
        component
        for value, canonical, evidence in proposals
        for component in (
            *tuple(_iter_text_components(value, include_keys=False)),
            canonical,
            evidence,
        )
    )
    raw_metadata_components = tuple(item for item in metadata if item is not None)
    metadata_components = _decoded_components(raw_metadata_components)
    if _contains_fragmented_assignment(
        (
            *value_components,
            *canonical_components,
            *evidence_components,
            *metadata_components,
        )
    ):
        raise ValueError(
            "proposal content contains fragmented sensitive or credential-like value"
        )
    component_channels: list[tuple[str, tuple[str, ...]]] = []
    for channel, raw_components in (
        ("values", raw_value_components),
        ("canonical text", raw_canonical_components),
        ("selected evidence", raw_evidence_components),
        ("metadata", raw_metadata_components),
    ):
        for depth, layer in enumerate(_component_decode_layers(raw_components)):
            component_channels.append(
                (
                    (
                        f"raw {channel}"
                        if depth == 0
                        else f"{channel} percent-decode layer {depth}"
                    ),
                    layer,
                )
            )
    window_source = "".join(
        character for character in normalized_source if character.isalnum()
    )
    source_character_counts = Counter(window_source)
    compact_channels = tuple(
        (
            channel,
            components,
            _compact_components(components),
            _source_alphanumeric_components(
                components,
                source_character_counts=source_character_counts,
            ),
        )
        for channel, components in component_channels
    )
    needs_broad_comparison = any(
        compact_source
        and sum(len(item) for item in compact_components) * 100
        >= len(compact_source) * _BROAD_DOCUMENT_PERCENT
        for _, _, compact_components, _ in compact_channels
    )
    source_tokens = (
        Counter(
            match.group()
            for match in _DOCUMENT_TOKEN_PATTERN.finditer(normalized_source)
        )
        if needs_broad_comparison
        else None
    )
    source_token_weight = (
        sum(len(token) * count for token, count in source_tokens.items())
        if source_tokens is not None
        else 0
    )
    channel_window_sizes = tuple(
        _document_window_size(
            source_length=len(window_source),
            components=window_components,
        )
        for _, _, _, window_components in compact_channels
    )
    needed_window_sizes = {
        size for size in channel_window_sizes if size is not None
    }
    source_window_counts = {
        size: _sampled_source_window_counts(window_source, size=size)
        for size in needed_window_sizes
    }
    for (
        channel,
        components,
        compact_components,
        window_components,
    ), window_size in zip(compact_channels, channel_window_sizes, strict=True):
        _reject_whole_source_reconstruction(
            compact_source=compact_source,
            components=components,
            compact_components=compact_components,
            window_components=window_components,
            window_size=window_size,
            channel=channel,
            source_tokens=source_tokens,
            source_token_weight=source_token_weight,
            source_window_counts=source_window_counts,
        )
    for depth, layer in enumerate(_component_decode_layers(raw_content_components)):
        _reject_exact_source_reconstruction(
            compact_source=compact_source,
            compact_components=_compact_components(layer),
            channel=(
                "raw combined content"
                if depth == 0
                else f"combined content percent-decode layer {depth}"
            ),
        )

    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    source_codepoints = sum(character.isalnum() for character in source_text)
    selected_codepoints = sum(
        character.isalnum()
        for start, end in merged
        for character in source_text[start:end]
    )
    if source_codepoints == 0:
        raise ValueError("profile import source must contain alphanumeric text")
    if selected_codepoints * 100 >= source_codepoints * _BROAD_DOCUMENT_PERCENT:
        raise ValueError("profile import spans collectively cover too much source text")
