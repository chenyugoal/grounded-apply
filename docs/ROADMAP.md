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

As of September 3, 2026, the repository is in **Phase 0**. The design basis,
resumability contract, executable scaffold, deterministic truth policies, and
initial SQLite persistence slice are implemented and verified. Safe structured
text-import proposals, registered schema-v1 claim values, deterministic
high-confidence defense-in-depth screening of persisted content and metadata,
whole-source minimization guards, a stable-descriptor file/stdin CLI boundary,
application-owned digest provenance, a registered manifest ingress,
replay-bound workflow records, provenance-validating read-only review, and the
first synthetic profile fixture are implemented. Review decisions, broader
restricted-content classification, registered derivations, data lifecycle
tools, and all end-user application workflows in Phases 1–5 remain planned or
in progress.

## Phase 0 — Repository and truth layer

**Status: In progress**

Outcome: a dependency-light, testable foundation that can represent candidate
facts and provenance without placing private data in the repository.

| Deliverable | Status | Exit evidence |
|---|---|---|
| Product design and local-first architecture decision | Implemented | Design document and ADR 0001 exist |
| Durable resume and handoff protocol | Implemented | `AGENTS.md`, development guide, and live checkpoint agree |
| Zero-install CLI bootstrap and diagnostics | Implemented | Help, doctor, dry-run, and idempotent private init tests pass |
| Private runtime path resolution and user-only directories | In progress | POSIX/XDG, portable-child/database containment, and permission-drift checks pass; native Windows paths remain |
| Typed atomic claims, evidence, status, scope, and sensitivity | Implemented | Domain invariants and serialization tests pass |
| `NeedInfo`, `Contradiction`, and claim-packet resolution | Implemented | Missing/conflicting/stale/sensitive cases fail closed |
| SQLite schema and migration mechanism | Implemented | Atomic fresh/concurrent init, future refusal, checksum validation, and existing-only no-migration writes pass |
| Repository/service boundaries for validated mutation | In progress | Claim/evidence service round trips and CLI import use services; approval mutation remains |
| Resume import, extraction proposal, and human review | In progress | Manifest-v2 file/stdin proposals snapshot nested state, bind exact source bytes to a path-free digest artifact and registered ingress, use closed value schemas plus bounded content/minimization guards, create workflow/index-bound records, and revalidate provenance for replay/read-only review; extraction, broader restricted-content classification, and review decisions remain |
| Redacted logging, backup, export, and deletion | Planned | Privacy and round-trip tests pass on synthetic data |
| Synthetic candidate/job fixtures and adversarial corpus | In progress | One synthetic profile plus adversarial schema, sensitive-content, secret, percent-encoding, fragmentation, whole-source, padding, mutation, and no-write import cases exist; broader candidate/job corpus remains |

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
