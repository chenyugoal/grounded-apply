# ADR 0008: Versioned resume presentation

- **Status:** Accepted; verification recorded in SESSION_HANDOFF.md
- **Date:** 2026-09-19

The first renderer flattened imported role headers and did not recognize
LaTeX-source bullet markers. Version-2 builds use `approved_text_selection@2` with
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
existing immutable JSON structure/manifest and workflow fields record the
transformation version.

## Research presentation extension

Select `approved_text_selection@3` with `grounded-apply.latex-resume@3` when at
least one selected resume claim has type
`research_description`. Version 3 adds a fixed Research section while retaining
Publications separately. It uses the same exact approved wording, evidence
mappings, presentation choices and four-field heading rules. It does not infer
research context from prose or relabel earlier claims.

Select the version in `MaterialService.plan`; building uses that same plan and
pins its transformation for revalidation. Keep version 2 for unaffected resume
selections, including unselected research and research used only in answers.
Do not globally change the low-level structure default. Existing batch plan
fingerprints, pending child keys and approved-bundle reuse must stay valid.

Keep version-1 and version-2 renderer mappings and LaTeX unchanged. Historical
read, replay, export and audit dispatch by the stored version. The structure and
layout schemas remain version 1; no database migration occurs. New research
materials still require normal exact-bundle review and approval. Verification
must cover all three transformations, legacy material bytes, unaffected batch
recovery, and a real PDF with separate Research and Publications sections.

Codex still chooses relevant facts and keeps employer/title/date/bullet groups
together. It must inspect each rendered page, using the supplied original as a
reference when available. TeX layouts and local extraction do not establish
universal ATS compatibility. Arbitrary themes, automatic grouping, semantic
rewrites and resume optimization for private hiring algorithms are out of scope.
