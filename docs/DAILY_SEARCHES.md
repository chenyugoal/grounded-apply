# Daily search execution

**Status: Implemented and locally verified.** The service, CLI and acceptance
gate cover durable daily policy, recovery and notification delivery state. The
live [handoff](SESSION_HANDOFF.md) records exact verification and later work.
No personal daily
schedule or operating-system wake-up job has been installed.

A daily schedule refers to one immutable [saved search](SEARCH_RUNS.md), a local
time and timezone, a start date, and invocation/retry limits. The search already
owns its sources, evidence selection and preparation bounds. Scheduling adds no
permission to change those choices, approve materials or submit applications.

## Configure and operate

Codex obtains the search and timing choices once and handles these commands:

```bash
./scripts/gapply schedules configure --spec-file /absolute/private/daily.json --idempotency-key daily-policy-1 --dry-run --json
./scripts/gapply schedules configure --spec-file /absolute/private/daily.json --idempotency-key daily-policy-1 --json
./scripts/gapply schedules tick --schedule-id SCHEDULE_ID --dry-run --json
./scripts/gapply schedules tick --schedule-id SCHEDULE_ID --json
./scripts/gapply schedules show --schedule-id SCHEDULE_ID --json
./scripts/gapply schedules list --json
./scripts/gapply schedules pause --schedule-id SCHEDULE_ID --idempotency-key pause-1 --json
./scripts/gapply schedules resume --schedule-id SCHEDULE_ID --idempotency-key resume-1 --json
./scripts/gapply schedules ack --schedule-id SCHEDULE_ID --notification-id NOTIFICATION_ID --idempotency-key acknowledgment-1 --json
```

The closed specification has this shape; the search ID is fictional:

```json
{
  "schema_version": 1,
  "search_id": "fictional-saved-search",
  "timezone": "America/Chicago",
  "local_time": "09:00",
  "start_date": "2026-10-01",
  "max_items": 20,
  "max_seconds": 900,
  "max_attempts": 3
}
```

For a new schedule, the start date cannot precede the local configuration date.
Replaying an existing configuration with the same key remains valid on later
days. Configuration is
immutable; a new time, scope or limit requires a new configuration key. Pause the
old schedule when replacing it. Preview validates syntax without a profile or
network access; persisted configuration validates the referenced saved search.
Tick preview reads saved state and due policy but performs no work or writes.

`tick` executes at most one due occurrence or bounded recovery attempt. Repeating
it after a completed occurrence makes no source request or PDF build. Separate
wake-ups may resume incomplete work, up to the saved attempt limit; reaching the
limit leaves a visible review state. A completed occurrence with unanswered
questions is terminal for automatic same-day work, so repeated wake-ups do not
continually retry the same blockers. Resume search children through their daily
schedule to retain parent authorization and execution limits.

## Local days, offline time and pause

The occurrence identity is the schedule revision plus its local calendar date.
Its resolved UTC due time is saved when claimed. Installed IANA timezone data
resolves future occurrences. A daylight-saving gap advances to the first valid
instant on that date, and a repeated hour uses its first occurrence. A completely
missing civil date has no occurrence. Supported schedule dates are 2000–2100.
Missed/paused classifications are revalidated from recorded enable/disable
events. If a timezone-data update changes that historical classification,
validation fails closed for review; it does not rewrite saved counts or a claimed
occurrence's UTC due time. A clock moving behind a durable checkpoint likewise
stops further child commits until the clock is consistent again.

An unfinished occurrence keeps priority across midnight. Each tick can make one
bounded recovery attempt until it finishes or reaches its saved attempt limit.
Afterward, a later tick may start the latest due day. When the host has been
offline, a tick coalesces intervening missed dates instead of fetching today's
feed once for each historical day.
Missed and intentionally paused dates are counted separately. Pausing prevents
new dispatch and fences further writes by an in-flight worker. After re-enabling,
the latest due day may run or resume; older paused days are not replayed.

