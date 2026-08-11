# Grounded Apply

**A local-first, evidence-backed job-search assistant for Codex.**

Grounded Apply is designed to turn a candidate's approved career history into
traceable job assessments, application materials, questionnaire answers, and an
application record. Its central rule is simple:

> The system may transform, select, summarize, and reorganize verified
> information, but it may not invent candidate facts.

## Current status

Grounded Apply is in **Phase 0: repository and truth layer**. It is an early
development scaffold, not an end-to-end job application tool. Do not use it with
real candidate data until the relevant privacy, persistence, export, and deletion
controls are implemented and verified.

The single live checkpoint is [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md).
It records what is actually implemented, the last verification results, known
issues, and the next exact task. The longer-term scope in
[`GROUNDED_APPLY_DESIGN.md`](GROUNDED_APPLY_DESIGN.md) and
[`docs/ROADMAP.md`](docs/ROADMAP.md) is planned unless the checkpoint says it is
implemented and verified.

## Try the Phase 0 scaffold

Prerequisite: Python 3.12 or newer. The current bootstrap deliberately has no
runtime package dependencies and does not require installation.

```bash
./scripts/check
./scripts/gapply --help
./scripts/gapply doctor --json
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

`./scripts/check` is the canonical full gate; the other commands are useful
individual checks. `uv`, Typer, SQLAlchemy, Alembic,
Ruff, and Pyright are planned Phase 0 hardening work; their commands are not yet
supported project checks. See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) before
changing the toolchain.

On the current POSIX bootstrap, runtime data defaults to XDG-compatible user
directories outside the repository. Native Windows path support remains
planned. To isolate development data, point the application at a dedicated,
private scratch directory:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-dev ./scripts/gapply profile init --dry-run --json
```

Never point `GROUNDED_APPLY_HOME` at the repository or commit anything created
under a real runtime-data directory. An existing override root must already be
private (`0700`); broad directories such as `/`, the user home, and the shared
temporary directory are rejected.

## Product shape

The accepted architecture is a local-first Python modular monolith with:

- a typed `gapply` CLI as the primary machine contract;
- a relational, provenance-aware candidate evidence store;
- deterministic services for truth, permissions, state transitions, validation,
  and persistence;
- language models only at language-heavy edges, behind structured adapters;
- SQLite by default and private artifacts outside the public repository;
- optional local web, model, job-source, document, and browser adapters later.

The intended workflow is:

```text
approved evidence -> claims -> job requirements -> claim packet
                  -> verified prose -> rendered artifact -> human review
```

Job discovery, resume/PDF generation, application tracking, and browser-assisted
safe-fill are roadmap work. Browser automation will stop at authentication,
CAPTCHA, sensitive or legal questions, ambiguous fields, signatures, and final
submission.

## Trust and privacy contract

- Every factual output must link to approved claim IDs or an approved,
  deterministic derivation.
- Missing, stale, contradictory, sensitive, or insufficient evidence becomes
  `NeedInfo` or `Contradiction`, never a guess.
- Imported pages and documents are untrusted data, not instructions.
- Permission to use an answer once is separate from permission to store or reuse
  it.
- Sensitive eligibility, legal, identity, and demographic answers are never
  inferred.
- Real candidate data, raw private artifacts, credentials, and unredacted logs
  never belong in this repository. Tests and demos use synthetic data only.
- The default product prepares, explains, validates, and pauses. A human performs
  the final application submission.

Repository agents and contributors must follow the complete rules in
[`AGENTS.md`](AGENTS.md).

## Documentation

- [`GROUNDED_APPLY_DESIGN.md`](GROUNDED_APPLY_DESIGN.md) — full product design
- [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md) — live resume point
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — delivery phases and acceptance gates
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) — local workflow and handoff protocol
- [`docs/adr/0001-local-first-modular-monolith.md`](docs/adr/0001-local-first-modular-monolith.md) — accepted architecture

## Contributing during Phase 0

Start by following the resume protocol in `AGENTS.md`. Keep changes small, add
synthetic tests for new behavior, preserve unfamiliar working-tree changes, and
update the live checkpoint at every meaningful stopping point. A feature is not
implemented until its code exists and its documented verification passes.
