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

As of September 4, 2026, the repository is in **Phase 0**. The design basis,
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
an isolated installed-extra round trip are implemented. Edit and user-facing semantic
contradiction workflows, semantic and broader obfuscation classification,
registered derivations, data lifecycle tools, and all end-user application
workflows in Phases 1–5 remain planned or in progress.

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
| SQLite schema and migration mechanism | Implemented | Atomic fresh/concurrent init, future refusal, checksum validation, and existing-only no-migration writes pass |
| Repository/service boundaries for validated mutation | In progress | Claim/evidence service round trips, CLI import, and single-item CLI approve/reject transitions use typed services and atomic repository mutations with exact idempotent replay; edit and broader lifecycle services remain |
| Resume import, extraction proposal, and human review | In progress | Manifest-v2 file/stdin proposals snapshot nested state, bind exact source bytes to a path-free digest artifact and registered ingress, use closed value schemas plus a versioned nine-category restricted-text taxonomy and bounded minimization guards, create workflow/index-bound record digests and stale-safe review tokens, and support storage-free decision syntax previews plus confirmed, minimized, audited CLI approve/reject with replay and resolution revalidation; extraction, edit/user-facing semantic contradiction handling, and semantic/novel-obfuscation classification remain |
| Content-free diagnostic logging | Implemented | Opt-in fixed-schema JSONL excludes caller content; failure, interruption, ambiguous-commit recovery, and broken-sink tests pass; no file logs or telemetry |
| Backup, export, deletion, and retention | In progress | Optional bounded encrypted profile backup and confirmed new-home restore pass synthetic authentication, tamper, private-path, stale-preview, partial-output refusal, and provenance/replay round trips. Full filesystem backup, support export, deletion, and retention remain planned |
| Installed-package verification | Implemented | Base and optional encrypted-backup source/wheel installations, entry point, bundled migrations, and synthetic provenance/replay pass on local Python 3.13.1 |
| CI interpreter/OS matrix | In progress | Python 3.12/3.13 on Ubuntu 24.04 and macOS 15 configured with pinned actions and isolated synthetic gates; hosted runs have not yet verified those targets |
| Synthetic candidate/job fixtures and adversarial corpus | In progress | One synthetic profile plus adversarial schema, all nine taxonomy-category positives, explicit lexical false-positive controls, secret, percent-encoding, fragmentation, whole-source, padding, mutation, and no-write import cases exist; broader candidate/job corpus remains |

Phase 0 exits only when the canonical commands pass from a clean checkout, real
data is unnecessary, and the truth/privacy invariants have automated coverage.

## Phase 1 — Usable application MVP

**Status: Planned**

- manually save a job URL with an immutable raw snapshot;
- extract explicit and inferred requirements with source spans;
- produce an explainable requirement-to-claim fit matrix and gap questions;
- generate structured resume content, then LaTeX, PDF, extracted text, manifest,
  and transparent readiness validation;
- produce copy-paste questionnaire answers with scope and sensitivity policy;
- track applications through validated state transitions and append-only events;
- preserve an immutable submission snapshot;
- require human approval before readiness and final submission.

Phase 1 exits when every factual artifact unit maps to approved claim IDs, every
missing sensitive fact becomes `NeedInfo`, supported PDFs pass critical-field
extraction, and the complete workflow runs on synthetic fixtures.

## Phase 2 — Job discovery

**Status: Planned**

- official Greenhouse, Lever, and Ashby adapters;
- generic JSON-LD and compliant company-page ingestion;
- watchlists, source cursors, bounded sync, health, and freshness reports;
- conservative deduplication that preserves source aliases and versions;
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
