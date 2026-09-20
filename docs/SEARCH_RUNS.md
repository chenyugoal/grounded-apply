# Discover jobs into a preparation queue

**Status: Implemented and locally verified.** The original on-demand increment
passed the full repository suite and fresh installed-package gate, including
configured feeds through sixteen real PDFs and encrypted restoration. Netflix
advancing windows pass the 659-test repository checkpoint, fresh installed-package
gate and a separate nine-PDF, encrypted-restore gate. See
[SESSION_HANDOFF.md](SESSION_HANDOFF.md) for exact verification.
No personal
search scope or daily job-search schedule has been configured.

A saved scope combines public sources, explicit title filters, exclusions,
location-text preferences, approved-evidence selections and budgets. A run discovers within that scope,
captures immutable job versions and prepares a durable batch. One review shows
source coverage, selected jobs, skipped identities, drafts and grouped blockers.
Preparation never approves facts/materials or submits an application.

## Configure once and run on demand

```bash
./scripts/gapply searches configure --spec-file /absolute/private/search.json --idempotency-key search-scope-1 --dry-run --json
./scripts/gapply searches configure --spec-file /absolute/private/search.json --idempotency-key search-scope-1 --json
./scripts/gapply searches run --search-id SEARCH_ID --idempotency-key search-run-1 --max-items 20 --max-seconds 900 --json
./scripts/gapply searches resume --run-id RUN_ID --max-items 20 --max-seconds 900 --json
./scripts/gapply searches show --run-id RUN_ID --json
./scripts/gapply searches list --search-id SEARCH_ID --json
./scripts/gapply searches scopes --json
./scripts/gapply searches export --run-id RUN_ID --output-dir /absolute/private/new-review --dry-run --json
./scripts/gapply searches export --run-id RUN_ID --output-dir /absolute/private/new-review --json
```

Codex creates the specification under one agreed search/preparation scope and
handles commands and IDs. `--spec-file -` reads bounded stdin. Configure preview
validates syntax without opening a profile, fetching sources or rendering PDFs.
It does not prove claim eligibility. Real configuration needs an initialized
private runtime; each job's evidence is validated when preparing its material.

A specification is immutable. Retry the same request/key after uncertain output.
Changing the scope requires a new configuration key and creates a separate saved
revision. Repeat a run key to recover that run; use a new run key for a new
discovery round. Keep the same authorized `GROUNDED_APPLY_HOME`.

This example uses fictional sources and claim identifiers:

```json
{
  "schema_version": 2,
  "sources": [
    {"id": "fictional-research", "provider": "greenhouse", "board": "fictional-lab"},
    {"id": "fictional-engineering", "provider": "ashby", "board": "fictional-systems"},
    {"id": "fictional-manual", "provider": "manual", "careers_url": "https://example.com/careers"}
  ],
  "title_contains": ["research engineer", "software engineer"],
  "preparation_filters": {
    "title_excludes": ["director"],
    "location_contains": ["Preferred City", "Remote"],
    "location_excludes": ["Excluded Region"],
    "missing_location": "include"
  },
  "claim_ids": ["fictional-employment", "fictional-python", "fictional-degree"],
  "layout": {"schema_version": 1, "presentations": {"fictional-employment": "heading"}},
  "questions": [],
  "questionnaire_coverage": "unknown",
  "excluded_identities": [
    {"kind": "discovery", "provider": "greenhouse", "board": "fictional-lab", "external_id": "999"}
  ],
  "max_jobs": 10,
  "max_requests": 64,
  "max_bytes": 67108864
}
```

Sources use the existing [closed source contract](JOB_DISCOVERY.md). The shared
preparation fields use [batch preparation](BATCH_PREPARATION.md). There are no
freeform resume claims or inferred sensitive answers. `title_contains` is a
case-insensitive substring OR filter. It is not semantic matching, eligibility
screening or an employer ranking model. Unknown application questions remain
explicitly unknown. Source text and question labels are untrusted data.

