# Development guide

This guide describes the current Phase 0 workflow. It intentionally separates
commands that work now from the target toolchain described in the product design.

## Prerequisites

- Python 3.12 or newer
- a POSIX shell for `scripts/gapply`
- Git

The current bootstrap has no runtime dependencies. `pyproject.toml` contains
packaging metadata and Hatchling as its build backend, but repository development
does not currently require installing the package.

## Canonical commands

Run commands from the repository root.

| Purpose | Current command | Status |
|---|---|---|
| Full repository gate | `./scripts/check` | Supported now |
| CLI help | `./scripts/gapply --help` | Supported now |
| Environment/path diagnostics | `./scripts/gapply doctor --json` | Supported now |
| Full test suite | `PYTHONPATH=src python3 -m unittest discover -s tests -v` | Supported now |

The full gate runs both CLI smokes and the test suite without creating bytecode
in the checkout. The wrapper adds `src` to `PYTHONPATH`, so it exercises the
working tree without an editable install. For a disposable runtime root:

```bash
GROUNDED_APPLY_HOME=/tmp/grounded-apply-dev ./scripts/gapply doctor --json
```

Do not use real candidate data in repository development.

### Planned command hardening

The design targets `uv`, Typer, SQLAlchemy, Alembic, Ruff, and Pyright. They are
**planned**, not current prerequisites or passing checks. Expected commands such
as the following must not be called canonical until their dependencies,
configuration, and tests are committed and the commands pass:

```bash
uv sync --all-groups
uv run gapply --help
uv run pytest
uv run ruff check .
uv run pyright
uv run alembic upgrade head
```

When the project adopts them, update this command matrix, `README.md`,
`AGENTS.md`, and `docs/SESSION_HANDOFF.md` in the same change. Remove obsolete
commands rather than leaving two ambiguous paths.

## Source layout and dependency direction

Grounded Apply is a modular monolith. The target domain boundaries are:

- `profile`: claims, evidence, preferences, eligibility, stories, contradictions,
  and memory proposals;
- `jobs`: discovery, normalization, snapshots, deduplication, and requirements;
- `matching`: gates, requirement-to-claim mapping, gaps, and fit explanations;
- `materials`: structured content, verification, LaTeX, PDF, and manifests;
- `applications`: records, append-only events, submissions, and reminders;
- `automation`: safe browser plans, human gates, traces, and resumability;
- `connectors`: external job, model, document, browser, and email adapters;
- `agent`: Codex workflows and versioned structured model calls.

Keep dependency direction inward:

```text
CLI / future API -> application services -> domain and policies
                                      <- repository and external adapters
```

Domain and policy modules must not import CLI, web, ORM, browser, or model SDK
types. Prompts may orchestrate typed services; they never own truth,
authorization, persistence, or state transitions.

## Development workflow

1. Resume exactly as described in `AGENTS.md` and
   `docs/SESSION_HANDOFF.md`.
2. Choose one bounded roadmap outcome and identify its truth/privacy failure
   modes before implementation.
3. Add or update synthetic tests. Prefer a failing test that expresses the rule
   before changing behavior.
4. Implement through the appropriate domain or application boundary. Avoid
   direct storage mutation from an interface.
5. Run the narrow test while iterating.
6. Run the canonical full test command and both CLI smoke commands.
7. Review the complete diff for private data, secrets, scope creep, and stale
   documentation.
8. Update `docs/SESSION_HANDOFF.md` at the meaningful stopping point.

### Definition of done

A change is complete only when:

- behavior and failure behavior are represented by tests;
- the canonical test suite passes, or a precise pre-existing failure is recorded;
- public interfaces are typed and errors are structured;
- truth, scope, sensitivity, and human-gate policies remain fail-closed;
- persistence changes are migration-safe and idempotent where applicable;
- fixtures are unmistakably synthetic;
- user-facing and handoff documentation matches the code;
- the live checkpoint identifies the next exact task.

