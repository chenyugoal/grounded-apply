# ADR 0012: Complete profile inventory and progressive onboarding

- **Status:** Accepted; implementation/verification status is in ROADMAP.md and SESSION_HANDOFF.md
- **Date:** 2026-09-21

The original pilot required UTF-8 text and rejected imports retaining 80% or more
of a source. That percentage was an implementation heuristic for minimization,
not a truth requirement. It prevented complete CV inventories and short factual
statements, and encouraged irrelevant omissions. The user's explicit retention
choice is a better boundary than a percentage of document length.

## Decision

Account for every nonblank extracted source line. Show supported proposals,
recognized headings, unclassified lines, and blocked locations separately.
Do not choose a silent starter subset based on target roles. Review may proceed
in batches, but the full inventory and outstanding lines must remain visible.
Line accounting is not a guarantee that every semantic fact was extracted.

Provide an explicit complete-facts content policy (version 3). It permits all
selected career facts without content-channel source-coverage rejection. Existing
version-2 imports retain their recorded policy and replay identities. Both
policies keep bounded typed claims, exact evidence spans, sensitive/credential
checks including selected context and fragmented assignments, metadata screening,
private storage, and separate per-fact approval. No raw source file is stored.
Retained facts start pending; full retention never means automatic verification
or use of every fact in every application. Unsupported/sensitive data remains a
visible gap rather than being silently inferred or stored.

Accept local document formats through an adapter, with explicit extraction
warnings and original/extracted hashes. Never compile supplied LaTeX or follow
its inputs, links, or commands. Static extraction cannot interpret arbitrary TeX;
unsupported constructs must be exposed for review. PDF text extraction is local
and optional; missing text, unsupported encryption, and parsing failures need an
actionable result. Source spans refer to the extracted text, not PDF byte offsets.
Original-document metadata shown during extraction is not durable original-file
provenance until a registered storage contract explicitly supports it.

Offer an optional interview from basic contact/education through responsibilities,
research, projects, skills, publications, achievements, preferences and evidence.
Ask a few questions at a time, permit skipping or stopping, and explain why a
question helps. Never infer answers. Retention and approval remain separate from
answering a question. The first catalogue/cursor increment does not claim durable
interview progress or profile-completeness measurement; approved profile facts
remain the reusable source of truth.

## Retained statement origin

Exact answers chosen for retention must keep `user_statement` origin. Routing
them through a resume-only importer incorrectly records `imported_resume`.
Add explicit `profile import --source-kind user-statement` while preserving the
default resume path and its existing identities. This is a provenance correction,
not a new source of authority: user statements still require exact evidence,
content validation, pending review and separate per-fact approval.

Use the existing schema-2 manifest with exact UTF-8 source spans and a source-hash
assertion. Accept text files or stdin only; at most one input may be stdin. Keep
`--source-format text` as the default and reject document interpretation on the
statement path. The application owns extractor identity and the source namespace;
the proposal author cannot assert origin or trust through manifest metadata.
Complete-facts retention remains an explicit `--retain-all-facts` choice.
The registered ingress is `grounded-apply.profile-user-statement.manifest@1`,
with `user-statement:sha256:<digest>` source references and
`profile-user-statement-source:sha256:<digest>` artifact IDs.

Preserve complete version combinations: resume request/source-identity/record-
digest versions remain 4/1/1; statement imports use 5/2/2. The new record digest
binds origin, and decisions inherit the validated import's digest version.
Manifest, result-manifest, record-ID, database schema and material versions do
not change. Existing profiles and generic user statements are not reclassified.
An origin change must not replay under a previous request's idempotency key.

Managed statement ownership must still be recognized when an association is
missing or altered; otherwise generic claim handling could bypass import audits.
Review, decided replay, resolution, retirement and current/historical material
reads must validate the expected claim/evidence origin and identity together.
Ordinary generic `user_statement` claims retain their existing behavior.

The conversational flow asks a few questions, shows exact proposed answers, and
retains only those the user chooses. Skip/stop does not store unretained answers.
Resume from retained facts and decisions without claiming a saved transcript,
interview position or completeness score. Sensitive questions remain outside
ordinary career-fact retention.

## Explicit research context

Add the scalar `research_description` in value vocabulary 3. Keep vocabulary 1's
existing value shapes and vocabulary
2's contact additions unchanged; each import chooses the smallest vocabulary it
needs. This vocabulary extension is separate from complete-facts content policy
3. Manifest, evidence, workflow identity and database schema versions do not
change merely to add this type.

Extractor 3 changes only the existing explicit Research, Research Experience and
Research Projects headings to use that type. Preserve versions 1 and 2 exactly,
including proposal order and indexes; keep the command default at version 2 for
compatibility. The research extension introduced explicit version 3 for new intake. Maintain
exact spans, text, contribution level, degree progress and publication status.
Do not classify by inferred research keywords. Publications retain their
existing type; teaching, volunteering, service and summary types are outside
this increment.

