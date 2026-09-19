# Use Grounded Apply through Codex

Open this repository in Codex and ask for the job-search work you need. The
repository skill is `grounded-apply`; explicit invocation is `$grounded-apply`.
Codex handles the CLI and identifiers. You choose facts, review results, and
submit the application yourself.

Examples:

- "Help me set up my profile for industry research and engineering roles."
- "Compare these two job descriptions with my approved experience."
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
does not parse those formats or fetch the URL itself.

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

Exact commands and dependency setup are in [QUICKSTART.md](QUICKSTART.md).
The launcher uses `GAPPLY_PYTHON` if supplied, otherwise `.venv/bin/python3` if
present, otherwise `python3` from PATH. An incompatible interpreter fails with a
setup message before importing application code. `sh scripts/python` uses the
same interpreter contract for verification commands.

## Compare jobs and prepare materials

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

Codex selects and orders exact approved text for the current fixed template.
It can propose clearer language for separate review, but that proposal is not a
verified artifact. There is no automatic semantic rewrite or cover-letter
generator in this milestone. Keep employer/title/date/bullet groups together;
the program does not infer those associations.

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
workflow, not the entire long-term product. Discovery feeds, semantic rewriting,
browser filling, automated email/calendar integrations, and richer input formats
can be added behind the existing services without replacing your profile or
application history. Hiring outcomes remain outside the software's guarantees.
