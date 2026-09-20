# Use Grounded Apply through Codex

Open this repository in Codex and ask for the job-search work you need. The
repository skill is `grounded-apply`; explicit invocation is `$grounded-apply`.
Codex handles the CLI and identifiers. You choose facts, review results, and
submit the application yourself.

Examples:

- "Help me set up my profile for industry research and engineering roles."
- "Compare these two job descriptions with my approved experience."
- "Find research openings on these public company boards and show coverage gaps."
- "Use these sources and my agreed evidence selection to discover jobs and prepare a batch of drafts."
- "Run that saved search each morning and show me new drafts or questions that need me."
- "Prepare a resume and answers for this saved job. Show me the draft."
- "What should I work on next in my job search?"
- "I submitted the reviewed pack. Record it and show me what is next."

The skill is stored at `.agents/skills/grounded-apply/SKILL.md`, following the
[official Codex skill location](https://learn.chatgpt.com/docs/build-skills).
If the current task has not discovered a newly added skill, start a new task in
this project or ask Codex to read that file explicitly. A repository checkout is
required for this skill; a Python wheel alone supplies the CLI, not a global
Codex plugin. No MCP installation or model API key is required.

## First useful session

Supply an explicit private data-home path outside the repository, a UTF-8 resume
or career-inventory file, and one or two job descriptions with their URLs.
Existing PDF/DOCX resumes need a faithful plain-text export. This milestone
does not parse those formats. Supplied arbitrary URLs are not fetched by
`jobs add`; supported public feeds can now supply job snapshots through discovery.

Use a career inventory with clear `Name:` and `Email:` labels and recognized
sections such as `Experience`, `Projects`, `Education`, `Skills`, and
`Publications`. Keep the exact wording of dates, degree progress, research
contributions, and publication status. Codex must review skipped lines and cannot
quietly drop relevant research just because a heading was unrecognized. A short
resume may hit the whole-source minimization guard; select a genuinely relevant
subset, rather than padding or splitting it to evade the guard.

Codex extracts proposals without storing the source. You choose which facts may
be retained. Those facts remain pending until you review and approve them. The
same approved facts can then support multiple applications without repeated
approval. New facts and corrections go through review; retirement keeps old
submission history intact.

Facts, selected source evidence, and approval records live in SQLite. Codex shows
readable fact reviews in the conversation by default and regenerates them from
`profile review --json` (pending facts) or `profile show --json` (recorded facts).
The repository skill avoids persistent Markdown review copies and duplicate
profile or session summaries. No user setup is needed for this default when
using the skill; it is workflow guidance, not a per-profile configuration flag
or automatic retention service. Ask explicitly to keep a review export at a
private location outside the managed runtime and repository. Database evidence,
approval history, material versions, and submission snapshots remain intact;
the default does not authorize deleting existing files.

Exact commands and dependency setup are in [QUICKSTART.md](QUICKSTART.md).
The launcher uses `GAPPLY_PYTHON` if supplied, otherwise `.venv/bin/python3` if
present, otherwise `python3` from PATH. An incompatible interpreter fails with a
setup message before importing application code. `sh scripts/python` uses the
same interpreter contract for verification commands.

## Discover jobs and show coverage

Codex can run `jobs discover` against configured Greenhouse, Ashby and Lever
boards and the bounded Netflix sitemap route. Use
[JOB_DISCOVERY.md](JOB_DISCOVERY.md) for source setup and bounds.
An explicit public-search request authorizes the corresponding feed reads; use
the existing authorized private home for capture. Preview with `--dry-run` when
no storage target has been chosen; it makes network requests but opens no profile.
Title filters are optional substring alternatives, not evidence of qualification.

Present every source's status and selected/filtered counts. The `major-tech`
preset checks Anthropic and OpenAI feeds, partially reads Netflix's published
sitemap, and shows four other employers as manual coverage gaps. Exit 2 can
accompany useful partial results. Inspect the report
before retrying or claiming there are no jobs. Default capture reuses unchanged
versions and returns job IDs for `jobs assess` and material preparation. Never
present a saved observation as current availability, or describe this command as
a daily runner, automatic shortlist or batch package generator.

## Compare jobs and prepare materials

For a concise daily setup, start with [the daily quickstart](DAILY_QUICKSTART.md).

For discovery and preparation together, establish a
[saved search scope](SEARCH_RUNS.md). Codex handles `searches configure`, `run`
and `resume` under one agreed set of sources, title filters, exclusions, approved
evidence and limits. Version-2 scopes add explicit title exclusions and location
text preferences. Keep missing locations included unless the user explicitly
chooses otherwise, and show the employer's location wording when reviewing the
queue. Text matches do not prove remote-work or geographic eligibility.
A repeat run key recovers the same run; a new discovery key
advances past unchanged current drafts. Present source gaps alongside the draft
queue and grouped questions. This runs on demand and does not install a daily
schedule. Resume a search-owned batch through its parent search.

For a file handoff, Codex uses `searches export` on the exact run and opens its
`review.md`. One private folder contains current PDFs, answer files, job links,
source gaps and grouped blockers. Stale packages remain listed without files;
partial answers and unknown form coverage remain explicit. Choose a new private
external directory, and retain the copy's snapshot and approval limitations.
Export does not approve or submit. Later changes require a fresh review/export;
the copies are outside managed runtime backup and deletion.

For several saved jobs, use [batch preparation](BATCH_PREPARATION.md). Establish
the role/evidence-selection scope once and reuse the approved facts across
ordinary drafts. Codex writes the closed specification, previews it and runs
`batches prepare`; the user need not supply IDs or repeat the same choices.
Use `batches resume` after a budget stop or interruption and `batches show` for
one consolidated review. Continue independent items while grouping missing facts
and required human answers. Review all exported PDF pages and exact bundles as
described below. Unknown questionnaire coverage must remain visible.

Save the supplied text and URL through `jobs add`, then use `jobs assess`.
For each job, Codex should explain which quoted requirements have relevant
approved evidence, what remains unknown, and what appears worth investigating.
Evidence should be cited by claim ID. A requirement with shared terms is still
unresolved until reviewed; the tool does not compute fit or hiring probability.
Check the actual opening's availability and deadline separately.

For industry research, emphasize approved research contributions, methods,
evaluation, publications, and projects that address the role. For engineering,
emphasize approved implementation, testing, systems, and delivery evidence.
Neither route permits upgrading academic prototypes into production experience,
contributions into leadership, or expected degrees into awarded degrees.

Codex selects and orders exact approved text for the versioned resume template.
It can propose clearer language for separate review, but that proposal is not a
verified artifact. There is no automatic semantic rewrite or cover-letter
generator in this milestone. Keep employer/title/date/bullet groups together;
the program does not infer those associations.

New builds use a restrained sans-serif layout, section rules, aligned headings,
and source-aware bullets. Codex can supply `--layout-file` to adjust presentation
without changing facts; see the quickstart for its closed JSON schema. When a
source CV is supplied, use it as a visual reference. Inspect every exported page
for hierarchy, line wrapping, whitespace and heading placement, and rebuild if
needed. Never hide a layout failure by silently discarding relevant experience.
Earlier material versions keep their original layout; changing presentation
creates a new draft that needs review.

Use `materials build --dry-run` before building and exporting. Inspect the
exported PDF and answers. Approval binds the displayed bundle SHA-256; a changed
bundle needs fresh review. Sensitive or unknown questions yield `NeedInfo`.
Required unanswered questions block approval; never mark them optional simply
to obtain a ready result. Handle unsupported mandatory fields directly with the
user and report the limitation.

For interview preparation, Codex can organize approved examples into discussion
notes and identify gaps to practice. The CLI does not yet provide a dedicated
interview-story store or interview-preparation generator.

## Resume without reconstructing the conversation

For recurring discovery and preparation, use [daily searches](DAILY_SEARCHES.md).
Codex agrees the saved search, local time/timezone, start date and limits once,
checks for an existing schedule, then previews and saves the daily policy. An
authorized Codex automation or other wake-up mechanism invokes the same schedule.
The CLI itself installs no background process. The private runtime and schedule
IDs belong in that instruction; career facts and source contents stay in SQLite.

A tick resumes unfinished work within its attempt limit before starting a later
day. Offline days are coalesced; pausing fences active work and does not build a
historical backlog. A local computer must be awake and able to run its trigger.
Schedule-owned searches resume through the schedule so its limits still apply.

Codex shows new drafts, changed blockers and source-health changes together. It
reviews the run identified by a pending notification before acknowledging delivery;
that run may be older than the latest one. A failure before child creation is
reviewed from the schedule itself. Acknowledgment does not approve material or
assert an application was submitted. Successful unchanged work stays quiet, while
execution or validation failures remain visible even if no notice could be saved.
An exit 2 with an unchanged known source gap does not by itself create another
notification; the validated delta distinguishes it from a new failure.

For an on-demand review of the broader search, use the briefing:

`./scripts/gapply brief --json` returns one consistent, read-only view:

- pending fact-review counts;
- saved job URLs and capture dates;
- recorded application stages and latest material status;
- requirement retrieval/gap counts, with no invented fit score;
- stage-based next actions, including repairs when approved evidence was retired;
- response-check suggestions after a configurable interval (default seven days).

`brief --job-id ID` restricts job/application details to that snapshot; profile
review counts are still global. `--follow-up-days 14` changes only that call's
response-check interval. No preference is stored and no reminder is scheduled.
These are workflow suggestions, not employer deadlines or automatic outreach.
The output is private even though it excludes resume text, answer bodies, and
material bytes. Never attach stdout to a support log.

An approved draft is different from an application recorded as ready. A ready
application can become unusable if its evidence changes; the briefing then asks
for repair. `application_material_id` names the last bundle bound to its ready
or applied event; `latest_material` may be a different, newer draft. Only
`currently_ready` describes current readiness of the recorded ready application.
Once a submission is recorded, its historical snapshot is immutable,
even if current evidence is later withdrawn. Each explicit application record
appears separately; the briefing never merges possible duplicates automatically.

Codex follows the existing application state machine: discovered → shortlisted
→ preparing → ready_for_review → applied, with subsequent user-reported screen,
assessment, interview, and offer stages. Terminal decisions preserve history.
Preview and confirm transitions through the CLI. A user request to prepare a
pack does not assert they submitted it.

## Milestone and later upgrades

[ADR 0007](adr/0007-codex-guided-application-preparation.md) defines the acceptance
gates; [ROADMAP.md](ROADMAP.md) records the status. This is a practical local
workflow, not the entire long-term product. Bounded public ATS discovery now
extends it, alongside saved-job batches and integrated on-demand discovery-to-draft
runs and durable daily execution. Broader company coverage, semantic rewriting,
browser filling, automated email/calendar integrations, and richer input formats
can be added behind the existing services without replacing your profile or
application history. Hiring outcomes remain outside the software's guarantees.