Existing facts are never relabeled automatically. A changed classification uses
the normal explicit import/review path and retains the original history.
Inventory accounting, retention choices, sensitive-content guards and per-fact
approval remain unchanged. A separate versioned presentation decision in
[ADR 0008](0008-versioned-resume-presentation.md#research-presentation-extension)
places selected approved research facts under Research without changing older
materials or unaffected preparation workflows.

## Neutral section boundaries

Add explicit extractor 4 while
preserving full extractor 1/2/3 outputs, source spans, proposal indexes and replay.
Keep default 2. The skill selects 4 for new intake and keeps
the displayed version when resuming earlier selections.

Recognize exactly four neutral labels with the existing casefold/trailing-colon
normalization: Research Interests, Academic Research, Selected Research and
Professional Memberships. They end inherited classification from the preceding
section, remain visible headings, and leave following nonlabel text unclassified
until a recognized section begins. Preserve explicit contact labels and version
3's three research mappings. Do not add research aliases or infer career facts,
membership achievements, preferences or eligibility. Users may explicitly
classify exact source spans through the normal import/review path.

This bounded set fixes observed section carry-over; it is not general heading
recognition. Other unsupported headings still need review. Keep classification
gaps distinct from document-adapter issues: readable PDF/TeX with
`document.incomplete: false` can still have unclassified content. Neither line
accounting nor `--allow-partial` establishes semantic completeness. No adapter,
extracted text/hash, stored vocabulary, content policy, manifest, schema or
material-version change is part of this extension.

## Explicit grouping of publication fragments

The read-only typed command supports explicitly chosen groups of consecutive
publication proposals. Current trimmed
extraction output omits the exact whitespace between spans; operators must not
guess those characters or call a Python adapter as the normal workflow. Require
the displayed extractor version and both source hashes for every format, re-read
through the existing document adapter, and require whitespace-only source gaps.
Keep the complete multiline evidence slice; normalize only whitespace in value
and canonical text, preserving all other characters and qualifiers.

Return the existing schema-2 manifest with all supported proposals, replacing the
selected fragments once and preserving other entries and their order. Expose the
index mapping and original inventory/gaps; do not infer grouping relationships,
classify unknown content or narrow the intake to one publication silently.
The helper has no runtime writes, retention or approval. Authorized retention
uses a private temporary manifest and the normal original-document import path.
No new extractor, claim type, stored schema or material version is introduced.

Repeat `--indexes` for
explicit disjoint groups from the same original extraction, keeping the singular
API and one-occurrence schema-1 response unchanged. A plural API accepts 1–500
groups and at most 1,000 members total; each group contains 2–1,000 consecutive
ascending exact-integer indexes. Reject overlaps and duplicate groups/members
before reading input. Never merge adjacent groups or infer relationships.
Allow either request order: schema-2 report `groups: [{indexes, manifest_index}]`
preserves that order and replaces the singular grouping fields, while the full
schema-2 manifest and index map remain in original source order. Read and extract
once, then validate the complete final manifest once. Every other proposal and
visible gap remains accounted for; retention and approval stay unchanged.

The original-document hash guards the invocation only. Existing generic import
does not enforce semantic equivalence between evidence and proposed wording;
the helper's stricter whitespace-only transformation does not change that
pending-review boundary or remove the need for explicit approval.

## Consequences and acceptance

Older source-minimization tests remain meaningful for policy 2. Policy 3 needs
whole-CV and short-source acceptance, unchanged sensitive/credential rejection,
no-write preview, pending review, approved resolution, replay and historical
compatibility tests. Extraction needs full line accounting and visible unknowns.
Document readers need malformed/unsupported/injection cases, bounded resources,
no TeX execution and no source-path/content leakage into diagnostic events.
Synthetic end-to-end source and fresh installed-package checks must exercise the
new entry points before they are described as available.

The research extension additionally requires unchanged legacy extraction,
import/replay identities and review tokens, smallest-needed vocabulary selection,
exact research qualifiers, and source/installed onboarding into a real PDF.
Research and Publications must remain separate while every factual unit retains
its approved claim mapping.

The neutral-boundary extension additionally needs exact prior-version output
and index preservation, transitions after multiple supported sections, known
section restart, visible heading/body accounting and exact spans. Test bullets,
ordinary prose, contact labels and sensitive guards without broadening the four
matches. Source and installed acceptance must exercise synthetic text, PDF and
TeX classification gaps separately from document-reading gaps, with unchanged
approval and replay behavior.

Publication grouping additionally requires exact contiguous evidence, unchanged
qualifiers and all other proposals, a correct index map, hash/version/index
refusals, wrong-type/nonconsecutive/non-whitespace-gap failures and existing size
limits. Source and installed acceptance must retain the full supported inventory
as pending, preserve replay and expose unknown/document gaps without runtime
writes from the helper or extra material PDF builds.
Multiple groups additionally require exact single-group report compatibility,
identical manifest/mapping under reversed group order, separate adjacent groups,
early overlap/duplicate refusals, whole-batch limits and one read/extraction/preview.

The statement-origin extension additionally needs exact-answer intake through
source and fresh installed CLIs, no-write preview/failure checks, pending
resolution refusal, explicit decisions, retirement and historical material use.
Verify unchanged resume replay, cross-origin key rejection, malformed version
combinations, altered origins/digests/extractors and missing or replaced audit
associations. Include a no-CV stop/resume flow without transcript storage or
implicit approval; diagnostics must remain fixed and content-free.