Version 2 adds optional `preparation_filters`; omitted lists default to empty
and `missing_location` defaults to `include`. Existing version-1 scopes remain
valid with their exact stored bytes and replay behavior. To change a saved scope,
configure a new one with a new key; its daily schedule must bind that new scope.

These are literal, Unicode-casefold substring preferences over fetched posting
metadata. `title_excludes` is checked first. For a published location,
`location_excludes` wins; otherwise any `location_contains` alternative matches,
or an empty include list accepts it. Each list holds at most twenty trimmed
terms of one to 128 characters without control characters. Location strings are
not geocoded, translated or interpreted as workplace or immigration eligibility.
For example, `Remote` also matches `Not Remote`; use explicit exclusions and
review the published wording. A city abbreviation or different employer wording
can produce a nonmatch.

Missing location remains `null` in the review. The default includes that posting
despite location-text rules, preserving an opportunity for human review. Choosing
`missing_location: exclude` explicitly skips it. Fixed counters distinguish
`title_excluded`, `location_excluded`, `location_not_matched` and
`location_unknown_excluded`. Filtered postings consume no local capture or
preparation slot. These rules operate after the source adapter's bounded reads
and returned-candidate cap; they do not extend source coverage. The saved job's
metadata is revalidated against the immutable filter before preparation commits.

An exclusion identifies either an exact provider/board/posting, or a `kind: url`
entry with `source_url`. Cross-source aliases are not guessed. Recorded historical
submission excludes the same identity even if its current application state is
rejected or withdrawn. An unsubmitted application is not automatically excluded.
At most 500 explicit exclusions are accepted. History review is bounded at 1,000
applications and 10,000 events; exceeding that bound stops shared work visibly
rather than treating a partial history scan as eligibility.

## Review and recovery

### One review folder

`searches export` copies one exact run into a new private directory. The export
and compact overview passed the 781-test full gate and fresh installed-package
workflow checks. The folder contains `review.md`, a complete structured
`review.json`, a hash receipt, and one directory per exported job with its PDF, answers and
evidence manifests. The index shows job links, published location, source gaps,
grouped blockers and approval requirements, with direct file links in a compact
job table. It can also describe a run with no prepared packages.

Before writing anything, the command validates the run history and current facts
in one read-only database snapshot, then checks all output sizes. Stale packages
remain listed with blockers but their files are omitted. Current partial packages
retain unresolved questions; unknown questionnaire coverage stays unknown.
Export creates no approval, application or submission. Its success exit code
means the copy succeeded, even when the review contains source gaps or blockers.

The destination must have an existing private parent and be outside Git
worktrees and every managed runtime directory. Directories are private and files
are exclusive, single-link private copies. A dry run validates without creating
files. Retrying the same unchanged snapshot at the same destination compares
every byte and file; changed, extra or missing contents are refused without
overwriting. After interrupted creation, inspect the partial folder and choose a
new destination. A changed fact, approval or run state likewise needs a new
folder; previous copies are not synchronized or a current-readiness authority.

At most fifty packages and 403 files are supported, with a 32 MiB total, 8 MiB
per index and 2 MiB per material file. Oversized output fails before creation.
The checks use sampled file identities, not protection against a same-UID process
winning a later filesystem race. External copies remain caller-owned and are
outside runtime backup and deletion. For a daily notification, export its exact
`run_id`, which can differ from the schedule's latest child.

### Resume saved work

The private report contains the saved scope/run IDs, source checkpoints,
selection, skip reasons, counters, child batch, items, grouped blockers and
coverage status. Its `counts` contains total/queued/building/draft/blocked.
Version-2 selections also retain each employer's published `location` or `null`.
`application_ready` stays false. Export and visually review each PDF and its
answers, then use normal exact-bundle approval where appropriate.

