# Discover jobs from public boards

**Status: Implemented and locally verified.** The existing multi-source CLI,
adapters and idempotent capture passed synthetic and installed-package checks.
Netflix saved-search traversal passes the 659-test repository checkpoint,
fresh installed-package gate and its nine-PDF, encrypted-restore acceptance
gate. Live read-only checks also passed for
Anthropic, OpenAI and bounded Netflix discovery on September 19, 2026; Lever was
verified with synthetic responses only. The complete discovery milestone
remains unfinished. See
[SESSION_HANDOFF.md](SESSION_HANDOFF.md) for verification evidence.

Discovery reads configured Greenhouse, Ashby, Lever and Lever EU public boards,
plus a bounded Netflix sitemap and structured-posting route.
It does not guarantee coverage of most companies, search the entire internet,
rank candidate fit, retrieve application questions or prepare materials.

## Run a preview or capture

```bash
./scripts/gapply jobs discover --preset major-tech --title-contains research --title-contains engineer --limit-per-source 100 --dry-run --json
./scripts/gapply jobs discover --sources-file /private/tmp/gapply-discovery-sources.json --title-contains research --json
```

Choose exactly one of `--preset major-tech` and `--sources-file FILE`; `-` reads
the manifest from stdin. `--dry-run` performs network reads but never opens or
writes the profile database. Without it, discovery validates the already
initialized, authorized private runtime before fetching, then saves selected
snapshots through `JobService`. Preserve the user's `GROUNDED_APPLY_HOME`.

