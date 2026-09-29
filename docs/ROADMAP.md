# Roadmap

This roadmap defines delivery scope and acceptance gates. It is not the live
session log; use [`SESSION_HANDOFF.md`](SESSION_HANDOFF.md) for the current
working-tree checkpoint and next exact task.

**September 29, 2026: release wrap-up.** Broader feature development is paused.
The public-facing scope is an experimental, supervised application-preparation
tool. The sections below preserve implemented work and unfinished ideas; “next”
and “planned” describe design order, not scheduled commitments. Active-user-time
savings, independent first-use acceptance, and the full hosted platform matrix
remain unverified. No browser filling or automatic submission is implemented.

## Status rules

- **Implemented** means code or documentation exists and its documented
  verification has passed.
- **In progress** means work has started but is incomplete, unverified, or not
  integrated.
- **Planned** means design intent only.
- **Blocked** names a concrete dependency or unresolved decision.

As of September 21, 2026, **the bounded local application pilot is implemented**
(ADR 0006); broader platform and semantic features remain in progress. The design basis,
resumability contract, executable scaffold, deterministic truth policies, and
initial SQLite persistence slice are implemented and verified. Safe structured
text-import proposals, registered schema-v1 claim values, a versioned
nine-category restricted-text taxonomy for deterministic high-confidence
screening of persisted content and metadata, legacy source-minimization guards
and explicit complete-fact retention, a
stable-descriptor file/stdin CLI boundary, application-owned digest provenance,
a registered manifest ingress, replay-bound workflow records,
provenance-validating read-only review, durable record-digest associations,
stale-safe review tokens, a storage-free CLI decision syntax preview, confirmed
atomic audited approve/reject decisions with minimized output and idempotent
recovery, typed-CLI database/sidecar link and orphan guards, read-only
persistent-WAL refusal, resolution-time imported-record revalidation, and the
first synthetic profile fixture, adapter-owned safe SQLite opens, genuine
hot-journal recovery tests, fixed-schema opt-in diagnostic events, and an isolated
installed-package gate, and optional authenticated profile backup/restore with
an isolated installed-extra round trip are implemented. Local text/PDF/static-LaTeX
extraction, complete-inventory and selected onboarding, explicit name/contact vocabulary, audited retirement and
approved replacement, support export, and whole-portable-home deletion now extend
that foundation. The local pilot covers job capture, evidence retrieval, verified
PDFs/answers, explicit material approval, and manual application history. Broader
semantic contradiction handling, novel-obfuscation classification, registered
derivations, automatic retention, and Phases 2–5 remain planned or in progress.

## Delivered milestone — Codex-guided application preparation

### September 21 usability follow-up

**Status: Implemented and locally verified.** The 1296-test full suite, 74 focused
onboarding tests, required PDF/encryption gates, source lifecycle/search/daily
pilots, near-capacity restoration and fresh installed-package pilots pass. An
independent first-user test retained all seven fictional PDF facts pending,
without a manual text export or implicit approval. The current window
prioritizes new-user feedback over further storage-conversion preparation.
The implementation adds text/PDF/static-LaTeX intake, complete line inventory,
explicit full-fact retention without the legacy 80% cap, a progressive optional
question catalogue, board-link source setup, and a concise README with a detailed
reference. See [profile setup](PROFILE_SETUP.md),
[ADR 0012](adr/0012-complete-profile-onboarding.md), and the live checkpoint for
exact acceptance evidence. Semantic completeness, OCR/DOCX, custom TeX expansion,
durable interview state, persisted original-document provenance and platform-wide
job aggregation remain unfinished. Final review/approval/submission boundaries
are unchanged.

