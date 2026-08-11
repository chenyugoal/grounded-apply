# ADR 0001: Local-first modular monolith

- **Status:** Accepted
- **Date:** 2026-08-11
- **Decision owners:** Grounded Apply maintainers
- **Source:** `GROUNDED_APPLY_DESIGN.md`, sections 5–7, 18–22, and 27

## Context

Grounded Apply will manage sensitive career history, application answers, job
snapshots, generated documents, and application events. It must make factual
claims traceable, handle unknown or sensitive information explicitly, preserve
state across workflows, and remain useful from Codex, scripts, CI, and possible
future interfaces.

A prompt-only application cannot reliably provide transactions, schema
migrations, provenance, authorization, idempotency, state machines, validation,
export, or deletion. A hosted or microservice-first system would add operational
and privacy complexity before a single-user product benefits from it. A vector
store alone cannot enforce revisions, dates, scope, sensitivity, or referential
integrity.

## Decision

Build Grounded Apply as a **local-first Python modular monolith**.

1. One repository, one normal local process, and one relational database own the
   initial system of record.
2. A typed `gapply` CLI is the primary machine contract. Codex skills and future
   interfaces call the same application services through that contract rather
   than editing persistence directly.
3. Domain boundaries are explicit: profile, jobs, matching, materials,
   applications, automation, connectors, and agent orchestration.
4. Business truth, authorization, scope, state transitions, validation, and
   persistence are deterministic and testable. Language models are adapters at
   language-heavy edges and receive minimal structured context packets.
5. SQLite is the default relational source of truth. Optional PostgreSQL, FTS,
   or embeddings may be added behind ports; search indexes are never canonical
   evidence.
6. Personal data and artifacts live in platform-standard private runtime
   directories, or beneath `GROUNDED_APPLY_HOME`, never in the public repository.
7. External integrations use narrow adapters. Local web and MCP surfaces may be
   added only after the CLI/service contract stabilizes; no generic shell or
   direct-database tool is exposed.
8. Browser assistance is visible, resumable, minimum-data, and human-gated. Final
   submission is a user action in the default product.

The early Phase 0 executable may use a dependency-free standard-library CLI and
migration runner so a clean checkout is testable immediately. Typer, SQLAlchemy,
Alembic, `uv`, and other target-stack tools remain planned hardening until they
are installed, configured, and adopted through a later decision or documented
implementation change. The bootstrap does not weaken the architectural boundary.

## Consequences

### Benefits

- Local storage and data minimization reduce default disclosure risk.
- One transaction boundary simplifies claim revisions, event projections, and
  immutable submission snapshots.
- A normal CLI makes workflows resumable and usable independently of a chat
  runtime.
- Explicit ports permit later replacement of models, job sources, document
  renderers, databases, and user interfaces.
- Deterministic policies and synthetic fixtures make truth and privacy failures
  testable.

### Costs and constraints

- Modules require discipline even though Python does not enforce process-level
  isolation.
- SQLite concurrency and local-only UX may eventually limit multi-user or remote
  scenarios; those are non-goals for the initial product.
- Every model-generated factual unit needs structured inputs, provenance, and a
  post-generation verifier, which adds implementation work.
- Connectors and browser adapters must translate into shared domain contracts and
  cannot shortcut policies for convenience.
- Operating-system disk encryption is the initial baseline; stronger database or
  vault encryption remains optional future work.

## Alternatives considered

### Prompt-only Codex workflow

Rejected as the source of truth. It cannot provide durable transactional state,
schema migrations, reproducible validation, or reliable permission enforcement.
Codex remains the conversational orchestrator.

### Microservices

Rejected for the single-user initial product. Deployment, authentication,
observability, network privacy, and distributed consistency costs outweigh useful
isolation. Module ports preserve a future extraction path if evidence supports it.

### Hosted SaaS first

Rejected because it expands custody, authentication, compliance, and breach
surface before they are required. Optional remote services may later be explicit
user choices behind outbound-data previews.

### Vector database as the primary store

Rejected. Similarity search does not replace relational invariants, exact
provenance, revision chains, temporal validity, sensitivity, and scope. Embeddings
may be a rebuildable secondary index.

### Browser-first autonomous application bot

Rejected. It is fragile across sites and conflicts with the human-gated truth,
authorization, and legal-attestation policy. Browser assistance is deferred until
the evidence and application state foundations are reliable.

## Follow-up decisions

Separate ADRs should be written when evidence is sufficient for:

- the stable schema and migration tool after the bootstrap;
- Tectonic versus pinned TeX Live;
- local web UI scope;
- embeddings and model-provider policy;
- stronger encrypted storage;
- browser adapter authorization and trace retention.