Repeated `--title-contains` terms are case-insensitive substring **OR** filters
over titles, applied locally. No term means all titles. Up to 20 terms of 128
characters are accepted. `--limit-per-source` defaults to 100 and accepts 1–1000;
selection follows external posting ID order, not relevance. Truncation is reported
as incomplete coverage rather than silently called a complete search.
Repeating this standalone command reuses the same capped subset; it does not
advance through remaining matches. Raise the limit or use explicit title filters
to change a feed's subset. Netflix details have a separate seven-page limit;
[saved searches](SEARCH_RUNS.md#netflix-windows-across-runs) advance through that
inventory across runs.

A source manifest has exactly `schema_version` and `sources`. This synthetic
example shows the shapes; its fictional boards are not live integrations:

```json
{
  "schema_version": 1,
  "sources": [
    {"id": "fictional-research", "provider": "greenhouse", "board": "fictional-lab"},
    {"id": "fictional-systems", "provider": "ashby", "board": "fictional-systems"},
    {"id": "fictional-tools", "provider": "lever", "board": "fictional-tools"},
    {"id": "fictional-europe", "provider": "lever_eu", "board": "fictional-europe"},
    {"id": "fictional-manual", "provider": "manual", "careers_url": "https://example.com/careers"}
  ]
}
```

Use 1–32 sources, unique opaque IDs and unique provider/board routes. ATS entries
accept only `id`, `provider`, `board`; manual entries accept only `id`, `provider`,
`careers_url`. Board names contain letters, digits, underscores or hyphens, start
with a letter/digit and have at most 128 characters. A manual URL must be HTTPS
without credentials, an explicit port, query or fragment. It is displayed, never
fetched. The `netflix` provider uses the fixed board `netflix`; no arbitrary
career-page endpoint is accepted. A manifest cannot introduce network destinations.

## Coverage of the `major-tech` preset

The preset is a public route catalog, not a stored personal watchlist. Only two
entries use ATS feeds, Netflix uses its published sitemap, and the other four
return `manual_required` with a link.

| Employer | Preset route | Official careers page |
|---|---|---|
| Anthropic | Greenhouse board `anthropic` | [Jobs](https://www.anthropic.com/careers/jobs) |
| OpenAI | Ashby board `openai` | [Search](https://openai.com/careers/search/) |
| Google | Manual | [Search](https://www.google.com/about/careers/applications/jobs/results/) |
| Apple | Manual | [Search](https://jobs.apple.com/en-us/search) |
| Amazon | Manual | [Search](https://www.amazon.jobs/en/search) |
| Netflix | Bounded sitemap/JobPosting reads; partial coverage | [Careers](https://explore.jobs.netflix.net/careers) |
| Meta | Manual | [Careers](https://www.metacareers.com/) |

Google also offers [official email alerts](https://support.google.com/googlecareers/answer/6095419?hl=en).
Those alerts and the four manual searches are user-operated; this command does
not create alerts, scrape those sites or treat an unsupported route as zero jobs.
Add verified public board names through a manifest to extend feed coverage.
The adapter contracts come from the official [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html),
[Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)
and [Lever Postings API](https://github.com/lever/postings-api).

Public checks on September 19, 2026 did not establish a supported automatic
enumeration route for the four manual employers. Google's
[robots policy](https://www.google.com/robots.txt) disallows the careers results
pagination URLs, and its [terms](https://policies.google.com/terms) require
automated access to respect machine-readable restrictions. Apple's
[website terms](https://www.apple.com/legal/internet-services/terms/site.html)
restrict automated collection; this implementation does not add a scraper for
its visible search results. Amazon's public search response did not expose a
verified feed/pagination contract in the check. Meta's robots/automation-policy
checks encountered rate limiting or login restrictions and were stopped. These
are observed integration gaps, not claims that the employers have no openings
or that an authorized source could never be added.

A September 20 follow-up checked Google DeepMind's possible secondary board.
Its current [careers page](https://deepmind.google/careers/) and
[student researcher page](https://deepmind.google/student-researcher-program/)
direct applicants to Google Careers. A read-only check of the documented
Greenhouse route for board `deepmind` returned `not_found`; an older indexed
Greenhouse page is not evidence of current feed coverage. Google/DeepMind
therefore remain a visible manual gap, with no new automatic preset entry.

A further September 20 public-route check retained the Google and Amazon gaps.
Google's [published sitemap index](https://www.google.com/sitemap.xml) returned
22 product sitemaps with no careers entry. Amazon's
[robots policy](https://www.amazon.jobs/robots.txt) advertised no sitemap, and a
single standard sitemap request returned HTTP 404. Its
[application FAQ](https://amazon.jobs/content/en/faq/application) did not establish
a documented enumeration API. These bounded observations do not prove that no
authorized route exists. No undocumented API or posting details were fetched.
User-authorized offline job-alert import is a possible later extension; it is
not implemented and does not close either automatic coverage gap.

Netflix publishes a careers sitemap index in its
[robots policy](https://explore.jobs.netflix.net/robots.txt). This adapter checks
that policy on each run, reads the advertised sitemap and then only advertised
same-host job pages. It extracts inert `JobPosting` JSON-LD, follows no scripts
or application links, and refuses restrictions, redirects and rate limits.
The ten-request budget normally permits seven job details after robots/index/
sitemap reads. Standalone `jobs discover` attempts the first seven in descending
advertised `lastmod` order, with numeric posting ID breaking ties. The timestamp
is a sorting hint, not proof that a posting is current. Title filters apply after
detail reads, so changing the filter does not reach later Netflix pages.

Saved searches instead refresh one newest posting and attempt up to six later
entries from their durable cursor. Even filtered-out details advance that cursor;
finishing the tail starts another cycle on a later run. Isolated missing,
malformed, oversized or temporarily unavailable details remain visible errors
and allow later entries to proceed. Policy/authentication restrictions, redirects,
rate limits and request-budget refusals stop before consuming the restricted or
refused entry. Every run rereads the current robots policy and advertised routes.
Neither traversal promises complete or current coverage of a changing board.

## Interpret results and retry

JSON uses the normal `command`, `data`, `error`, `ok`, `version`, `warnings`
envelope. `data.sources` reports each source's identity, `status`, selected
`count`, `filtered_count`, raw `observed_count`, `fetched_at`, first `error` and
complete `errors` list. Netflix also reports advertised unique `indexed_count`
and unattempted `remaining_count` for that invocation; these are null for ATS
feeds. These are not cumulative coverage counters. A budget refusal before the
GET is not an observed detail. Status is `successful`, `partial`, `failed` or
`manual_required`. Failed/partial reads never mark existing postings closed.

`data.jobs` contains selected posting identities, titles, locations, URLs and
content hashes. Preview additionally includes normalized source text and its
normalizer version. Capture mode returns `captures` with saved `job_id` and
`replayed`, plus `capture_blockers`, `storage_error` and `uncaptured_count`; use
`jobs show` for saved text. `storage_checked` is false for preview;
`content_trust` is `untrusted` and
`external_submission_taken` is false in both modes.

Exit 0 means every configured source succeeded and selected jobs were captured
(or previewed). Exit 2 retains useful results but reports incomplete discovery
or storage failure. The preset therefore returns 2 while any manual route
remains. Inspect the report rather than discarding it. `capacity_reached` or
`capture_failed_retry_discovery` preserves earlier captures; repeat discovery to
reuse unchanged versions, including after an uncertain later commit.
An individual posting with too many extractable requirements yields
`snapshot_rejected` in `capture_blockers`; independent captures continue. Shared
storage/integrity errors stop remaining writes. If output is interrupted or
fails after a commit, repeat discovery to recover already-saved versions.

Snapshot identity binds provider, board, external ID, normalized content and
versioned provenance. Unchanged content reuses the first capture; changed content
creates another immutable snapshot. No migration is needed: existing snapshot
and workflow tables store this contract. Hashes detect inconsistency; they do
not authenticate source ownership. `live_page_verified` remains false: feed or
job-detail reads do not inspect the application form or confirm eligibility.
Netflix snapshots use `netflix_jobposting@1` and `public_careers_jsonld`
provenance; existing ATS snapshots keep their original normalizer and identity.

## Bounds and remaining work

Each response is limited to 16 MiB and each feed to 10,000 raw records. Lever
uses at most ten pages of 100 records. Requests use a ten-second budget; DNS
resolution lacks a portable hard deadline, so this is not an absolute wall-time
guarantee. The transport uses fixed HTTPS GET endpoints, no credentials, proxies
or redirects, and sends no candidate facts. DNS screening is sampled, not pinned.

A new discovery capture that would grow the database beyond 16 MiB rolls back
that capture and stops further saves. This is a capture guard, not global
retention or automatic deletion. Stdout is private; `--log-events` uses fixed,
content-free diagnostic events on stderr, not raw job text or remote errors.

Persistent source/evidence scopes, integrated on-demand preparation and Netflix
cursor checkpoints are available through [saved searches](SEARCH_RUNS.md).
[Daily execution](DAILY_SEARCHES.md) adds durable timing and notification state;
an independently authorized wake-up mechanism must invoke it. General discovery
cursors, automatic retention and browser filling remain planned.
Saved-job [batch preparation](BATCH_PREPARATION.md) is also available separately.
The standalone discovery command can be repeated safely; use saved searches for
durable source and preparation checkpoints.
See [ADR 0009](adr/0009-daily-discovery-and-draft-queue.md) for that milestone.

The Netflix traversal gate uses fictional public responses and approved profile
fixtures, prepares nine real PDFs, and verifies exact replay, filtered advancement,
cycle restart, a changed newest posting and encrypted restoration. All nine pages
passed visual review. Run [the acceptance gate](../scripts/check_source_window.py)
with the materials and backup dependencies from [DEVELOPMENT.md](DEVELOPMENT.md):

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_source_window.py --with-backup
```

## Additional ATS feasibility

SmartRecruiters remains **planned, not supported**. A September 20, 2026 review
found an [unauthenticated Posting API](https://developers.smartrecruiters.com/docs/customer-overview)
with [public company listings](https://developers.smartrecruiters.com/reference/v1listpostings),
offset pagination and at most 100 entries per page. Full descriptions require a
[separate detail read](https://developers.smartrecruiters.com/docs/endpoints).
Under the current ten-request source cap, one listing leaves at most nine detail
reads, so useful coverage would need bounded advancement and explicit gaps.

The appropriate authorization route for a candidate-side tool is unresolved.
The [API introduction](https://developers.smartrecruiters.com/docs/posting-api)
describes customer career sites and partner widgets, while the
[candidate terms](https://www.smartrecruiters.com/legal/terms-of-use/)
restrict automated access to other users' content. This does not establish that
the documented API is prohibited, nor does it establish this tool's permission.
Resolve that scope before implementing an adapter. No live postings were read
for this feasibility review. Adding another ATS would expand configured-company
support; it would not establish market-wide coverage.
