"""Fail-closed schemas and content policy for profile import proposals.

The caller-managed raw source is used only for exact-span, minimization, and
bounded selected-context checks. It is not globally classified for sensitive
patterns and is never persisted by this workflow.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from urllib.parse import unquote_plus

from grounded_apply.domain import JsonValue


PROFILE_IMPORT_VALUE_SCHEMA_VERSION = 1
PROFILE_IMPORT_CONTENT_POLICY_VERSION = 2
PROFILE_IMPORT_RESTRICTED_TAXONOMY_VERSION = 1
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
_OWNERSHIP_LEVELS = frozenset(
    {"supported", "contributed", "co-led", "led", "owned"}
)

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


@dataclass(frozen=True, slots=True)
class _RestrictedContentCategory:
    """Declarative, auditable rules for one fail-closed content category."""

    identifier: str
    context_patterns: tuple[str, ...]
    label_patterns: tuple[str, ...]
    assignment_keys: tuple[str, ...]
    case_sensitive_context_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _RestrictedContentTaxonomy:
    version: int
    categories: tuple[_RestrictedContentCategory, ...]


_RESTRICTED_CONTENT_TAXONOMY = _RestrictedContentTaxonomy(
    version=PROFILE_IMPORT_RESTRICTED_TAXONOMY_VERSION,
    categories=(
        _RestrictedContentCategory(
            identifier="work_authorization_immigration",
            context_patterns=(
                r"\b(?:work|employment)[\s_-]+authori[sz](?:ation|ed)\b",
                r"\bauthori[sz]ed\s+to\s+work\b",
                r"\bable\s+to\s+work\s+(?:in|within|throughout)\s+(?:the\s+)?"
                r"(?:u\.?\s*s\.?|united\s+states|canada)\b",
                r"\b(?:immigration|visa)\s+status\b",
                r"\b(?:require[sd]?|need(?:ed|s)?|without|seek(?:ing|s)?)\s+"
                r"(?:employment\s+)?sponsor(?:ship)?\b",
                r"\b(?:h[\s-]?1b|l[\s-]?1|o[\s-]?1|f[\s-]?1|j[\s-]?1|visa)\s+"
                r"sponsor(?:ship)?\b",
                r"\b(?:green\s+card|permanent\s+resident)\b",
                r"\bcitizenship\s+status\b",
                r"\bnationality\s*(?::|=)\s*\S+",
                r"\bCanadian\s+citizen\b",
                r"\bcitizen\s+of\s+[A-Za-z][A-Za-z .'-]{1,40}\b",
                r"\b(?:u\.?\s*s\.?|united\s+states)\s+citizen\b",
                r"\b(?:legal\s+(?:right|permission)|eligible)\s+to\s+work\b",
                r"\blegally\s+(?:permitted|eligible|authori[sz]ed)\s+to\s+work\b",
                r"\b(?:employment|work)\s+eligib(?:ility|le)\b",
                r"\bwork\s+permit\b",
                r"\b(?:currently\s+)?on\s+(?:OPT|CPT)\b",
                r"\bEAD(?:\s+(?:card|status|holder))?\b",
                r"\b(?:h[\s-]?1b|l[\s-]?1|o[\s-]?1|f[\s-]?1|j[\s-]?1|"
                r"tn|e[\s-]?3)\s+"
                r"(?:visa\s+)?(?:holder|status)\b",
                r"\b(?:hold(?:s|ing)?|on)\s+(?:an?\s+)?"
                r"(?:h[\s-]?1b|l[\s-]?1|o[\s-]?1|f[\s-]?1|j[\s-]?1|"
                r"tn|e[\s-]?3)\b",
                r"\b(?:opt|cpt)\s+(?:eligible|holder|status)\b",
                r"\bsponsor(?:ship)?\s+(?:will\s+be|is|would\s+be)\s+required\b",
                r"\b(?:no\s+)?sponsor(?:ship)?\s+(?:is\s+)?(?:not\s+)?required\b",
            ),
            label_patterns=(
                r"work[\s_-]*authori[sz]ation",
                r"(?:employment|work)[\s_-]*eligibility",
                r"sponsor(?:ship)?",
                r"visa[\s_-]*status",
                r"immigration[\s_-]*status",
                r"citizenship[\s_-]*status",
                r"nationality",
                r"work[\s_-]*permit",
                r"ead(?:[\s_-]*(?:card|status))?",
                r"are\s+you\s+(?:legally\s+)?authori[sz]ed\s+to\s+work"
                r"(?:\s+(?:in|within)\s+[^\r\n?]{1,64})?\??",
                r"will\s+you\s+(?:now\s+or\s+)?(?:at\s+any\s+time\s+)?"
                r"in\s+the\s+future\s+require(?:\s+(?:employment|visa))?\s+"
                r"sponsor(?:ship)?\??",
                r"do\s+you\s+now\s+or\s+will\s+you\s+in\s+the\s+future\s+"
                r"require(?:\s+(?:employment|visa))?\s+sponsor(?:ship)?\??",
            ),
            assignment_keys=(
                "workauthorization",
                "workauthorisation",
                "employmentauthorization",
                "employmentauthorisation",
                "workeligibility",
                "employmenteligibility",
                "sponsor",
                "sponsorship",
                "visa",
                "visastatus",
                "immigrationstatus",
                "ead",
                "citizenship",
                "citizenshipstatus",
                "nationality",
            ),
        ),
        _RestrictedContentCategory(
            identifier="security_clearance",
            context_patterns=(
                r"\b(?:active|current|inactive|expired|interim)\s+"
                r"(?:(?:confidential|secret|top[\s_-]*secret|ts\s*/\s*sci|sci)\s+"
                r"(?:security\s+)?clearance|security\s+clearance)\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|that\b|which\b|requiring\b))",
                r"\b(?:hold(?:s|ing)?|held|maintain(?:s|ed|ing)?|"
                r"possess(?:es|ed|ing)?|granted)\s+(?:an?\s+)?"
                r"(?:(?:active|current)\s+)?"
                r"(?:(?:confidential|secret|top[\s_-]*secret|ts\s*/\s*sci|sci)\s+"
                r"(?:security\s+)?clearance|security\s+clearance)\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|that\b|which\b|requiring\b))",
                r"\b(?:eligible\s+for|able\s+to\s+obtain)\s+(?:an?\s+)?"
                r"(?:(?:confidential|secret|top[\s_-]*secret|ts\s*/\s*sci|sci)\s+"
                r"(?:security\s+)?clearance|security\s+clearance)\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|that\b|which\b|requiring\b))",
                r"\bsecurity\s+clearance\s+(?:status|level)\s*"
                r"(?:is|:|=)\s*\S+",
                r"\bsecurity\s+clearance\s*(?::|=)\s*\S+",
                r"\bpublic[\s_-]+trust[\s_-]+(?:clearance|status)\s*"
                r"(?::|=)\s*\S+",
                r"\b(?:active|current|inactive|expired)\s+public[\s_-]+trust"
                r"(?:[\s_-]+clearance)?\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|that\b|which\b|requiring\b))",
                r"\bpublic[\s_-]+trust(?:[\s_-]+security)?[\s_-]+clearance\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|that\b|which\b|requiring\b))",
                r"\b(?:confidential|secret|ts\s*/\s*sci|sci)\s+"
                r"(?:security\s+)?clearance\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|that\b|which\b|requiring\b))",
                r"\b(?:ts\s*/\s*sci|top[\s_-]*secret)\s+(?:security\s+)?clearance\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|that\b|which\b|requiring\b))",
                r"\b(?:(?:hold(?:s|ing)?|held|maintain(?:s|ed|ing)?|"
                r"possess(?:es|ed|ing)?)\s+(?:an?\s+)?)?"
                r"(?:(?:active|current|interim)\s+)?"
                r"(?:(?:confidential|secret|top[\s_-]*secret|ts\s*/\s*sci|sci)\s+"
                r"(?:security\s+)?clearance|security\s+clearance)\s+"
                r"(?:(?:valid|effective)\s+)?(?:through|until)\s+\S+",
                r"\b(?:active|current|interim)\s+"
                r"(?:(?:confidential|secret|top[\s_-]*secret|ts\s*/\s*sci|sci)\s+"
                r"(?:security\s+)?clearance|security\s+clearance)\s+"
                r"(?:granted|issued|adjudicated)\s+(?:in|on)\s+\S+",
                r"\b(?:eligible\s+for|able\s+to\s+obtain)\s+(?:an?\s+)?"
                r"(?:security|confidential|secret|top[\s_-]*secret|"
                r"ts\s*/\s*sci|sci)\s+clearance\s+(?:upon|if)\s+\S+",
                r"\bts\s*/\s*sci(?:\s+(?:with\s+)?"
                r"(?:(?:full[\s_-]+scope|counterintelligence|ci)\s+)?polygraph)?\b",
                r"\b(?:hold(?:s|ing)?|held|maintain(?:s|ed|ing)?|"
                r"possess(?:es|ed|ing)?)\s+(?:an?\s+)?"
                r"(?:(?:active|current|interim)\s+)?"
                r"(?:confidential|secret|top[\s_-]*secret|ts\s*/\s*sci|sci)\s+"
                r"(?:security\s+)?clearance\s+with\s+"
                r"(?:(?:full[\s_-]+scope|counterintelligence|ci)\s+)?polygraph\b",
                r"\b(?:active|current|interim)\s+"
                r"(?:(?:confidential|secret|top[\s_-]*secret|ts\s*/\s*sci|sci)\s+"
                r"(?:security\s+)?clearance|security\s+clearance)\s*"
                r"\([^\r\n)]{1,64}\)",
                r"\b(?:my|candidate(?:'s)?|applicant(?:'s)?)\s+"
                r"security\s+clearance\s+(?:is|:|=)\s*\S+",
            ),
            label_patterns=(
                r"security[\s_-]*clearance(?:[\s_-]*(?:status|level))?",
                r"clearance[\s_-]*(?:status|level)",
                r"public[\s_-]*trust(?:[\s_-]*(?:status|clearance))?",
                r"(?:do\s+you|does\s+the\s+(?:candidate|applicant))\s+"
                r"(?:hold|have|possess)\s+(?:an?\s+)?(?:security\s+)?clearance\??",
                r"what\s+(?:level|type)\s+of\s+(?:security\s+)?clearance\s+"
                r"do\s+you\s+(?:currently\s+)?hold\??",
            ),
            assignment_keys=(
                "securityclearance",
                "securityclearancestatus",
                "securityclearancelevel",
                "publictrustclearance",
                "publictruststatus",
            ),
        ),
        _RestrictedContentCategory(
            identifier="veteran_status",
            context_patterns=(
                r"\bveteran[\s_-]+status\s*(?::|=)\s*\S+",
                r"\bveteran\s*(?::|=)\s*\S+",
                r"\b(?:my|candidate(?:'s)?|applicant(?:'s)?)\s+"
                r"veteran[\s_-]+status\s+is\s+\S+",
                r"\b(?:protected|disabled|recently[\s_-]+separated|"
                r"active[\s_-]+duty[\s_-]+wartime[\s_-]+or[\s_-]+campaign[\s_-]+badge|"
                r"armed[\s_-]+forces[\s_-]+service[\s_-]+medal)\s+veteran\b",
                r"\b(?:i\s+am|candidate\s+is|applicant\s+is|"
                r"self[\s_-]*identif(?:y|ies|ied)\s+as)\s+(?:not\s+)?"
                r"(?:a\s+)?(?:protected\s+|military\s+)?veteran\b",
                r"\b(?:served|service)\s+in\s+(?:the\s+)?"
                r"(?:u\.?\s*s\.?\s+)?armed\s+forces\b",
                r"\b(?:military|armed[\s_-]+forces)[\s_-]+service[\s_-]+status\b",
                r"\b(?:u\.?\s*s\.?\s+)?(?:army|navy|air[\s_-]+force|"
                r"marine[\s_-]+corps|coast[\s_-]+guard|space[\s_-]+force|military)\s+"
                r"veteran\b(?=\s*(?:$|[.,;:!?)]|and\b|but\b|who\b|with\b))",
            ),
            label_patterns=(
                r"(?:protected[\s_-]+)?veteran[\s_-]+status",
                r"military[\s_-]+service[\s_-]+status",
                r"veteran",
                r"(?:are|do)\s+you\s+(?:identify\s+as\s+)?(?:an?\s+)?"
                r"(?:protected\s+)?veteran\??",
            ),
            assignment_keys=(
                "veteranstatus",
                "veteran",
                "protectedveteran",
                "protectedveteranstatus",
                "militaryservicestatus",
            ),
        ),
        _RestrictedContentCategory(
            identifier="disability_status",
            context_patterns=(
                r"\bdisability[\s_-]+status\s*(?::|=)\s*\S+",
                r"\bdisability\s*(?::|=)\s*\S+",
                r"\b(?:my|candidate(?:'s)?|applicant(?:'s)?)\s+"
                r"disability[\s_-]+status\s+is\s+\S+",
                r"\b(?:i\s+)?(?:have|has|report(?:s|ed)?|declare(?:s|d)?|"
                r"disclose(?:s|d)?)\s+(?:no\s+|an?\s+)?disabilit(?:y|ies)"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|that\b|which\b|requiring\b|so\b))",
                r"\b(?:i\s+am|candidate\s+is|applicant\s+is|"
                r"self[\s_-]*identif(?:y|ies|ied)\s+as)\s+(?:not\s+)?"
                r"(?:an?\s+)?(?:individual|person)\s+with\s+(?:a\s+)?"
                r"disabilit(?:y|ies)\b",
                r"\b(?:disabled|non[\s_-]*disabled)\s+"
                r"(?:candidate|applicant|individual|person)\b",
                r"\bi\s+am\s+(?:not\s+)?disabled\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|with\b))",
                r"\breasonable[\s_-]+accommodation\s+"
                r"(?:requested|required|needed|not[\s_-]+required|status)\b",
                r"\b(?:require|need|request)\s+(?:a\s+)?reasonable[\s_-]+accommodation\b",
            ),
            label_patterns=(
                r"disability[\s_-]+status",
                r"disability",
                r"reasonable[\s_-]+accommodation",
                r"accommodation[\s_-]+request",
                r"do\s+you\s+have\s+(?:an?\s+|any\s+)?disabilit(?:y|ies)"
                r"(?:\s+or\s+have\s+you\s+ever\s+had\s+one)?\??",
                r"are\s+you\s+(?:an?\s+)?(?:individual|person)\s+with\s+"
                r"(?:a\s+)?disabilit(?:y|ies)\??",
            ),
            assignment_keys=(
                "disability",
                "disabilitystatus",
                "disabledstatus",
                "reasonableaccommodation",
                "accommodationrequest",
            ),
        ),
        _RestrictedContentCategory(
            identifier="criminal_legal_attestation",
            context_patterns=(
                r"\bcriminal[\s_-]+(?:history|record|charges?)\s*(?::|=)\s*\S+",
                r"\b(?:have|has|reports?|discloses?|without|no)\s+(?:an?\s+|any\s+)?"
                r"criminal[\s_-]+(?:history|record|convictions?|charges?)\b",
                r"\b(?:i\s+)?(?:have|has)\s+(?:never\s+|not\s+)?been\s+convicted\b"
                r"(?=\s*(?:$|[.,;:!?)]|of\b|for\b|and\b|but\b))",
                r"\b(?:i\s+)?(?:was|were)\s+(?:never\s+|not\s+)?convicted\b"
                r"(?=\s*(?:$|[.,;:!?)]|of\b|for\b|and\b|but\b))",
                r"\b(?:have|has|with|without|no)\s+(?:an?\s+|any\s+)?"
                r"(?:felony|misdemeanor)\s+(?:convictions?|record|charges?)\b",
                r"\b(?:pending|no)\s+criminal\s+charges?\b",
                r"\blegal[\s_-]+attestation\s*(?::|=)\s*\S+",
                r"\b(?:provide|provided|accept|accepted|complete|completed|agree|agreed)\s+"
                r"(?:an?|the|this)\s+legal[\s_-]+attestation\b",
                r"\bunder\s+penalty\s+of\s+perjury\b",
                r"\b(?:i\s+)?(?:certify|attest|declare)\s+(?:that\s+)?"
                r"(?:(?:the|this)\s+)?"
                r"(?:application|foregoing|information|statements?|answers?)\s+"
                r"(?:is|are)\s+(?:true|accurate|complete)\b",
                r"\b(?:my|candidate(?:'s)?|applicant(?:'s)?)\s+"
                r"electronic[\s_-]+signature\b",
                r"\b(?:provided|entered|submitted|affixed)\s+"
                r"(?:my|an?|the|their|his|her)\s+electronic[\s_-]+signature\b",
                r"\belectronic[\s_-]+signature\s*(?::|=)\s*\S+",
                r"\belectronically\s+sign(?:ed|ing)\s+(?:this|the)\s+"
                r"(?:application|attestation|form)\b",
            ),
            label_patterns=(
                r"criminal[\s_-]+(?:history|record|conviction[\s_-]+history)",
                r"legal[\s_-]+attestation",
                r"electronic[\s_-]+signature",
                r"have\s+you(?:\s+ever)?\s+been\s+convicted"
                r"(?:\s+of\s+(?:an?\s+|any\s+)?(?:crime|felony|misdemeanor))?\??",
                r"have\s+you(?:\s+ever)?\s+been\s+(?:arrested\s+or\s+)?"
                r"charged\s+with\s+(?:an?\s+|any\s+)?crime\??",
                r"(?:type|enter)\s+your\s+(?:full\s+)?legal\s+name\s+as\s+"
                r"your\s+electronic[\s_-]+signature\.?",
            ),
            assignment_keys=(
                "criminalhistory",
                "criminalrecord",
                "convictionhistory",
                "felonyconviction",
                "legalattestation",
                "electronicsignature",
            ),
        ),
        _RestrictedContentCategory(
            identifier="conflict_of_interest",
            context_patterns=(
                r"\bconflicts?\s+of\s+interest\s*(?::|=)\s*\S+",
                r"\b(?:have|has|declare(?:s|d)?|disclose(?:s|d)?|report(?:s|ed)?|"
                r"without|no|free\s+from|do(?:es)?\s+not\s+have)\s+"
                r"(?:an?\s+|any\s+)?"
                r"conflicts?\s+of\s+interest\b",
                r"\bconflicts?\s+of\s+interest\s+(?:status|disclosure)\b",
                r"\b(?:have|has|declare(?:s|d)?|disclose(?:s|d)?|report(?:s|ed)?|"
                r"without|free\s+from|do(?:es)?\s+not\s+have)\s+"
                r"(?:an?\s+|any\s+|no\s+)?outside[\s_-]+employment[\s_-]+conflict\b",
            ),
            label_patterns=(
                r"conflicts?[\s_-]+of[\s_-]+interest",
                r"outside[\s_-]+employment[\s_-]+conflict",
                r"do\s+you\s+have\s+(?:an?\s+|any\s+)?"
                r"(?:(?:actual|potential)\s+or\s+(?:actual|potential)\s+)?"
                r"conflicts?[\s_-]+of[\s_-]+interest\??",
            ),
            assignment_keys=(
                "conflictofinterest",
                "conflictsofinterest",
                "conflictofintereststatus",
                "outsideemploymentconflict",
            ),
        ),
        _RestrictedContentCategory(
            identifier="demographic_self_identification",
            context_patterns=(
                r"\b(?:race(?:\s+or\s+ethnicity)?|ethnicity|gender[\s_-]+identity|"
                r"sexual[\s_-]+orientation|marital[\s_-]+status|religion|"
                r"religious[\s_-]+affiliation)\s*(?::|=)\s*\S+",
                r"\b(?:my|candidate(?:'s)?|applicant(?:'s)?)\s+"
                r"(?:race|ethnicity|gender[\s_-]+identity|sexual[\s_-]+orientation|"
                r"marital[\s_-]+status|religion|religious[\s_-]+affiliation)\s+"
                r"(?:is|are)\s+\S+",
                r"\b(?:i\s+am|candidate\s+is|applicant\s+is)\s+"
                r"(?:asian|black|white|hispanic|latino|latina|latinx|"
                r"native[\s_-]+american|pacific[\s_-]+islander|"
                r"middle[\s_-]+eastern|male|female|non[\s_-]*binary|transgender)\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|who\b|with\b))",
                r"\b(?:my|candidate(?:'s)?|applicant(?:'s)?)\s+pronouns?\s+"
                r"(?:are|is)\s+\S+",
                r"\bi\s+use\s+\S+(?:\s*/\s*\S+)?\s+pronouns?\b",
                r"\b(?:i\s+am|candidate\s+is|applicant\s+is)\s+\d{1,3}\s+years?\s+old\b",
                r"\b(?:my|candidate(?:'s)?|applicant(?:'s)?)\s+age\s+(?:is|:|=)\s*"
                r"\d{1,3}\b",
                r"\b(?:date\s+of\s+birth|birth\s+date|dob)\s*(?::|=)\s*\S+",
                r"\b(?:my|candidate(?:'s)?|applicant(?:'s)?)\s+"
                r"(?:date\s+of\s+birth|birth\s+date)\s+is\s+\S+",
                r"\b(?:single|married|divorced|widowed)\s+(?:candidate|applicant)\b",
                r"\bi\s+am\s+(?:single|married|divorced|widowed)\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|with\b))",
                r"\bi\s+am\s+(?:buddhist|christian|hindu|jewish|muslim|sikh)\b"
                r"(?=\s*(?:$|[.,;:!?)]|and\b|but\b|who\b))",
            ),
            label_patterns=(
                r"race(?:\s*(?:/|or)\s*ethnicity)?",
                r"ethnicity",
                r"gender[\s_-]+identity",
                r"gender",
                r"sex",
                r"sexual[\s_-]+orientation",
                r"pronouns?",
                r"age",
                r"date[\s_-]+of[\s_-]+birth",
                r"birth[\s_-]+date",
                r"dob",
                r"marital[\s_-]+status",
                r"religion",
                r"religious[\s_-]+affiliation",
                r"what\s+is\s+your\s+(?:age|gender|gender[\s_-]+identity|"
                r"race(?:\s+(?:/|or)\s+ethnicity)?|ethnicity|religion)\??",
                r"please\s+(?:select|choose)\s+your\s+(?:gender|race|ethnicity|"
                r"race\s*(?:/|or)\s*ethnicity|religion)\??",
                r"are\s+you\s+(?:hispanic|latino|latina|latinx)"
                r"(?:\s+or\s+(?:hispanic|latino|latina|latinx))?\??",
            ),
            assignment_keys=(
                "race",
                "ethnicity",
                "raceethnicity",
                "gender",
                "genderidentity",
                "sex",
                "sexualorientation",
                "pronoun",
                "pronouns",
                "age",
                "dateofbirth",
                "birthdate",
                "dob",
                "maritalstatus",
                "religion",
                "religiousaffiliation",
            ),
        ),
        _RestrictedContentCategory(
            identifier="government_identifier",
            context_patterns=(
                r"\bsocial[\s_-]+security(?:[\s_-]+number)?\b",
                r"\bs[\W_]*s[\W_]*n\b",
                r"\bpassport[\s_-]+(?:number|no\.?|id)\b",
                r"\bpassport(?:[\s_-]+(?:number|no\.?|id))?\s*(?:#|:)?\s*"
                r"(?=[A-Z0-9-]{5,}\b)(?=[A-Z0-9-]*[0-9-])"
                r"[A-Z0-9][A-Z0-9-]{4,}\b",
                r"\bdriver'?s[\s_-]+licen[cs]e[\s_-]+(?:number|no\.?|id)\b",
                r"\bdriver'?s?[\s_-]+licen[cs]e(?:[\s_-]+(?:number|no\.?|id))?"
                r"\s*(?:#|:)?\s*(?=[A-Z0-9-]{5,}\b)(?=[A-Z0-9-]*[0-9-])"
                r"[A-Z0-9][A-Z0-9-]{4,}\b",
                r"\bDL\s*(?:#|:|=)\s*(?=[A-Z0-9-]{5,}\b)"
                r"(?=[A-Z0-9-]*[0-9-])[A-Z0-9][A-Z0-9-]{4,}\b",
                r"\b(?:national|government[\s_-]*issued|taxpayer)[\s_-]+id\b",
                r"\b(?:national|government|tax)[\s_-]+id\s*(?:#|:|=)\s*\S+",
                r"\b(?:itin|taxpayer[\s_-]+identification[\s_-]+number)\b",
                r"\bEIN\s*(?:#|:)?\s*\d{2}-\d{7}\b",
                r"(?<!\d)\d{3}[\s-]\d{2}[\s-]\d{4}(?!\d)",
            ),
            label_patterns=(
                r"social[\s_-]*security(?:[\s_-]*number)?",
                r"ssn",
                r"passport(?:[\s_-]*(?:number|no\.?|id))?",
                r"driver'?s?[\s_-]*licen[cs]e(?:[\s_-]*(?:number|no\.?|id))?",
                r"national[\s_-]*id",
                r"government[\s_-]*id",
                r"tax[\s_-]*id",
                r"itin",
                r"ein",
            ),
            assignment_keys=(
                "ssn",
                "socialsecurity",
                "socialsecuritynumber",
                "passport",
                "passportnumber",
                "passportno",
                "passportid",
                "driverlicense",
                "driverlicence",
                "driverlicensenumber",
                "driverlicencenumber",
                "driverlicenseno",
                "driverlicenceno",
                "driverlicenseid",
                "driverlicenceid",
                "driverslicense",
                "driverslicence",
                "driverslicensenumber",
                "driverslicencenumber",
                "driverslicenseno",
                "driverslicenceno",
                "driverslicenseid",
                "driverslicenceid",
                "nationalid",
                "governmentid",
                "governmentissuedid",
                "taxid",
                "taxpayerid",
                "taxpayeridentificationnumber",
                "itin",
                "ein",
            ),
        ),
        _RestrictedContentCategory(
            identifier="authentication_credential",
            context_patterns=(
                r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[\s_-]+){0,3}"
                r"(?:password|passwd|pwd|token|secret|credential(?:s)?|cookie|"
                r"session[\s_-]*id|api[\s_-]*key|private[\s_-]*key|"
                r"secret[\s_-]*access[\s_-]*key|access[\s_-]*key(?:[\s_-]*id)?)"
                r"\s*(?::|=)\s*\S{4,}",
                r"\bauthorization\s*:\s*(?:basic|bearer)\s+\S{8,}",
                r"\bcookie\s*:\s*\S{8,}",
                r"\b[A-Za-z][A-Za-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@",
                r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----",
                r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
                r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
                r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b",
                r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b",
            ),
            case_sensitive_context_patterns=(
                r"\bAKIA[0-9A-Z]{16}\b",
                r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\."
                r"[A-Za-z0-9_-]{12,}\b",
            ),
            label_patterns=(
                r"api[\s_+-]*(?:token|key)",
                r"access[\s_-]*key(?:[\s_-]*id)?",
                r"secret[\s_-]*access[\s_-]*key",
                r"password",
                r"passwd",
                r"pwd",
                r"token",
                r"secret",
                r"credential(?:s)?",
                r"cookie",
                r"session[\s_-]*(?:id|token)",
                r"private[\s_-]*key",
            ),
            assignment_keys=(
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
            ),
        ),
    ),
)

_REQUIRED_RESTRICTED_CATEGORY_IDS = frozenset(
    {
        "work_authorization_immigration",
        "security_clearance",
        "veteran_status",
        "disability_status",
        "criminal_legal_attestation",
        "conflict_of_interest",
        "demographic_self_identification",
        "government_identifier",
        "authentication_credential",
    }
)


def _validate_restricted_content_taxonomy(
    taxonomy: _RestrictedContentTaxonomy,
) -> None:
    if taxonomy.version != PROFILE_IMPORT_RESTRICTED_TAXONOMY_VERSION:
        raise RuntimeError("profile import restricted taxonomy version is inconsistent")
    if not taxonomy.categories:
        raise RuntimeError("profile import restricted taxonomy must not be empty")
    identifiers: set[str] = set()
    assignment_keys: set[str] = set()
    for category in taxonomy.categories:
        if (
            re.fullmatch(r"[a-z][a-z0-9_]*", category.identifier) is None
            or category.identifier in identifiers
        ):
            raise RuntimeError("profile import restricted category IDs must be unique")
        identifiers.add(category.identifier)
        if not (
            category.context_patterns
            or category.case_sensitive_context_patterns
            or category.label_patterns
            or category.assignment_keys
        ):
            raise RuntimeError("profile import restricted categories must contain rules")
        for pattern in (
            *category.context_patterns,
            *category.case_sensitive_context_patterns,
            *category.label_patterns,
        ):
            try:
                compiled = re.compile(pattern)
            except re.error as error:
                raise RuntimeError(
                    "profile import restricted taxonomy contains an invalid pattern"
                ) from error
            if compiled.search("") is not None:
                raise RuntimeError(
                    "profile import restricted taxonomy patterns must not match empty text"
                )
        for key in category.assignment_keys:
            if (
                not key
                or key != key.casefold()
                or not key.isalnum()
                or key in assignment_keys
            ):
                raise RuntimeError(
                    "profile import restricted assignment keys must be unique normalized text"
                )
            assignment_keys.add(key)
    if identifiers != _REQUIRED_RESTRICTED_CATEGORY_IDS:
        raise RuntimeError("profile import restricted taxonomy categories are incomplete")


def _restricted_taxonomy_sha256(taxonomy: _RestrictedContentTaxonomy) -> str:
    payload = {
        "version": taxonomy.version,
        "categories": [
            {
                "identifier": category.identifier,
                "context_patterns": sorted(category.context_patterns),
                "case_sensitive_context_patterns": sorted(
                    category.case_sensitive_context_patterns
                ),
                "label_patterns": sorted(category.label_patterns),
                "assignment_keys": sorted(category.assignment_keys),
            }
            for category in sorted(
                taxonomy.categories,
                key=lambda item: item.identifier,
            )
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


_validate_restricted_content_taxonomy(_RESTRICTED_CONTENT_TAXONOMY)
PROFILE_IMPORT_RESTRICTED_TAXONOMY_SHA256 = _restricted_taxonomy_sha256(
    _RESTRICTED_CONTENT_TAXONOMY
)
_RESTRICTED_ASSIGNMENT_KEYS = tuple(
    key
    for category in _RESTRICTED_CONTENT_TAXONOMY.categories
    for key in category.assignment_keys
)
_RESTRICTED_ASSIGNMENT_KEY_SET = frozenset(_RESTRICTED_ASSIGNMENT_KEYS)
_RESTRICTED_CONTEXT_PATTERN = re.compile(
    "|".join(
        compiled_pattern
        for category in _RESTRICTED_CONTENT_TAXONOMY.categories
        for compiled_pattern in (
            *(f"(?i:{pattern})" for pattern in category.context_patterns),
            *(
                f"(?:{pattern})"
                for pattern in category.case_sensitive_context_patterns
            ),
        )
    )
)
_RESTRICTED_LABEL_ONLY_PATTERN = re.compile(
    r"\s*(?:"
    + "|".join(
        f"(?:{pattern})"
        for category in _RESTRICTED_CONTENT_TAXONOMY.categories
        for pattern in category.label_patterns
    )
    + r")(?:\s*(?:[*†]|\((?:optional|voluntary|required|select one|choose one)\))){0,2}"
    + r"\s*:?\s*",
    re.IGNORECASE,
)


def registered_profile_import_claim_types() -> frozenset[str]:
    """Return every claim type with a versioned import value schema."""

    return frozenset(_PROFILE_IMPORT_VALUE_SCHEMAS)


def registered_profile_import_restricted_categories() -> frozenset[str]:
    """Return stable category IDs from the active restricted-text taxonomy."""

    return frozenset(
        category.identifier for category in _RESTRICTED_CONTENT_TAXONOMY.categories
    )


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


def _contains_restricted_content(value: str) -> bool:
    return any(
        _RESTRICTED_CONTEXT_PATTERN.search(normalized) is not None
        for normalized in (
            _normalized_for_detection(value),
            _normalized_with_control_separators(value),
        )
    )


def _matches_restricted_label(value: str) -> bool:
    return any(
        _RESTRICTED_LABEL_ONLY_PATTERN.fullmatch(normalized) is not None
        for variant in _percent_decoded_variants(value)
        for normalized in (
            _normalized_for_detection(variant),
            _normalized_with_control_separators(variant),
        )
    )


def _compact_assignment_fragment(value: str) -> str:
    return "".join(
        character
        for character in _normalized_for_detection(value).casefold()
        if character.isalnum()
    )


def _assignment_left_fragments(value: str) -> Iterator[str]:
    maximum_key_length = max(map(len, _RESTRICTED_ASSIGNMENT_KEYS))
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


def _contains_exact_restricted_assignment(value: str) -> bool:
    return any(
        fragment in _RESTRICTED_ASSIGNMENT_KEY_SET
        for fragment in _assignment_left_fragments(value)
    )


def _contains_fragmented_restricted_assignment(components: Sequence[str]) -> bool:
    if len(components) < 2 or not any(
        "=" in component or ":" in component or "#" in component
        for component in components
    ):
        return False
    maximum_key_length = max(map(len, _RESTRICTED_ASSIGNMENT_KEYS))
    compact_components = tuple(_compact_assignment_fragment(item) for item in components)
    suffix_owners: dict[str, set[int]] = {}
    for owner, component in enumerate(compact_components):
        for length in range(1, min(len(component), maximum_key_length) + 1):
            suffix_owners.setdefault(component[-length:], set()).add(owner)
    possible_splits: dict[str, set[str]] = {}
    for prefix, assignment_fragment in (
        (key[:split], key[split:])
        for key in _RESTRICTED_ASSIGNMENT_KEYS
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


def _reject_restricted_components(components: tuple[str, ...]) -> None:
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
        if _contains_restricted_content(
            item
        ) or _contains_exact_restricted_assignment(item):
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
                    if _contains_restricted_content(aggregate):
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
                if _contains_restricted_content(aggregate):
                    raise ValueError(
                        "proposal content contains fragmented sensitive or credential-like value"
                    )
    if _contains_fragmented_restricted_assignment(semantic_components):
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
    evidence_prefix: str,
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
    _reject_restricted_components(
        (
            *tuple(_iter_text_components(value, include_keys=False)),
            canonical_text,
            evidence_text,
        )
    )
    _reject_restricted_components((evidence_context,))
    if evidence_prefix.strip() and _matches_restricted_label(evidence_prefix.strip()):
        raise ValueError(
            "proposal content contains a disallowed sensitive or credential-like value"
        )
    if (
        preceding_line is not None
        and _matches_restricted_label(preceding_line)
    ):
        raise ValueError(
            "proposal content contains a disallowed sensitive or credential-like value"
        )


def validate_profile_import_metadata(values: tuple[str | None, ...]) -> None:
    """Reject sensitive or credential-like text in persisted import metadata."""

    components = tuple(value for value in values if value is not None)
    _reject_restricted_components(components)


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
    if _contains_fragmented_restricted_assignment(
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
