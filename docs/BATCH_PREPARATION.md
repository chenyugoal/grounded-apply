# Prepare a batch of saved jobs

**Status: Implemented and locally verified.** The batch service and
CLI pass the full, real-PDF and installed-package gates. See [SESSION_HANDOFF.md](SESSION_HANDOFF.md) for
the actual verification evidence. This slice prepares saved jobs. Implemented
[saved searches](SEARCH_RUNS.md) connect discovery to these batches, and
[daily execution](DAILY_SEARCHES.md) adds bounded scheduled policy and recovery.
An actual personal scope and wake-up mechanism still need separate setup.

A batch supplies one shared selection of approved career facts and a list of
saved job versions. Each job can override the shared selection or presentation.
Grounded Apply prepares independent items, saves progress and returns one review
queue. Missing evidence or required human answers block the affected item.
Preparation never approves a claim or bundle and never submits an application.

## Commands

```bash
./scripts/gapply batches prepare --spec-file /private/tmp/fictional-batch.json --idempotency-key fictional-batch-1 --dry-run --json
./scripts/gapply batches prepare --spec-file /private/tmp/fictional-batch.json --idempotency-key fictional-batch-1 --max-items 20 --max-seconds 900 --json
./scripts/gapply batches resume --batch-id BATCH_ID --max-items 20 --json
./scripts/gapply batches show --batch-id BATCH_ID --json
./scripts/gapply batches list --json
```

`--spec-file -` reads bounded JSON from stdin. Preview checks current job/evidence
state using read-only storage, without rendering PDFs or saving a batch. It needs
an initialized private profile. Real preparation creates the request and runs it;
resumption uses its saved specification. Keep `GROUNDED_APPLY_HOME` unchanged.

Codex should select approved evidence under one user-authorized preparation scope,
prepare this specification, operate the CLI and present the review queue. The
user should not need to type identifiers or repeat the same choices per job.

## Closed specification

This example uses fictional identifiers and cannot be run against a real profile:

```json
{
  "schema_version": 1,
  "claim_ids": ["fictional-employment", "fictional-python", "fictional-degree"],
  "layout": {
    "schema_version": 1,
    "presentations": {"fictional-employment": "heading"}
  },
  "questionnaire_coverage": "unknown",
  "jobs": [
    {"job_id": "fictional-job-one"},
    {
      "job_id": "fictional-job-two",
      "claim_ids": ["fictional-python", "fictional-degree"],
      "layout": {"schema_version": 1, "presentations": {}},
      "questionnaire_coverage": "provided",
      "questions": [
        {
          "id": "fictional-career-question",
          "text": "Describe your experience with Python.",
          "claim_ids": ["fictional-python"],
          "required": true
        }
      ]
    }
  ]
}
```

Required fields are exactly `schema_version`, shared `claim_ids` and `jobs`.
Optional shared fields are `layout`, `questions` and `questionnaire_coverage`.
Each job requires `job_id` and may override those four preparation fields. The
resolved specification is immutable. A changed selection, job list or question
needs a new idempotency key; reusing a key with changed input fails closed.

There are 1–50 distinct saved job IDs and 1–80 distinct ordered claim IDs per
selection. The specification after applying shared defaults must fit within
1 MiB. Questions and layout use the same closed contracts as individual material
builds. Overriding claim IDs may require a corresponding layout override so all
presentation entries still refer to selected claims. There are no freeform
resume/prose fields, tool instructions or inferred candidate answers.

`questionnaire_coverage` defaults to `unknown`. `provided` means the specification
contains the supplied questions; it does not certify that every question on the
external form has been retrieved. Required unknown or sensitive questions remain
blockers. A partial material may be retained so its validated resume is not lost.

## Review and recovery

The JSON envelope uses the existing `command`, `data`, `ok`, `error`, `version`
and `warnings` fields. A batch report includes `batch_id`, `status`, `counts`,
`remaining_count`, `blockers_count`, `items`, `grouped_blockers`, `stop_reason`
and `lease_active`. Each item includes its saved job, stage, attempts, material
and bundle references, current validity, questionnaire coverage and blockers.
Items include the application URL and discovered title when available. Existing
exact-bundle approvals are revalidated and reported; preparing a batch records
no new approval. Required-answer blocker groups retain the question identifier,
a bounded untrusted label and a hash of the full question. Only matching question
contexts group together. Inspect the retained partial material's question list
for full text. A shared question never authorizes reusing a sensitive answer.

Item stages are `queued`, `building`, `draft` and `blocked`. Overall states are
`queued`, `running`, `budget_exhausted`, `waiting_for_input`, `completed` and
`failed`. `completed` means all items have validated drafts; the report still
says `application_ready: false`. Export and visually review each PDF, review
answers, and use normal exact-bundle approval before treating it as ready.

Preparation/resumption exits 0 when all items have drafts, 2 when work remains
or a shared/capacity failure stops the run, and 3 when only blocked items remain.
Preview exits 0 for a valid plan or 3 for item blockers. Show/list exit 0 on a
successful read, even when the inspected batch has blockers. A nonzero result
can contain useful completed work; inspect its report.
Shared execution errors return the batch ID, recovery guidance and any saved
items that still pass a fresh validated read. If that read fails, no cached item
report is returned and `review_available` is false.

Retry preparation with the same specification and idempotency key after uncertain
output, or resume the same batch ID. Material identity binds the job version,
approved evidence/provenance, selected presentation, supplied questions and
generation version. Matching inputs can reuse a material across batch requests.
A material committed before its parent checkpoint is recovered on resumption.
Current evidence is rechecked; retired or changed claims prevent stale reuse.
Read-only review reports stale drafts as blocked without rewriting history.

Per-item checkpoints are append-only. An expiring lease with an owner and
generation number prevents an earlier invocation from replacing a later
invocation's checkpoints or committing a child material. The expected plan hash
also prevents changed evidence from being saved under an earlier child identity.
After abrupt process termination, retry may need to
wait for the existing lease to expire. Ordinary exceptions release it. This is
coordination between application processes, not protection against a writer
with the same operating-system identity.

## Bounds and compatibility

The default invocation attempts at most 20 items with a 900-second budget.
Allowed invocation bounds are 1–50 items and 1–3600 seconds. An item stops after
100 attempts and requires a new reviewed batch request for further preparation.
Time is checked between items; an in-flight render/validation is not preempted.
Storage is bounded by the existing 16 MiB database limit, with 256 KiB reserved
for parent checkpoints before a material write. Capacity failure retains earlier
work. It does not authorize deleting history or raising backup/deletion limits.

Migration 005 adds batch requests, items, events and leases. Upgrade an existing
private runtime with `profile init`; ordinary data commands do not migrate it.
Version-4 through version-7 backups remain restorable exactly as captured. Restore performs no
migration; run `profile init` explicitly before using the restored profile with
new batch features. Unknown or modified SQL schemas still fail closed.

Source text and question labels remain untrusted data. Diagnostics contain fixed
command/outcome enums only; private review content stays on stdout. The bulk
claim-resolution helper validates one fresh profile snapshot per group and does
not cache authority across rendering, writes or later invocations.

The real-PDF acceptance gate is:

```bash
sh scripts/python -W error scripts/check_batch.py --with-backup
```

It uses fictional data, ten saved jobs and two isolated blockers,
then verifies replay, budgeted resumption and encrypted queue restoration. This
does not measure real user active time or establish discovery-to-draft integration.
