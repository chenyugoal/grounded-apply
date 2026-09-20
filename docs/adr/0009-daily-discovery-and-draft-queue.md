# ADR 0009: Daily discovery and a draft review queue

- **Status:** Accepted direction; staged implementation in progress
- **Date:** 2026-09-19
- **Related:** ADRs 0001, 0006, 0007 and 0008; design sections 11, 22 and 23

## Problem and outcome

The current application pilot requires the user to supply a job, participate in
preparation, wait for a bundle, and then fill the application. Repeating this
cycle can consume as much attention as a conventional search. Correct artifacts
and reusable facts are necessary, but do not establish a productivity benefit.
The reported preparation delay has not been profiled; it is not evidence that
any particular model or renderer is the bottleneck.

The next product milestone is **daily job discovery plus prepared application
packages**. A user sets a bounded search and preparation scope once. A run finds
new openings within that scope, prepares drafts for selected candidates, and
returns one review queue with grouped exceptions. A blocked item does not stop
unrelated preparation. Long unattended wall time is useful only when it reduces
active user time and produces relevant, reviewable work.

The full milestone remains unfinished. A bounded multi-source discovery and
capture increment, durable saved-job batches and on-demand discovery-to-draft
runs and daily execution are implemented and locally verified;
no personal daily job-search schedule is installed.
Browser filling follows this milestone. Existing
profile, material and submission approval contracts continue to apply.

## Proposed run contract

1. Store a versioned search specification in the explicitly authorized private
   runtime through a validated service: allowed sources/company boards, role and
   location preferences, exclusions, preparation policy, and per-run limits.
   Record which constraints the user supplied. Unknown eligibility remains
   unknown; search preferences cannot authorize inference or sensitive reuse.
2. Bound work by requests/bytes, sources, new jobs, drafts, elapsed time, storage
   growth, and model calls/cost where applicable. Persist progress when a limit
   is reached. Use bounded retries and backoff, and prevent overlapping runs for
   the same search. Reserve capacity before writes so supported backup/deletion
   bounds remain usable; the current database limit is 16 MiB and automatic
   retention is unfinished. Capacity exhaustion is a visible blocker, not
   permission to delete history or silently disable backup.
3. Discover through narrow read-only source adapters. Begin with official
   Greenhouse, Ashby and Lever public feeds over explicit company boards. This is coverage
   of configured boards, not internet-wide discovery. Broader source discovery
   and additional ATS adapters are later increments.
4. Keep stable source/external IDs, retrieval time, source health and immutable
   posting versions. Unchanged fetches do not create duplicate jobs or drafts;
   changed content creates a new version without rewriting prior submissions.
   Skip already-applied or explicitly dismissed identities under saved policy.
   Do not mark a job closed because a source request failed or a partial listing
   omitted it. Keep cross-source ambiguity for review instead of over-merging.
5. Filter explicit mismatches first. Present evidence-backed relevance with
   quoted requirements, approved claim references and uncertainties. Keyword
   overlap is retrieval, not qualification or hiring probability. Keep uncertain
   matches visible in a separate review group rather than silently rejecting
   them or fabricating a hard-constraint answer.
6. Reuse approved wording and user-authorized role/evidence selection preferences.
   Codex may propose ordered claim IDs and presentation choices inside that
   scope; deterministic services validate each packet and build the artifact.
   A restricted evidence-selection manifest is not permission to add prose or
   facts. Preserve employer/date/bullet associations. Do not require the user to
   select the same evidence again for every ordinary draft.
7. Produce a resume, grounded answers to available questions, a relevance/gap
   note and application link for each successful item. If the form's questions
   have not been retrieved, say so; an absent questionnaire is not an answered
   questionnaire. Required unknowns remain blockers. Preserve useful partial
   drafts while clearly identifying what is missing. Validate and visually
   inspect every PDF before describing a package as reviewable.
8. Return one private review queue: prepared drafts, items needing information,
   skipped/unchanged jobs, remaining queued work and source failures. Group
   related questions without treating one answer as permission for wider reuse.
   Exact bundle approvals may be presented together, but each still binds its
   own digest. Preparation never approves facts/materials or records submission.
9. Add a daily trigger only after the same bounded run works on demand. The
   trigger invokes the normal service contract, preserves durable checkpoints,
   and reports meaningful new results, failures or required user action. Do not
   send repeated unchanged status notifications. A sleeping/offline host yields
   a visible missed/deferred run with bounded catch-up, not a claim of 24/7 work.

## Persistence and service boundaries

Compose `JobService`, `MatchingService`, `MaterialService` and validated profile
access. Extend briefing with run/item status rather than maintaining a second
Markdown dashboard or copying the profile into a scheduler prompt. External
sources receive no candidate facts. Model context contains only the relevant
approved evidence and untrusted job input needed for that item.

The existing `services/workflow.py` helpers validate completed, atomic
validate/persist operations. Their presence does not establish resumability of
an hours-long run. Add a separate typed run/item checkpoint contract, repository
methods, migrations and tests. Keep completed-operation replay compatible.

Each run item needs stable identity bound to the search revision, posting version,
preparation inputs and child operation keys. Reusable material identity separately
depends on the posting version, selected current evidence/provenance, questions,
layout and generator versions. An unrelated search preference change must not
force regeneration of identical validated content. Store stage, attempts, artifact
references and structured blockers. Recovery must reconcile a child operation
that committed before its parent checkpoint. Recheck evidence before reuse and
before readiness; retired or changed claims invalidate current use. A source
outage or one job's NeedInfo can be isolated. Database integrity, privacy or
authorization failures stop affected shared work rather than being swallowed
as ordinary job errors. Use short transactions; do not keep SQLite locked during
network requests, model work or PDF rendering.