Source checkpoints distinguish pending/fetching/complete/deferred work. The
embedded source report separately distinguishes successful, partial, failed and
manual-required coverage. An empty successful source is different from an outage.
`coverage_complete` is false when any source is incomplete, failed or manual.
Failures never imply closure or delete saved jobs.

Run/resume exits 2 for incomplete coverage, remaining work or a shared failure;
it can still return useful drafts. With complete coverage, only item blockers
produce exit 3. A complete unblocked run exits 0. Read-only show/list/scopes exit
0 for a validated read regardless of the inspected run's state.
An overlapping invocation returns `stop_reason: lease_active`; it does not take
over the current owner or report that owner's work as failed.

Source capture and its checkpoint commit atomically. A stopped run retains prior
source checkpoints; retry refetches an unfinished source. An uncommitted remote
response is not preserved. Network requests run with the runtime closed.
Rendering runs outside write transactions. Parent and child leases prevent stale
invocations from committing after another owner resumes. Search-owned batches
must resume through their parent search so eligibility and budgets are retained.

Unchanged current drafts already handled under the same scope are skipped before
the final preparation cap. Changed postings create new immutable job versions;
changed scope can prepare an existing posting. Final selection is frozen and
distributed in rounds across configured sources, then ordered deterministically
within a source. New source captures prioritize exact posting versions with
fewer previous preparation attempts in the same saved scope, preserving adapter
order for ties. This lets later unattempted postings pass repeated blockers;
blockers remain in their original review and can be attempted again. Changed
posting versions start with no previous attempts. Resuming an existing run keeps
its already frozen selection. This ordering does not represent fit or hiring
probability.

New runs also rotate automatic sources using `source_rotation@1`. The same
reserved order controls fetching, candidate quotas and final round-robin
selection. Manual coverage gaps follow the automatic sources. Even with a
one-request/one-job limit, later boards therefore get the first turn in later
runs. All ordering still operates within source and candidate-window bounds;
it does not guarantee complete coverage or rank job fit.

The first successful leased start reserves one generation for the saved scope.
Retries keep that reservation, and an overlapping attempt that cannot obtain a
lease consumes none. Existing started legacy runs retain their original order
and checkpoint bytes. New reports expose `source_order` and derived
`source_priority`; the `sources` array remains in manifest order. A terminal
request/byte budget reason stays visible when replay still has deferred sources.

## Netflix windows across runs

A saved Netflix source uses policy `netflix_head1_tail6@1`: each new discovery
round refreshes the newest advertised posting, then reads at most six entries
after its saved tail position. The ten-request source limit includes robots,
sitemap index and job sitemap reads. Standalone `jobs discover` keeps its first
seven sample; this advancing behavior belongs to saved searches, including runs
started by a daily schedule.

The cursor records a normalized advertised `lastmod` and numeric posting ID,
ordered by newest date then ascending ID. It never contains a fetch URL. Every
run takes URLs from the current permitted sitemap. Reordering or deleting the
previous anchor does not restart a useful cursor. Reaching the tail clears it
for a later cycle; if sitemap changes leave nothing after the cursor, traversal
restarts from the current beginning. New or changed entries ahead of the cursor
may wait for that cycle unless they are the refreshed newest entry.

Title-filtered entries, 404s, malformed details and isolated timeout/transport/
oversize failures consume their bounded attempt so one posting cannot starve
later jobs. Failures remain in the source report and can be revisited next cycle.
Robots or page restrictions, authentication refusals, redirects and rate limits
stop without advancing past that entry. A parent request-budget refusal does the
same and does not increase the observed-detail count. Failure before reading a
valid inventory leaves the cursor unchanged.

The run freezes its input cursor and generation before network work. An
interrupted, uncommitted source retries that same input and retains request/byte
charges. Captures and output progress commit together. Repeating a completed run
key returns its saved result; use a new key to start the next window. A new run
takes the highest completed generation available when its Netflix source starts.
An older interrupted run finishing late cannot overwrite newer progress. Progress belongs
to the exact saved scope, survives encrypted restoration, and starts afresh for a
new scope. Existing checkpoints remain readable; this adds no SQL migration.