The configured-board Workable extension is also implemented and locally
verified: 1326 combined regression tests, 84 focused discovery tests, two real
PDFs in both source and fresh installed acceptance gates, and the existing
PDF/encryption/search/capacity gates pass. Offline setup accepts explicit company
boards; boardless job links remain manual gaps. The public feed uses fixed,
credential-free reads, strict identities, visible location fields, bounded
requests and explicit unknown totals. One live read of Workable's own board
passed without retaining posting prose. This extends configured-company support,
not market-wide aggregation; see [job discovery](JOB_DISCOVERY.md#workable-public-boards).

The complete fact queue can now be reviewed in bounded pages, with total,
earlier and later pending counts. Continuation survives approving or rejecting
the anchor; skipped facts remain pending and reappear on restart. Each page
validates the complete queue in one read snapshot, and no-option review remains
compatible. All 1353 regression tests, 247 focused profile tests, source and fresh
installed onboarding checks, and required PDF/encryption/capacity gates pass.
Per-fact approval remains explicit; pagination stores no cursor or answers.

Explicit Research, Research Experience and Research Projects headings now retain
research context through extractor 3 and `research_description`, with a separate
Research section in newly selected materials. Exact contribution, degree and
publication qualifiers remain grounded. Legacy extraction/material bytes and
unaffected or interrupted batch identities remain unchanged; old facts are not
automatically reclassified. All 1382 regression tests, 243 focused tests, source
and fresh installed research-PDF/approval/history checks, PDF/encryption gates
and sequential capacity probes pass.

Retained interview answers now have explicit user-statement provenance through
text/stdin-only `profile import --source-kind user-statement`. Exact wording,
separate retention and per-fact approval, bounded review, replay, retirement and
historical material audits are preserved. Default resume imports and their
recorded identities remain unchanged. All 1414 regression tests, 327 focused
tests, eleven source gates, fresh installed intake/material flows and sequential
capacity probes pass. This adds no saved interview transcript or progress state.

Discovery can now begin from explicit role/location terms without known company
URLs or profile setup. The pure `jobs plan-search` command supplies bounded literal
queries over supported hosts; Codex performs authorized browsing, validates
observed links and previews feeds. It shows location and coverage gaps rather
than treating the plan or search snippets as posting evidence. All 1433 tests,
58 focused checks, eleven source gates, fresh installed flows and capacity probes
pass. An independent fictional first-user pass reached partial useful previews
without a runtime, retaining rejected, manual and deferred lead accounting.
Live index coverage remains unverified.

Opt-in `jobs sources --keep-valid` now retains valid observed links alongside
position-only invalid/capped reports. Strict defaults and schema-1 manifests remain
unchanged; all-invalid input yields no manifest. All 1454 tests, 79 focused checks,
eleven source gates, fresh installed flows and capacity probes pass. A new offline
first-user trial reached useful previews with one setup call, preserving rejected,
manual, rate-limited, location and deferred-lead gaps.

A compact retained-profile inventory now supports interview resumption. The default
view contains counts only; bounded topic pages preserve exact wording, origin,
recorded approval and lifecycle state without loading unrelated evidence. Full
profile validation precedes every page; the view stores no answers, progress or
approvals and makes no completeness or usability assessment. All 1483 tests,
105 focused checks, eleven source gates, fresh installed workflows and sequential
capacity probes pass. An independent resumed-interview trial reused retained
research context, displayed six pending facts and asked two non-repeated follow-ups
without mutation or network access.

Explicit extractor 4 now keeps four observed unsupported headings from inheriting
the preceding section's fact type: Research Interests, Academic Research, Selected
Research and Professional Memberships. Their headings and unclassified body text
remain visible; earlier extraction versions and the default stay unchanged. All
1500 tests, 86 focused checks, eleven source gates, fresh installed workflows and
sequential capacity probes pass. An independent original-document trial confirms
the correction while preserving honest PDF/TeX gaps.

Explicit publication grouping now joins separately chosen, disjoint title/status
groups in one typed-CLI call, preserving exact multiline evidence and every
qualifier. Each work stays separate, all other supported proposals remain in
source order, and the report keeps the original inventory and visible gaps.
Single-group responses remain compatible; the helper neither stores nor approves
facts. All 1543 tests, 116 focused checks, eleven source gates, fresh installed
workflows and sequential capacity probes pass. A fresh operational confirmation
produced the same complete eight-fact proposal through one grouping call without
manual manifest composition; reversing group order preserved the final inventory.

Standalone discovery now applies explicit published-location filters before the
per-source selection limit. Literal alternatives and a separate missing-location
choice expose eligible unknowns, excluded records and deferred matches without
changing provider coverage, saved-search behavior or unfiltered responses. All
1572 regression tests, 90 focused checks, eleven source gates, fresh installed
workflows and sequential capacity probes pass. A fresh operational confirmation
reached a later preferred-location posting and preserved partial/manual coverage
gaps; this does not infer remote-work eligibility or geographic equivalence.

The complete-inventory operator recipe now explains how to preserve every
supported proposal while adding explicitly classified exact source rows through
the existing importer. A fresh documentation-only walkthrough preserved four
supported facts, added three chosen facts from original LaTeX, and kept one
unresolved item visible without storing or approving anything. Classification
choices do not rewrite the extraction report or establish semantic completeness.

Direct pending-fact review now opens a chosen inventory ID with its current
evidence, review token and global pending count. It validates the whole pending
queue in one read snapshot, preserves default and paged review, and stores no
decision or progress. All 1595 regression tests, 58 focused checks, eleven source
gates, fresh installed workflows and sequential capacity probes pass. A fresh
fictional walkthrough selected and revisited a publication without reading the
whole queue; six facts remained pending and none were approved.

An optional `doctor --materials` check now reports PDF-intake and PDF-output
prerequisite presence separately before profile setup. It reads no profile,
imports no PDF provider and executes no compiler. Missing TeX alone leaves the
PDF-intake prerequisite available when `pypdf` is present; probe errors stay
unknown. Default doctor is unchanged, and presence does not prove parsing or
rendering success. All 1604 regression tests, 25 focused checks, eleven source
gates, base and full fresh installed workflows, and sequential capacity probes
pass. A fresh four-call walkthrough confirms the missing-TeX guidance without
opening a document or creating a profile.

Developer-resume navigation is implemented: README and the live handoff link
directly to current verification and next actions. Completed older evidence is
preserved verbatim in a clearly historical archive, with exact-byte checks and
independent navigation review. All current feature contracts and unresolved
limits remain in the single live handoff; application code and feature scope
are unchanged by this documentation increment.

### Original delivered milestone

**Status: Implemented and locally verified** (ADR 0007, September 18, 2026).
The milestone is committed in `3fc3251`; see SESSION_HANDOFF.md for subsequent
working-tree changes and exact verification evidence.

Make the local pilot useful through Codex for industry research and engineering
applications: onboard selected facts once, compare user-supplied jobs against
evidence, prepare reviewed PDF/answer bundles, and resume from a clear next-action
briefing. Codex handles commands and IDs; the user controls facts and submission.

| Deliverable | Status | Acceptance evidence |
|---|---|---|
| Repository job-search skill and conversational workflow | Implemented | Skill validation and independent operation produced a real draft without approval, sensitive inference, or submission |
| Read-only next-action briefing | Implemented | Workflow stages, stale readiness, pending facts, required unknowns, job filtering, on-demand response checks, and no-write/diagnostic tests pass |
| Usable interpreter setup | Implemented | Explicit Python override, local virtualenv selection, literal argument forwarding and actionable version error verified |
| Research/engineering reuse demonstration | Implemented | One fictional researcher, two jobs, real PDFs/answers, exact degree/publication status, approvals, resumption and unchanged history; PDF visually reviewed |
| Complete regression and installed-pilot gates | Implemented locally | 360 tests with zero skips, required PDF/encryption gates, complete CLI and fresh offline installed pilot pass on macOS/Python 3.12.14 |

The milestone does not depend on live job discovery, semantic rewriting,
browser filling, email/calendar integrations, or a GUI. These remain subsequent
upgrades on the same services, facts and history. Success means useful reviewed
application preparation, not a promise of interviews or offers.

## Next milestone — Daily discovery plus prepared application packages

**Status: In progress** (prioritized September 19, 2026;
[ADR 0009](adr/0009-daily-discovery-and-draft-queue.md)). The first multi-source
discovery and durable saved-job batch increments are implemented and locally
verified. Integrated discovery-to-draft runs and daily execution are implemented
locally; active-user-time evaluation and broader employer coverage remain open.

The delivered pilot demonstrates verified artifacts and reuse, but has not
demonstrated lower active user time. Repeated job input, per-job preparation and
manual application handoffs can negate that benefit. The next outcome is one
bounded search/preparation scope followed by one review queue of new jobs,
prepared drafts and grouped questions. Independent jobs continue when another
needs information. Measure active user minutes and handoffs per accepted draft,
separately from elapsed agent time; a long run alone is not success.

| Increment | Status | Required outcome |
|---|---|---|
| Durable batch preparation for saved jobs | Implemented | CLI, schema 005 checkpoints, fenced leases, shared evidence, isolated blockers, replay/recovery and real-PDF synthetic gate pass; 490-test full gate and fresh installed batch pilot pass |
| Multi-source watchlist discovery | Implemented for configured boards | Greenhouse, Ashby, Lever/global and EU, and Workable; Netflix published-sitemap sampling plus advancing saved-search windows. The combined 1326-test checkpoint, fresh installed gates, two-PDF Workable gate and existing nine-PDF/restore traversal gate pass. Immutable capture/replay, title filtering, source limits/failures and unsupported-employer gaps are explicit. Full market coverage, broader provider cursors and independent source monitoring remain unfinished |
| Discovery-to-draft workflow | Implemented | Saved source/evidence scope, durable source checkpoints, applied/excluded/unchanged selection, resumed child batch and combined review; 539-test checkpoint and fresh installed configured-search gate pass, with sixteen real PDFs and encrypted restoration |
| Daily trigger over the same workflow | Implemented locally | Durable occurrences, timezone policy, fenced recovery, bounded catch-up, pause/resume and semantic notification acknowledgment; 659-test checkpoint and fresh installed eleven-PDF/encrypted-restore daily gate pass. External wake-up setup and personal schedule selection remain separate |
| Preparation preferences and repeated-blocker fairness | Implemented locally | V2 literal title/location filters and stable ordering reach unattempted jobs before repeated blockers; original v1 scope/history compatibility retained. The 693-test checkpoint, fresh install and two-PDF/restore gate pass |
| Fairness across automatic sources | Implemented locally | Durable rotation shares fetch/quota and final selection priority, preserving retries and started legacy runs; 715 tests, fresh installed and three-day/two-PDF/restore gates pass |
| Sustained daily workload | In progress | Twenty synthetic daily runs/200 PDFs and encrypted restore passed under the former 16 MiB allowance; that next run safely refused at 16,023,552 bytes. The expanded byte-capacity gate below now passes, while large material-history scaling remains unmeasured. Historical validation optimization retains every PDF check and reduces the same-history tick from 67.254 to 28.330s; 743 tests and fresh install pass. Mid-run capacity reporting now preserves partial drafts and passes the 753-test/full-installed checkpoint; larger storage lifecycle remains open |
| One-folder run review | Implemented locally | Current validated PDFs/answers, job links, source gaps and grouped blockers; stale files omitted, partial questions explicit, exact private-copy replay. 781 tests and fresh installed search/export/restore gates pass |
| Expanded supported profile capacity | Implemented locally | 256 MiB snapshots/automated storage and 384 MiB archives; no schema conversion. Near-255 MiB synthetic daily preparation and exact encrypted CLI restore pass, with measured ~2.3 GiB peak lifecycle memory. 791 regression tests, fresh installed capacity smoke, PDF and encryption gates pass; see ADR 0011 |
| Active-time evaluation | In progress | Synthetic ten-job one-kickoff/one-review batch passed with two isolated blockers and real PDFs. Local validation-call timing improved; a same-workload user active-time comparison remains unmeasured |
| Larger material storage lifecycle | In progress | [ADR 0010](adr/0010-content-addressed-material-storage.md) has exact-byte payload preparation, explicit migration policy, guarded capture, an owned read-only snapshot repository, per-material historical factual/answer validation, optional approval-record custody, approval-time emitted-fact validation and recorded required-answer eligibility. The 1226-test full gate, near-capacity/encryption/PDF gates and fresh installed pilot pass. Ordinary approval reads now share strict record validation in one transaction, with 15 new service/export regressions. Historical checks preserve later-retired facts, verify recorded packets and unchanged historical peers, and refuse rehashed unsupported output; both capacity checkpoints audit every saved real PDF and optional approval record without changing the source. One explicit synthetic approval survives exact encrypted restore with unchanged readiness. Eighteen additional tests preserve creation authority while checking approval-time facts, retirement and context. Twelve further tests cover required versus optional unanswered questions, preserved partial history and one-snapshot combined validation. Fourteen application regressions now verify aware event/approval ordering, exact timestamp bindings and rollback. Twenty-seven further tests now check recorded facts and required answers at every ready/submitted event, retaining later-retired history and checking every earlier ready event in one snapshot. Three real-PDF audits cover original, restored and later-retired restored homes in source and fresh installations. Nineteen strict application-record tests now reject JSON type aliases, duplicate keys, unsupported completed-workflow metadata and unlinked replay workflows while preserving valid formatting, clocks and later-history replay. Eighteen inventory tests now account for all application events, submissions and workflows, refusing valid-looking orphans and wrong ownership; source and installed pilots audit original, restored and later-retired inventories. Twenty-six batch-history tests now check strict records/workflows and every checkpoint, including earlier invalid drafts hidden by later blocked states, while preserving partial/unapproved/retired history and one PDF/factual audit per distinct material. Original/restored real-PDF batch audits pass in source and fresh installations. Eighteen batch-inventory tests now account for all batch/item/event/lease/workflow ownership, rejecting complete or incomplete orphans and equal-count wrong ownership without extra PDF/fact walks; original/restored inventory gates pass in source and fresh installations. Twenty-seven linked material-build tests now validate closed records, recursive raw JSON, exact identity/digests/completed metadata and raw creation bindings with aware update ordering; both transformations, harmless encoding, valid offsets, later retirement and one PDF/profile pass remain compatible. Source and fresh-installed historical gates pass. Twenty material-inventory tests now account for every material, composite claim link, approval and build/approval workflow, including orphan workflows and output saved before interrupted batch checkpointing; one PDF/profile walk per material and historical approval eligibility remain enforced in source, installed and capacity audits. Twenty-four saved-search configuration tests now verify strict origin/workflow records, typed recursive JSON, identity/digest/artifact bindings, raw creation clocks and aware updates in one snapshot; v1/v2 scopes, replay and exact encrypted restore pass in source and fresh-installed search/daily/filter pilots. Twenty-four search-run origin tests now check closed run/workflow records, typed recursive JSON, parent/manifest/deterministic identity and raw clock bindings in one snapshot; legacy/interrupted runs, parent errors, rollback and replay remain compatible. Source and fresh-installed search/daily/restore gates pass. Strict search-checkpoint row/JSON custody is the next bounded gate; aggregate workflow custody, production sharing and new-home conversion remain unfinished. Schema 7 is unchanged; no migration or conversion command exists |

This milestone advances Phase 2 while composing the existing Phase 1 services.
The first implementation slice was changed to multi-source discovery in response
to the requested employer coverage. Initial discovery covers configured company
boards, not all openings on the internet. Browser safe-fill remains a later upgrade;
package and fact approvals, sensitive answers and final submission retain their
existing boundaries. Creating a scheduled chat is not evidence that this
source-to-package pipeline exists.

## Phase 0 — Repository and truth layer

**Status: In progress**

Outcome: a dependency-light, testable foundation that can represent candidate
facts and provenance without placing private data in the repository.

| Deliverable | Status | Exit evidence |
|---|---|---|
| Product design and local-first architecture decision | Implemented | Design document and ADR 0001 exist |
| Durable resume and handoff protocol | Implemented | `AGENTS.md`, development guide, and live checkpoint agree |
| Zero-install CLI bootstrap and diagnostics | Implemented | Root and `profile decide` help, stable JSON usage errors, doctor, dry-run, and idempotent private init tests pass |
| Private runtime path resolution and user-only directories | In progress | Typed CLI and public SQLite adapters cover POSIX permissions, direct single-link database/sidecar files, orphan refusal, read-only persistent-WAL refusal, and creation before SQLite access; the CLI adds XDG/portable containment and exclusive no-follow config creation. Native Windows paths remain planned; sampled same-UID TOCTOU is an explicit residual limit |
| Typed atomic claims, evidence, status, scope, and sensitivity | Implemented | Domain invariants and serialization tests pass |
| `NeedInfo`, `Contradiction`, and claim-packet resolution | Implemented | Missing/conflicting/stale/sensitive cases fail closed |
| SQLite schema and migration mechanism | Implemented | Atomic fresh/concurrent init, consistent validation snapshots, exact-target race refusal, closed migration execution policy, isolated trusted schema inventories, future refusal, checksum validation and existing-only no-migration writes pass locally; 839 tests and fresh installed pilot pass, hosted verification remains pending |
| Repository/service boundaries for validated mutation | In progress | Profile, retirement, job, material, and application mutations use validated services with audited idempotency; broader editing and lifecycle services remain |
| Resume import, extraction proposal, and human review | Implemented for text/PDF/static-LaTeX | Every nonblank extracted line is accounted for; unknown/blocked content and document gaps remain explicit. Policy 3 permits complete fact retention while policy 2 and extractor 1 preserve recorded imports and legacy selections. Original PDF/TeX and extracted-text hashes bind selection; pending claims still require per-item approval. Bounded review pages account for all pending facts and preserve continuation after decisions, with unchanged default review. A chosen pending inventory fact can open directly with exact evidence/token and global count after the same complete-queue validation. Extractor 3 preserves explicit research context and a separate Research material section; explicit extractor 4 adds four neutral section boundaries without changing earlier versions. Read-only topic inventory supports interview resumption without approval or completeness claims. Explicit text/stdin statement intake preserves typed-answer origin with separate approval and unchanged resume defaults. Separately chosen disjoint publication groups preserve exact evidence and qualifiers in one full-proposal manifest without storage or approval. The 1595-test suite and fresh installed onboarding/lifecycle gates pass. OCR, DOCX, arbitrary TeX expansion, durable original-document provenance and broader semantic conflict assistance remain planned |
| Content-free diagnostic logging | Implemented | Opt-in fixed-schema JSONL excludes caller content; failure, interruption, ambiguous-commit recovery, and broken-sink tests pass; no file logs or telemetry |
| Backup, export, deletion, and retention | Implemented for bounded pilot; broader lifecycle planned | Encrypted complete-database backup/restore includes job/material/application state. Fixed-schema support export and confirmed whole-portable-home deletion pass the CLI lifecycle gate. External source files/exports/backups remain caller-owned. Full filesystem backup, per-record deletion and automatic retention remain planned |
| Installed-package verification | Implemented locally | Source/wheel install, bundled migrations 001–007, both optional extras, full pilot and batch/search/daily/window gates pass in a fresh offline environment; latest run used local Python 3.12.14 |
| CI interpreter/OS matrix | In progress | User-reported results after the workflow fix: macOS 15/Python 3.12 and 3.13 plus Ubuntu 24.04/Python 3.13 pass; Ubuntu/Python 3.12 exposed a concurrent schema-validation race. The local correction still needs a full hosted run |
| Synthetic candidate/job fixtures and adversarial corpus | In progress | One synthetic profile plus adversarial schema, all nine taxonomy-category positives, explicit lexical false-positive controls, secret, percent-encoding, fragmentation, whole-source, padding, mutation, and no-write import cases exist; broader candidate/job corpus remains |

Phase 0 exits only when the canonical commands pass from a clean checkout, real
data is unnecessary, and the truth/privacy invariants have automated coverage.

## Phase 1 — Usable application MVP

**Status: Local pilot implemented; broader product in progress**

| Deliverable | Status | Evidence / limits |
|---|---|---|
| Immutable job text and URL | Implemented for manual and supported feed capture | User-supplied text plus versioned public-feed captures, digest, capture time and source spans; feed observation does not verify current application-page availability |
| Requirement extraction | Implemented for recognized text headings | Exact quotes, classification basis and ambiguity labels; no semantic hiring hypotheses |
| Evidence matrix and gaps | Implemented | Approved-packet retrieval, shared terms, structured unknowns; fit and hiring probability stay unresolved |
| Structured resume, LaTeX, PDF, text and manifest | Implemented | Version-2 clean typography, source-aware bullets and role/education headings, closed presentation overrides, exact approved text/mappings, extraction and overflow gates; version-1 validation/replay/approvals preserved (ADR 0008) |
| Copy-paste career answers | Implemented | Selected approved text plus mappings; sensitive/unknown questions yield NeedInfo, required missing answers block approval. Explicit sign/e-sign requests stop before fact resolution while bounded career noun phrases remain usable. Eleven signature/history regressions, the 947-test full gate and fresh installed CLI pilot pass; old unanswered history is preserved without admitting unsafe drafts |
| Application tracker and submission snapshot | Implemented | Validated transitions, append-only audited/hash-linked events and immutable exact bundle snapshot; retirement preserves history and blocks future use. Aware event ordering and approval-before-readiness/submission preserve exact stored strings; 14 chronology regressions, 27 historical-use tests, 19 strict record/replay tests and 18 inventory tests, the 1087-test full gate and fresh installed pilot pass. Explicit use audits check original evidence at each ready/submitted event without granting current readiness; ordinary reads reject ambiguous JSON and corrupt workflow records. The explicit inventory audit additionally accounts for every application event, submission and application workflow |
| Human approval and submission boundary | Implemented | Exact material digest approval; applied requires explicit confirmation of manual submission; no external action |

`scripts/check_pilot.py` verifies the full CLI workflow, diagnostics separation,
backup/restore of all pilot state, retirement invalidation, and confirmed deletion.
`scripts/check_package.py --pilot-wheelhouse PATH` repeats it from a fresh offline
wheel installation. `scripts/check_materials.py` refuses optional-provider skips.
See the handoff for exact final counts, interpreter, visual QA and limitations.
The command-line quickstart is in `docs/QUICKSTART.md`.

Richer input formats, semantic tailoring, generic page fetching, broader platforms,
ATS compatibility validation, and a graphical interface remain unfinished.

Phase 1 exits when every factual artifact unit maps to approved claim IDs, every
missing sensitive fact becomes `NeedInfo`, supported PDFs pass critical-field
extraction, and the complete workflow runs on synthetic fixtures.

## Phase 2 — Job discovery

**Status: In progress**

- official Greenhouse, Lever/global and EU, Ashby and Workable adapters plus CLI capture
  are implemented and locally verified; see [JOB_DISCOVERY.md](JOB_DISCOVERY.md);
- bounded role/location planning and Codex-assisted source finding are implemented
  and locally verified without profile setup; they establish no market-wide coverage;
- the public major-tech catalog has feed routes for Anthropic/OpenAI, a bounded
  Netflix sitemap route, and explicit manual gaps for Google/Apple/Amazon/Meta;
  further company connectors,
  authorized alert ingestion and supplemental search remain planned;
- generic JSON-LD and compliant company-page ingestion;
- manifests, durable saved scopes, bounded reads, per-run coverage and daily runs
  and Netflix traversal are implemented. Broader source cursors and
  a source-health history remain planned;
- stable per-source content identities preserve versions; cross-source aliases
  and conservative cross-source deduplication remain planned;
- explainable ranking with hard constraints, evidence strength, and uncertainty.

Unauthorized LinkedIn or Indeed scraping/auto-apply code is not a roadmap item.

## Phase 3 — Browser assistance

**Status: Planned**

- visible Playwright sessions and semantic field extraction;
- minimum-data answer packets and a deterministic safe-fill policy;
- resumable browser state without duplicated secrets;
- Greenhouse, Lever, and Ashby application adapters;
- structured final review and mandatory human gates.

The public product target is review-ready assistance. Autonomous final submission,
CAPTCHA/MFA bypass, and broad laptop control are not goals.

## Phase 4 — Communication and learning loop

**Status: Planned**

- proposed application events from user-authorized email;
- reminders and optional calendar integration;
- reusable, evidence-linked behavioral stories;
- local conversion analytics with sample size and uncertainty;
- calibration from user overrides and outcomes;
- connector/plugin interfaces that preserve the same policy boundary.

## Phase 5 — Community ecosystem

**Status: Planned**

- contributor and adapter authoring guides;
- more resume templates and international conventions;
- optional local-model provider and encrypted portable vault;
- internationalization and optional desktop packaging.

## Cross-phase release gates

No phase may regress these requirements:

- zero unsupported factual claims in the golden evaluation corpus;
- full claim traceability for factual resume and questionnaire content;
- missing sensitive information always fails to `NeedInfo`;
- private runtime data remains outside the repository;
- tests and demos use synthetic data only;
- external content remains untrusted and cannot authorize tools;
- no default workflow submits an application;
- validation, state changes, and meaningful side effects remain deterministic,
  typed, testable, and auditable.
