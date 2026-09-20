# Roadmap

This roadmap defines delivery scope and acceptance gates. It is not the live
session log; use [`SESSION_HANDOFF.md`](SESSION_HANDOFF.md) for the current
working-tree checkpoint and next exact task.

## Status rules

- **Implemented** means code or documentation exists and its documented
  verification has passed.
- **In progress** means work has started but is incomplete, unverified, or not
  integrated.
- **Planned** means design intent only.
- **Blocked** names a concrete dependency or unresolved decision.

As of September 4, 2026, **the bounded local application pilot is implemented**
(ADR 0006); broader platform and semantic features remain in progress. The design basis,
resumability contract, executable scaffold, deterministic truth policies, and
initial SQLite persistence slice are implemented and verified. Safe structured
text-import proposals, registered schema-v1 claim values, a versioned
nine-category restricted-text taxonomy for deterministic high-confidence
screening of persisted content and metadata, whole-source minimization guards, a
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
an isolated installed-extra round trip are implemented. Exact text extraction,
selected onboarding, explicit name/contact vocabulary, audited retirement and
approved replacement, support export, and whole-portable-home deletion now extend
that foundation. The local pilot covers job capture, evidence retrieval, verified
PDFs/answers, explicit material approval, and manual application history. Broader
semantic contradiction handling, novel-obfuscation classification, registered
derivations, automatic retention, and Phases 2–5 remain planned or in progress.

## Delivered milestone — Codex-guided application preparation

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
| Multi-source watchlist discovery | Implemented for configured boards | Greenhouse, Ashby, Lever/global and EU; Netflix published-sitemap sampling plus advancing saved-search windows pass the 659-test checkpoint, installed gate and nine-PDF/restore gate. Immutable capture/replay, title filtering, source limits/failures and unsupported-employer gaps are explicit. Full market coverage, broader provider cursors and independent source monitoring remain unfinished |
| Discovery-to-draft workflow | Implemented | Saved source/evidence scope, durable source checkpoints, applied/excluded/unchanged selection, resumed child batch and combined review; 539-test checkpoint and fresh installed configured-search gate pass, with sixteen real PDFs and encrypted restoration |
| Daily trigger over the same workflow | Implemented locally | Durable occurrences, timezone policy, fenced recovery, bounded catch-up, pause/resume and semantic notification acknowledgment; 659-test checkpoint and fresh installed eleven-PDF/encrypted-restore daily gate pass. External wake-up setup and personal schedule selection remain separate |
| Preparation preferences and repeated-blocker fairness | Implemented locally | V2 literal title/location filters and stable ordering reach unattempted jobs before repeated blockers; original v1 scope/history compatibility retained. The 693-test checkpoint, fresh install and two-PDF/restore gate pass |
| Fairness across automatic sources | Implemented locally | Durable rotation shares fetch/quota and final selection priority, preserving retries and started legacy runs; 715 tests, fresh installed and three-day/two-PDF/restore gates pass |
| Sustained daily workload | In progress | Twenty synthetic daily runs/200 PDFs and encrypted restore passed under the former 16 MiB allowance; that next run safely refused at 16,023,552 bytes. The expanded byte-capacity gate below now passes, while large material-history scaling remains unmeasured. Historical validation optimization retains every PDF check and reduces the same-history tick from 67.254 to 28.330s; 743 tests and fresh install pass. Mid-run capacity reporting now preserves partial drafts and passes the 753-test/full-installed checkpoint; larger storage lifecycle remains open |
| One-folder run review | Implemented locally | Current validated PDFs/answers, job links, source gaps and grouped blockers; stale files omitted, partial questions explicit, exact private-copy replay. 781 tests and fresh installed search/export/restore gates pass |
| Expanded supported profile capacity | Implemented locally | 256 MiB snapshots/automated storage and 384 MiB archives; no schema conversion. Near-255 MiB synthetic daily preparation and exact encrypted CLI restore pass, with measured ~2.3 GiB peak lifecycle memory. 791 regression tests, fresh installed capacity smoke, PDF and encryption gates pass; see ADR 0011 |
| Active-time evaluation | In progress | Synthetic ten-job one-kickoff/one-review batch passed with two isolated blockers and real PDFs. Local validation-call timing improved; a same-workload user active-time comparison remains unmeasured |
| Larger material storage lifecycle | Planned | [Proposed ADR 0010](adr/0010-content-addressed-material-storage.md) evaluates exact-byte sharing and explicit conversion into a new private home; no migration or conversion command exists |

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
| SQLite schema and migration mechanism | Implemented | Atomic fresh/concurrent init, consistent validation snapshots during concurrent migration, future refusal, checksum validation, and existing-only no-migration writes pass locally; hosted verification of the snapshot fix is pending |
| Repository/service boundaries for validated mutation | In progress | Profile, retirement, job, material, and application mutations use validated services with audited idempotency; broader editing and lifecycle services remain |
| Resume import, extraction proposal, and human review | Implemented for UTF-8 pilot | Exact line extraction and hash-bound selected onboarding retain source spans; explicit name/contact vocabulary 2 retains vocabulary 1 replay. Pending records require per-item audited approval. Retirement/replacement resolves selected corrections without rewriting history. PDF/DOCX input and broader semantic conflict assistance remain planned |
| Content-free diagnostic logging | Implemented | Opt-in fixed-schema JSONL excludes caller content; failure, interruption, ambiguous-commit recovery, and broken-sink tests pass; no file logs or telemetry |
| Backup, export, deletion, and retention | Implemented for bounded pilot; broader lifecycle planned | Encrypted complete-database backup/restore includes job/material/application state. Fixed-schema support export and confirmed whole-portable-home deletion pass the CLI lifecycle gate. External source files/exports/backups remain caller-owned. Full filesystem backup, per-record deletion and automatic retention remain planned |
| Installed-package verification | Implemented locally | Source/wheel install, bundled migrations 001–007, both optional extras, full pilot and batch/search/daily/window gates pass in a fresh offline environment on local Python 3.13.1 |
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
| Copy-paste career answers | Implemented | Selected approved text plus mappings; sensitive/unknown questions yield NeedInfo, required missing answers block approval |
| Application tracker and submission snapshot | Implemented | Validated transitions, append-only audited/hash-linked events and immutable exact bundle snapshot; retirement preserves history and blocks future use |
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

- official Greenhouse, Lever/global and EU, and Ashby adapters plus CLI capture
  are implemented and locally verified; see [JOB_DISCOVERY.md](JOB_DISCOVERY.md);
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