The private source checkpoint exposes `window`, including its policy, generation,
input `after`, and completed `progress` (`next`, `consumed_tail_count`,
`cycle_complete`, `reset`, `indexed_count`). The embedded source report keeps its
existing shape. Its indexed/observed/remaining counts describe the current
invocation, not cumulative completeness. Finishing a cycle is not proof that
every current posting was searched: the sitemap can change, individual reads can
fail, and the source and preparation budgets still apply.

## Bounds and limits

The default scope selects at most ten jobs; the hard maximum is fifty. Candidate
capture is bounded separately at the smaller of 200 and four times `max_jobs`.
Source windows share this candidate allowance. Existing adapter/source/response
caps still apply, including at most 1,000 returned candidates per source and
Netflix's bounded seven-detail window. Traversal can advance through details
that the final preparation cap does not select; it does not promise a draft for
every matching posting. The runner does not claim unread postings were searched.

The scope defaults to 64 public requests and 64 MiB of response bytes, with hard
maximums of 320 requests and 256 MiB. Request and response-byte reservations are
saved before dispatch; unused byte reservations are refunded after success.
A failed request or crash conservatively consumes its full byte reservation
because the transport cannot prove how much was received. Invocation
time and draft-attempt limits are cooperative; an in-flight render or DNS lookup
is not hard-preempted. Storage remains within the 256 MiB supported database cap
with checkpoint headroom. Limits do not authorize automatic deletion.
Migration 006 adds saved-search records. An upgrade that cannot fit under the
storage cap rolls back that migration and retains a restorable prior schema.

[Daily schedules](DAILY_SEARCHES.md) add bounded offline catch-up, same-day replay
and notification acknowledgment around this saved scope. The product does not
install a wake-up mechanism or personal schedule automatically.
Broader employer coverage, semantic ranking, per-job question discovery, browser
filling and automatic retention remain unfinished.

## Netflix traversal verification

The synthetic [acceptance gate](../scripts/check_source_window.py) passed with
nine real PDFs and encrypted restoration. Its fifteen-entry sitemap first yields
seven filtered-out details, then six useful drafts, then two; after cycling,
a changed newest posting yields one new draft. Exact run replay makes no extra
requests or PDFs, each round stays within ten requests, and every observation
retains its partial-coverage disclosure. All nine PDF pages passed visual review.
The search runs record no applications or new fact/material approvals; fixture
setup explicitly approves fictional profile facts.

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_source_window.py --with-backup
```

Use the materials and backup setup in [DEVELOPMENT.md](DEVELOPMENT.md). This gate
does not measure active-user-time savings or prove completeness on the live site.

## Preparation-filter verification

The [filter gate](../scripts/check_search_filters.py) uses five fictional postings
with excluded titles, mismatched or missing locations ahead of a later matching
job. A one-job budget reaches the matching job; a separate default-include scope
retains the unknown location. It checks two real PDFs, exact replay, unchanged
read-only database bytes, rejected same-key scope changes and encrypted restore.
Both pages passed visual review. Search execution records no new fact/material
approvals or applications; fixture setup explicitly approves fictional facts.

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_search_filters.py --with-backup
```

## Source-rotation verification

The [rotation gate](../scripts/check_source_rotation.py) runs three daily rounds
with three automatic boards, two manual coverage gaps and only one request and
one preparation slot per day. The first board has a preparation blocker; the
next two still get their turn and produce two real PDFs. Encrypted restore after
day two preserves the third board's priority. Same-day replay performs no new
requests or renders, and both exported pages passed visual review.
Three further unchanged days verify quiet notification delivery after earlier
notices have been acknowledged, despite the alternating deferred-source reports.

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_source_rotation.py --with-backup
```

This verifies bounded scheduling fairness with fictional responses, not live
coverage, fit ranking or measured active-user-time savings.
