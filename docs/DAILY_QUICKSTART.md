# Set up daily discovery and preparation with Codex

Tell Codex the workflow you want. It handles specifications, commands and saved
identifiers. For example:

> Set up a daily search for research engineering roles at these companies. Use
> my approved career facts to prepare up to ten draft application packages.
> Keep location preferences and excluded titles explicit. Show me new drafts,
> grouped questions and source gaps together, with one private review folder.

The daily pipeline is implemented and locally verified. A personal search and
wake-up schedule still require your setup choices; installing this repository
does not create either.

## Choose the scope once

Codex gathers the following in one setup conversation and reuses the saved scope:

- **Targets:** company boards, role-title terms, title exclusions, published
  location preferences, and any postings you want excluded. Missing locations
  stay available for review unless you explicitly exclude them.
- **Evidence:** the approved facts and resume presentation to use across drafts.
  Existing approved facts can be reused. Newly imported facts need review before
  they can support a package; preparation never guesses missing qualifications.
- **Limits and timing:** the maximum drafts and work per invocation, local time,
  timezone, start date, and an authorized wake-up mechanism.
- **Review delivery:** the private runtime and, if wanted, an external private
  parent folder for per-run review copies. Codex can create each new run folder;
  you do not need to export applications one at a time.

Codex previews the scope, runs a first bounded discovery/preparation round and
shows its coverage and blockers. It then saves the agreed daily policy and
configures the authorized wake-up. The local computer must be awake and able to
run it. The CLI itself installs no background process.

## What happens while you are away

Each due run reads the configured public sources, saves immutable job versions
and prepares drafts from the saved evidence selection. It skips recorded
submissions and unchanged current drafts. Budgets and checkpoints make unfinished
work resumable. A question or rendering problem can block one package while the
other packages continue.

Ordinary runs require no per-job prompt. New drafts, changed blockers and source
health changes are presented together; unchanged results stay quiet. Offline
days are coalesced rather than replayed as a backlog of identical searches.
Storage or execution failures stay visible, including work already completed.

## What you review afterward

The review folder contains a job index, links, PDFs, answer files, source gaps and
grouped blockers. Current partial packages retain unanswered questions; stale
files are omitted. Unknown application questions remain unknown. A copy reflects
one read snapshot and does not update after later evidence or approval changes.

Review the exact documents, resolve any missing or sensitive answers, and approve
the packages you intend to use. Browser filling is not implemented; you perform
the external application and final submission. Export and notification
acknowledgment never approve a package or mark an application submitted.

## Coverage and storage limits

Configured Greenhouse, Ashby, Lever and Workable boards are supported. The major-tech
catalog includes Anthropic and OpenAI feed routes and bounded Netflix discovery.
Google, Apple, Amazon and Meta remain visible manual gaps. This is not complete
coverage of most companies or all jobs at a supported employer. See the
[coverage table](JOB_DISCOVERY.md#coverage-of-the-major-tech-preset).

The supported database snapshot limit is 256 MiB. Diagnostics show headroom
and capacity stops preserve completed drafts. Document deduplication and
automatic retention remain planned. Exported review folders are caller-owned
copies outside runtime backup and deletion.

Details: [saved searches](SEARCH_RUNS.md), [daily policy](DAILY_SEARCHES.md),
[full conversational workflow](CODEX_WORKFLOW.md), and
[verified checkpoint](SESSION_HANDOFF.md).
