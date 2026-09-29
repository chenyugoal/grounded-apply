# Roadmap

Grounded Apply is a public **experimental alpha** for supervised job discovery
and application preparation. Broader development is paused. This page separates
working capabilities from unfinished work; wishlist entries have no promised
schedule. See the [README](../README.md) to get started and the
[live checkpoint](SESSION_HANDOFF.md) for current verification and repository state.

## Status meanings

- **Implemented:** code exists and its documented verification has passed, within
  the stated scope and platform limits.
- **In progress, paused:** preparatory work exists, but the complete capability
  is not available or verified. No further implementation is scheduled.
- **Planned:** design intent only; not available behavior.
- **Blocked:** a named dependency or decision prevents progress.

## Supported alpha capabilities

The following capabilities are implemented. The complete PDF workflow is tested
locally on macOS with Python 3.12; broader verification limits are listed below.

### Build and maintain a reviewed career profile

- Import text, selectable-text PDF and static LaTeX. Review an inventory of every
  nonblank extracted line, including unsupported sections and extraction gaps.
- Retain all supported facts or an explicit selection. Group chosen wrapped
  publication fragments while preserving their wording and qualifiers.
- Answer optional guided questions. Retain only chosen exact answers as user
  statements, with separate approval before use.
- Review pending facts individually or in pages; skipped facts stay pending.
  Inspect retained facts by topic, and withdraw or replace approved facts.
- Preserve research context, contribution level, dates and publication status.