Network adapters require bounded responses/timeouts, schema validation and
explicit destination/redirect rules. Job text cannot supply tool instructions,
expand allowed sources or authorize disclosure. Preserve the existing private
runtime, read-only sidecar/WAL and sampled TOCTOU contracts. Any new diagnostic
events need registered content-free enums; private review content stays out of
logs. Final submission, authentication and sensitive/legal answers retain the
existing human gates.

## Implementation sequence

The September 19 coverage request changes the initial ordering: implement the
multi-source discovery/capture prerequisite first, then the durable batch below.
The source increment uses existing immutable snapshot/workflow tables with a new
registered `job_discovery_capture` operation; this needs no schema change. Its
per-posting replay does not implement the longer run/item checkpoint contract.
Per-run coverage reports are returned, not retained as historical source health.
Dedicated FAANG sources, official alert ingestion and supplemental search remain
separate increments; supported ATS feeds alone do not establish broad coverage.

1. **Multi-source discovery (bounded slice implemented):** configured
   Greenhouse/Ashby/Lever boards and a bounded published Netflix sitemap route,
   source identity/versioning, bounded read-only
   fetches, per-source deduplication, explicit title filters and coverage reports.
   Manual capture and old snapshots retain their provenance. Canonical hosted
   ATS links derive from validated provider/board/posting IDs; adapters never
   fetch returned links or change the manual-capture URL validator. Netflix
   saved-search cursors and daily notification source-health history are now
   implemented; broader provider cursors and cross-source aliases remain planned.
2. **Batch drafts for saved jobs (implemented):** typed batch input, durable per-item
   progress, bounded execution, idempotent recovery, grouped blockers and
   consolidated review. Reuse material services and update the conversational
   workflow to accept one preparation scope. Schema 005 adds immutable requests,
   append-only checkpoints and expiring fenced leases. The ten-job real-PDF
   acceptance and final full/installed checks passed and are recorded in the handoff.
   This remains a prerequisite, not completion of daily discovery.
3. **Discovery to drafts (implemented):** schema 006 binds a saved source/evidence
   scope to durable source checkpoints and a deterministic child batch. Selection
   excludes validated historical submissions, explicit identities and unchanged
   current drafts before its cap. Source rotation is explicitly not fit ranking.
   The configured-search/installed acceptance passed with sixteen real PDFs,
   isolated blockers, replay/change/recovery and encrypted restoration.
4. **Daily execution (implemented locally):** schema 007 saves time/timezone,
   occurrences, bounded attempts and delivery acknowledgment. CLI and installed
   acceptance verify real PDFs, quiet unchanged work, offline/pause recovery and
   encrypted restoration; core regressions cover overlap and interruption.
   An external wake-up mechanism is authorized separately. Scheduling a chat
   alone is not this acceptance gate.
5. **Later browser assistance:** fill permitted fields from reviewed packages
   with resumable per-application state and the existing mandatory human stops.

## Acceptance evidence required before calling it implemented

- One synthetic kickoff containing ten eligible jobs produces eight validated
  draft packages and two deliberately blocked items in one review queue, with
  no intermediate user response needed for the eight independent jobs.
- Repeat that outcome end to end from a configured watchlist through the real
  adapter using a synthetic HTTP transport. The user supplies the search scope,
  not individual job text or claim selections for every posting. Passing only
  the saved-job batch fixture does not complete this milestone.
- Fixtures cover duplicates, changed postings, explicit mismatches, ambiguous
  requirements, prompt injection, missing form questions and partial source
  outages. An outage must neither invent a closure nor erase queued work.
- Kill and resume after discovery, after a material commit, and before parent
  checkpoint persistence. Retries create no duplicate jobs, material versions
  or application events. Changed inputs cannot silently reuse stale output.
- Budget exhaustion checkpoints remaining work. Overlapping triggers do not
  execute the same item twice. Offline and unchanged runs report their real
  outcome. Retired evidence blocks current reuse; unsupported facts never render.
- Verify real PDFs, explicit per-bundle approval, and zero external submission
  actions. Existing full, encryption, pilot and installed-package gates pass.
- Compare the serial and batch workflows on the same bounded workload. Record
  active user minutes, required handoffs, accepted/rejected drafts, source
  coverage, elapsed runtime and cost separately. The release target is one
  kickoff plus one consolidated review and lower active time per accepted pack;
  numerical speedup remains unmeasured until the pilot. Keep personal pilot data
  outside the repository.

## Source choice evidence

The official [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html)
documentation, checked September 19, 2026, describes unauthenticated board-scoped
GET endpoints, descriptions via `content=true`, stable posting IDs, and optional
application fields via a job request with `questions=true`. This makes one board
a useful first source-to-package pilot. It requires configured board tokens;
it is not market-wide search. Source content and field labels remain untrusted,
and publicly visible sensitive questions still require human answers. The
adapter scope is read-only; application submission endpoints are excluded.

No new CLI spelling or schema number is canonical until its implementation and
documented checks pass. [JOB_DISCOVERY.md](../JOB_DISCOVERY.md) documents the bounded
source increment; [BATCH_PREPARATION.md](../BATCH_PREPARATION.md) documents saved-job
batch preparation. [SEARCH_RUNS.md](../SEARCH_RUNS.md) documents the verified
combined search-to-package runner. Daily execution is locally verified; see
[DAILY_SEARCHES.md](../DAILY_SEARCHES.md) for its contract and the live handoff for
verification.
