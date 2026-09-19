# ADR 0008: Versioned resume presentation

- **Status:** Accepted; verification recorded in SESSION_HANDOFF.md
- **Date:** 2026-09-19

The first renderer flattened imported role headers and did not recognize
LaTeX-source bullet markers. New builds use `approved_text_selection@2` with
`grounded-apply.latex-resume@2`: 11-point Latin Modern sans-serif, centered name
and contacts, section rules, aligned two-row headers, restrained bold labels,
ragged-right wrapping, and space reserved for headings with following content.
The optional local TeX installation additionally needs `needspace`.

Presentation is separate from factual wording. The closed layout input maps
selected approved claim IDs to heading, bullet or paragraph, without new prose
or executable markup. Source markers are data only. Four-field headings replace
exact ` | ` separators with row/column layout; every field remains in the same
reading order. Other punctuation and factual text remain intact. This is a
deterministic presentation transformation, not a new candidate claim or semantic
derivation. Exact canonical text, ClaimPackets and output mappings are retained.
PDF extraction compares the complete expected visible text, including the
versioned separator transformation. Unsupported glyphs, overflow, extra text and
more than two pages fail closed; the renderer never truncates or shrinks to fit.

Presentation choices are included in workflow identity and structure/bundle
hashes. New builds need normal material review; approval cannot transfer from an
older version. Version-1 rendering is preserved byte-for-byte at the LaTeX source
level. Reading, export, approval, historical submission validation and idempotent
replay dispatch by the stored transformation. No schema migration is needed:
existing immutable JSON structure/manifest and workflow fields store version 2.

Codex still chooses relevant facts and keeps employer/title/date/bullet groups
together. It must inspect each rendered page, using the supplied original as a
reference when available. TeX layouts and local extraction do not establish
universal ATS compatibility. Arbitrary themes, automatic grouping, semantic
rewrites and resume optimization for private hiring algorithms are out of scope.