## Truth-layer development

For every value that might appear in an application:

1. classify its intent and sensitivity;
2. resolve typed entities, approved claims, and approved derivations;
3. filter by status, scope, effective dates, and reuse policy;
4. return supported claim IDs, `NeedInfo`, or `Contradiction`;
5. generate only from the resulting claim packet;
6. verify factual units after generation;
7. persist provenance and validation results.

Use explicit enums for lifecycle and policy states. Confidence measures extraction
certainty; it never upgrades an unverified claim. A derived claim must record its
versioned rule, inputs, calculation time, and staleness policy. Phase 0 stores and
audits that provenance but rejects derived values during resolution until a
registered evaluator can recompute them.

## Persistence and migrations

SQLite is the target default source of truth; FTS or embeddings are secondary
indexes only. Once persistence lands:

- application code uses repositories and services, never ad hoc SQL from the CLI;
- every schema change has a forward migration and an upgrade test;
- opening a future schema version fails closed;
- mutations accept or grow toward idempotency keys and dry-run behavior;
- application events are append-only, while current state is a projection;
- job-posting and submission snapshots are immutable;
- runtime data is created outside the repository with user-only permissions.

Until Alembic is actually installed and configured, do not document a migration
command as supported. Record the temporary migration mechanism and its test in
the live checkpoint.

## Testing priorities

Use only synthetic fixtures. Alongside normal unit tests, add adversarial cases
as their surfaces are introduced:

- missing or stale information produces `NeedInfo`;
- contradictory claims are not silently resolved;
- unsupported metrics, technologies, seniority, or ownership are rejected;
- sensitive facts cannot be inferred or reused outside policy;
- imported prompt-injection text remains inert;
- repeated mutations are idempotent;
- invalid application transitions fail;
- duplicate jobs are not over-merged;
- rendering fails when verified content or critical extracted fields are absent;
- browser plans stop at every mandatory human gate.

The unsupported factual claim rate on the golden corpus is always zero.

## Runtime data and safe diagnostics

`GROUNDED_APPLY_HOME` overrides all data locations beneath one dedicated private
root. Without it, the current POSIX implementation uses XDG-compatible config,
data, cache, and state directories. Native Windows directory conventions remain
planned. Never default to a repository-relative data directory.

Normal logs may include run IDs, typed event metadata, hashes, redacted errors,
and protected artifact references. They may not include raw resumes, sensitive
answers, personal model prompts, secrets, cookies, browser storage, or unredacted
screenshots.

Before sharing a diff or support artifact:

```bash
git diff --check
git status --short
```

Inspect the complete diff manually. Automated scanning is an additional gate,
not a replacement for review.

## Exact session handoff protocol

`docs/SESSION_HANDOFF.md` is the single live checkpoint. `docs/ROADMAP.md` holds
phase scope; do not create competing status notes.

At session start:

1. Run `pwd`.
2. Read `AGENTS.md`, `docs/SESSION_HANDOFF.md`, `docs/ROADMAP.md`, this guide,
   relevant ADRs, and relevant design sections.
3. Run `git status --short --branch`, `git log -5 --oneline --decorate`, and
   `git diff --stat`; inspect relevant uncommitted diffs.
4. Run the handoff's **First command** and compare the result with **Verification**.
5. Continue the named **Next exact task**, or update the handoff before selecting
   a different task if repository evidence makes it stale.

At every meaningful stopping point, replace—not append duplicate copies of—the
live fields with:

- timestamp and current `HEAD`;
- milestone and status;
- completed items, naming whether they are committed or uncommitted;
- exact commands run and pass/fail outcomes;
- current working-tree paths relevant to the milestone;
- known issues or blockers;
- numbered next tasks, each naming the first file or command;
- one **First command** for the next session;
- durable key decisions not already captured by an ADR.

Do not write “tests pass” without the command. Do not write “next: continue
implementation.” The checkpoint must allow a new agent with no chat history to
act safely in its first minute.