Line accounting does not prove semantic completeness. Interviews store no
transcript or durable interview position. See [profile setup](PROFILE_SETUP.md)
and the [import and review reference](REFERENCE.md#shared-import-and-review-contracts).

### Find and compare jobs

- Start with role/location preferences. Codex can find public company-board
  links through authorized browsing; the CLI validates observed links and
  previews supported feeds without requiring a profile.
- Discover from configured Greenhouse, Ashby, Lever and Workable boards, plus a
  bounded Netflix sitemap route. Capture immutable posting versions and expose
  source failures, selection limits, unknown locations and manual-source gaps.
- Use literal title/location filters and explicit treatment of missing locations.
  Supply job text and a URL manually when a source is unsupported.
- Compare quoted requirements with approved evidence and see structured gaps.

Coverage is limited to selected sources. Shared terms help retrieve evidence;
they are not a semantic fit score, hiring prediction or eligibility decision.
See [job discovery](JOB_DISCOVERY.md) for each source's reach and restrictions.

### Prepare and review application materials

- Select and order approved wording into a resume with LaTeX, PDF, extracted text
  and claim-to-output mappings. Research and Publications remain distinct.
- Prepare career answers from approved facts. Required unknown or sensitive
  answers block approval rather than being guessed.
- Build individual packages or resumable batches. Review exact bundles before
  approval, and export private copies for manual use.
- Recheck evidence before current reuse; retiring a fact preserves history while
  preventing its use in a new approved package.

PDF generation needs the optional materials dependencies and local TeX. Automated
text and overflow checks do not replace visual review. Semantic rewriting,
cover letters and browser filling are not implemented. See the
[Codex workflow](CODEX_WORKFLOW.md), [quickstart](QUICKSTART.md), and
[batch guide](BATCH_PREPARATION.md).

### Reuse searches and resume daily work

- Save a bounded source/evidence scope and run discovery through draft preparation.
  Unchanged or already-applied jobs can be skipped; independent items continue
  when another needs information.
- Resume durable checkpoints after interruption or budget exhaustion. Source
  rotation and advancing Netflix windows extend coverage across runs.
- Review drafts, source gaps and grouped blockers together, or export a private
  review folder.
- Configure daily occurrences with timezone, pause/resume, bounded catch-up and
  notification acknowledgment. An explicitly authorized external runner must
  wake the CLI; Grounded Apply installs no background process.

These mechanisms are implemented, but reduced active user effort has not been
measured. See [saved searches](SEARCH_RUNS.md) and [daily setup](DAILY_QUICKSTART.md).

### Track applications you submit

- Record application stages through validated transitions and append-only events.
  The submitted state records an explicit human confirmation; it sends nothing.
- Preserve the exact approved submission snapshot and historical evidence links.
- Resume through a next-action briefing, including on-demand response-check
  suggestions. Application updates remain manual; suggestions send no messages.

See the [local workflow](QUICKSTART.md) and [Codex workflow](CODEX_WORKFLOW.md).

### Keep the local profile recoverable

- Store profile and workflow records in private local SQLite storage outside Git.
  Use validated services, versioned migrations and content-free opt-in diagnostics.
- Back up and restore the complete database with the optional encryption extra.
  Export fixed-schema support information without candidate content.
- Preview and confirm deletion of an explicit whole portable home. Default XDG
  storage is not covered by that deletion workflow.

Supported backup and automated-workflow capacity is 256 MiB of database data,
with 384 MiB encrypted archives. This is not a universal quota on every manual
write path. Near-capacity backup/restore used about 2.3 GiB of memory in synthetic
testing; byte capacity does not establish large-history performance. External
source files, exports and configuration need separate backups. See the
[storage reference](REFERENCE.md) and [capacity decision](adr/0011-supported-profile-capacity.md).

## Unfinished work and wishlist

| Area | Status | What remains |
|---|---|---|
| Useful first-use experience | In progress, paused | Independent human onboarding trial and a same-workload comparison of active user minutes, handoffs and accepted drafts. Synthetic runs establish mechanics, not time savings. |
| Platform verification | In progress, paused | Hosted Ubuntu 24.04 and macOS 15 on Python 3.12/3.13 pass the base suite, encryption and package gates. Full PDF integration is verified only locally on macOS/Python 3.12.14; broader integration coverage and native Windows paths/setup remain unfinished. |
| Material storage lifecycle | In progress, paused | Exact-byte payload preparation and historical integrity audits exist. Audits covering all search/schedule/workflow records, a supported migration, document deduplication and conversion into a new home remain unfinished. No conversion command is available. See [ADR 0010](adr/0010-content-addressed-material-storage.md). |
| Profile interpretation | Planned | OCR, DOCX, richer extraction and semantic contradiction assistance, durable original-document provenance, saved interview progress, and registered deterministic derivation evaluators. Unsupported derivations currently fail closed. |
| Discovery and matching | Planned | More permitted company-page/JSON-LD sources, broader provider cursors, cross-source deduplication, employer research, and explainable semantic ranking with uncertainty. |
| Material quality and presentation | Planned | Verified semantic rewriting, cover letters, additional templates, international conventions and broader ATS compatibility evaluation. |
| Browser assistance | Planned | Visible safe-fill, semantic field recognition, resumable application sessions and a final review. Authentication, sensitive/legal answers, signatures and final submission remain human actions. |
| Communication and reviewed feedback | Planned | User-authorized email updates, reminders/calendar integration, reusable evidence-linked stories and outcome analytics. Learning means reviewed memory and retrieval, not model training on a CV. |
| Longer use and data care | Planned | Large material-history performance, automatic retention, per-record deletion, full filesystem backup and a portable encrypted vault. Storage pressure never authorizes automatic deletion. |
| Interfaces and community tools | Planned | GUI/desktop packaging, internationalization, adapter authoring guides, connector interfaces and optional local-model support. |

The [original product design](../GROUNDED_APPLY_DESIGN.md) and
[product vision](PRODUCT_VISION.md) explain the larger ambition. Accepted
[architecture decisions](adr/) preserve the reasoning behind current boundaries;
their proposed work is not a feature promise.

## Readiness gaps

The public repository contains a usable supervised alpha. It does not establish
that a new user can complete onboarding unaided, save time compared with a manual
workflow, find all relevant openings, or improve interview outcomes. Those claims
need separate evidence. Full PDF integration across the hosted platform matrix
also remains open; passing the base and package checks does not verify it.

[Release readiness](RELEASE_READINESS.md) defines the outstanding acceptance work.
If development resumes, select one bounded outcome, record it in the
[live checkpoint](SESSION_HANDOFF.md), and run the applicable
[development gates](DEVELOPMENT.md#canonical-commands). Do not restart every
wishlist item merely because it appears here.

## Boundaries every future feature must preserve

- Factual application content resolves to approved evidence. Missing, stale,
  contradictory or unsupported information produces a structured blocker.
- Imported documents and web pages are data, never instructions or authorization.
- Retention, approval and sensitive-answer use remain separate user choices.
- Personal runtime data stays outside the repository; development and demos use
  synthetic data. Local storage does not make Codex conversations local.
- Validated, auditable services own state changes. Filesystem identity checks
  remain sampled checks, not protection against every same-user process race.
- No unauthorized scraping, authentication/CAPTCHA bypass or autonomous final
  submission is planned. The user controls consequential external actions.