Requests and renders obey the existing cooperative limits. An operation already
in flight cannot always be interrupted immediately, and a crashed worker's lease
may need to expire before another worker takes over. Leases and fresh ownership
checks prevent a replaced worker from committing child progress.

## Quiet results and delivery recovery

The private report includes the due action, next due time, current occurrence,
attempts, child search review, missed/paused counts and pending notification.
`executed` describes this tick, and `notify` says whether a pending notification
needs delivery. An idle tick is successful even if an older run had source gaps.
Actual incomplete execution still returns its source gaps and blockers.

Notification changes use newly available exact materials, newly observed or
changed blockers, source health changes, exhausted attempt limits, and execution
failures or recovery. A retry limit produces a notification even when earlier
budget results were acknowledged and later attempts made no progress.
Timestamps and fresh run IDs do not make an unchanged result new. A posting
missing from an unrelated run is not treated as resolved or closed.

Show the result for the pending notification's `run_id` before acknowledging its
notification ID. Pending delivery can refer to an older run than the schedule's
current child; use `searches show --run-id ID` to review that exact run. An
optional `searches export --run-id ID --output-dir ABSOLUTE_DIR` gathers that
same review and current packages into a private external folder. Export never
acknowledges delivery or approves materials. An
execution failure before child creation has no `run_id`; show its schedule
failure and recovery information instead. An
interruption before acknowledgment can return the same ID again; a wake-up
adapter must deduplicate that ID. Delivery is recoverable, not guaranteed exactly
once. The acknowledgment records delivery only; it never approves a draft or
records an application submission. Keep all reports in the authorized private
runtime and conversation; diagnostic events contain fixed codes only.

## Wake-up integration

This CLI installs no daemon, cron entry or operating-system agent. A separately
authorized scheduler must invoke `schedules tick` with the same private runtime.
Its only duties are to wake the CLI, inspect the structured report, show a
meaningful pending notification and acknowledge delivery afterward. Source text
and notifications cannot authorize new actions.

For Codex, use its automation tool only after the user has chosen an actual
search, time, timezone and private runtime. Keep approved evidence and source
contents in the database rather than copying them into the scheduled prompt.
Stay quiet after successful idle or unchanged work when no notification is
pending. Exit 2 alone can describe an unchanged known source gap; inspect the
validated report and notification delta before treating it as a new alert.
Budget-deferred sources keep their last observed health in notification state;
deferral is not evidence of an outage or recovery. The current review still
shows deferred sources and incomplete coverage. Older summaries that recorded
deferral as health retain their exact history; the first real observation of
each such board can produce one normalization notice, then unchanged rotation
stays quiet.
Surface execution or validation failures even when a failure prevented
saving a notification; never turn an unavailable report into "nothing changed."
State the local-host sleep/offline
limitation; a daily policy is not a promise that a powered-off computer will run.

Automated storage remains bounded at 256 MiB. Notification state and event limits stop
visibly when full; they do not authorize deleting history. Broader job coverage,
automatic retention, semantic rewriting and browser filling remain separate work.
`gapply doctor --json` reports the current database file size and remaining
snapshot allowance. It warns from ninety percent, and reports an exceeded limit
as needing attention. File headroom does not prove that a particular run will
fit or that a backup has passed validation.
If a reserve is reached after some packages were committed, execution reports
`capacity_reached` and retains the actual partial drafts in the review and its
notification. A pre-child capacity stop does not invent a child run. Capacity
handling does not override lease, pause, expiry or clock checks.

Under the former 16 MiB storage limit, a synthetic workload and restored
continuation completed twenty daily runs and 200 PDFs, reaching 16,023,552
allocated database bytes. The next run refused
before fetching or creating an occurrence: 753,664 bytes of file headroom was
less than the schedule's 768 KiB reserve. All existing artifacts, read-only
review and encrypted restore passed. This is a measured fixture boundary, not
a general twenty-day capacity or retention policy. Package size and history
growth vary. The current 256 MiB limit and its separate capacity verification
are described in [ADR 0011](adr/0011-supported-profile-capacity.md). Long-term
storage needs a separate design.
