# Set up a reusable career profile

Ask Codex to use `$grounded-apply` with your CV, to interview you, or both. You
choose a private data home outside the repository. Codex handles commands and
review identifiers; you decide what to retain and approve.

## Start with a CV

Supply the original `.txt`, `.pdf`, or `.tex` path. A manually created text export
is no longer the first step. PDF intake requires the optional `materials` extra;
static LaTeX and text intake use the base installation. If you have both a custom
LaTeX CV and its PDF, a selectable-text PDF will often recover more content.

Codex should show the complete inventory, grouped by section: proposed facts,
unclassified lines, and any extraction gaps. Research, projects, education,
individual publications and achievements belong in this inventory even when
they are not immediately useful for a target role. Review can happen in small
batches; the remaining inventory must stay visible.

Choose whether to retain all supported facts or a subset. The complete-facts
policy has no source-percentage cap. All retained claims start **pending** and
need explicit approval before use. Approving your career inventory does not put
every fact into every resume; application-specific selection happens later.

The original 80% rejection was a pilot minimization heuristic. It remains only
in the legacy selected-import policy for compatibility. It is not a requirement
to discard 20% of your CV, pad it, or split it artificially.

## Preserve explicit research context

Extractor version 3 introduced research proposals under
`Research`, `Research Experience` or `Research Projects`. Approved facts of
that type appear in a separate Research section when selected for a resume.
Publications keep their existing type and Publications section. Exact wording,
contribution level, dates and publication status still require review.

The complete inventory, retention choice and per-fact approvals stay the same.
The extractor uses those explicit headings without guessing from keywords.
Existing imports keep their original types; earlier materials keep their saved
layout and approvals. An older research fact needs the normal import/review path
to change its classification.

## Keep unsupported sections separate

Codex uses extractor version 4 explicitly for new intake and keeps the displayed
version when resuming an earlier selection.

Version 4 preserves the research mapping and treats exactly four labels as
neutral section boundaries: `Research Interests`, `Academic Research`,
`Selected Research` and `Professional Memberships`. They end the preceding
section's classification instead of becoming, for example, extra publications.
The labels remain visible as headings; following nonlabel text stays visible
as unclassified until a recognized section begins. Case and a trailing colon
do not change these matches. Codex can propose an explicit classification of
exact source lines through the existing import and approval process.

This does not recognize arbitrary headings or infer completed research,
publications, memberships or preferences. Check the full inventory against the
source. Classification gaps are separate from document-reading gaps:
`document.incomplete: false` does not mean every line has a supported fact type
or that the extracted facts are semantically complete. `--allow-partial` handles
reported document gaps; it does not classify unknown lines.

## Keep a wrapped publication together

A PDF may split a publication title and its status into separate proposals.
Those fragments are not separate works. When you choose consecutive publication
fragments that belong together, Codex uses `profile group-publication` to keep
their exact evidence and join only whitespace in the proposed wording. Status
such as “under review, not accepted” stays with the title. The command does not
infer relationships from layout or group entries automatically.

For several works, Codex keeps each work's explicitly chosen fragments in a
separate group and returns one complete inventory. Adjacent works stay separate;
overlapping selections are refused. You do not need to combine separate results.

The helper reads the original document without profile storage, retention or
approval. It returns every supported proposal, replacing each chosen group once
and keeping all other proposals in order. An index map connects the original
proposals to the result; the original inventory and unknown lines remain visible.
It requires the displayed extractor version and **both hashes for every format**,
including text. Gaps between the selected spans must contain only whitespace.
Document issues still require an explicit `--allow-partial` choice.

Once retention is authorized, Codex saves only the returned `data.manifest` as
temporary JSON at a chosen private path outside the checkout. That file contains
candidate evidence and is private; it is not a manual plain-text CV export. Codex
previews the existing structured import against the same original document and
hash, then retains the authorized proposals as pending facts. The usual review
and approval steps still apply. See the [operator example](#publication-grouping-for-operators).

The original-document hash guards this invocation; durable evidence still binds
the extracted-text hash and exact span, not the original PDF bytes. Generic
structured import does not prove that proposed wording preserves every qualifier.
Review the complete grouped wording and evidence; do not shorten the helper's
output and assume the remaining evidence makes that change equivalent.

## Review in manageable batches

After retention, Codex starts with five pending facts and their evidence. You
can choose a different batch size, approve or reject displayed facts, skip a
fact, or stop. Codex shows the total still pending, how many this batch contains,
and how many pending facts remain before and after it. A small display never
means a smaller retained profile. Extraction gaps and unclassified source lines
remain separate follow-up tasks; these counts cover retained pending facts.

Codex continues from the last displayed claim, even after you approve or reject
it. Skipped facts stay pending. At the end of a pass, earlier skipped facts may
still need review; restart from the first pending batch to revisit them. Review
is complete only when the total pending count is zero, with any source gaps
still reported separately.

You can resume later from the prior claim identifier, or start again with the
remaining pending facts. The command saves no page position, approval or new
profile state. Facts and recorded decisions remain in the existing database;
showing or skipping a page does not approve, reject or delete anything.

## Read the extraction limits honestly

- Text is read exactly. Explicit `Name:`/`Email:` and similar contact labels
  prevent identity guesses. Unlabeled content remains visible for classification.
- PDF extraction reads local text, without OCR. Multi-column order, unusual
  glyphs and scanned sections need comparison with the original. Encrypted or
  image-only files return an actionable error. Blank pages mark extraction
  incomplete, even if another page has useful text.
- LaTeX is read statically. It is never compiled, and includes, commands, custom
  macros, conditionals and mathematics are not executed or guessed. Affected
  paragraphs and enclosing groups are excluded from proposals and located by original source line for
  review. Use an available PDF or supply only the unresolved wording.
- DOCX and OCR are not implemented. The adapter limits input and extracted text
  to 16 MiB, PDF pages to 100 and parsing to 15 seconds. PDF parsing runs in a
  subprocess with stream limits and available OS resource limits; this is not a
  universal memory sandbox.

Every nonblank extracted line is accounted for, but line accounting cannot prove
semantic completeness. Unknown headings and unsupported facts need a decision;
blocked sensitive fields must not be silently reclassified as career facts.
Extractor 2 keeps Research and Research Experience under
`employment_description` (Experience), and Research Projects under `portfolio_item`
(Projects). Teaching and volunteer narratives also retain the broad Experience
classification. A section label does not establish an employer, paid role or
employment dates; those facts require their own source support.
The exact evidence spans refer to extracted text. Original document hashes and
extraction warnings are shown during intake but are not stored as durable
original-file provenance. The database stores chosen evidence and a digest-only
identity for the extracted text, never the source document itself.

## Start or deepen the profile with questions

The optional `profile interview` catalogue covers contact details, education,
experience, research, projects, skills, publications, achievements and search
preferences. Depth 1 asks basics; depth 2 asks responsibilities and context;
depth 3 asks outcomes and evidence. Codex asks a few useful questions at a time
and adapts to what you already supplied. Skip, switch topics or stop at any time.

This command stores no answers and reads no profile. Its cursor continues a
question list; it is not a saved interview or a completeness score. The retained
profile is separate context for choosing what to discuss next.

After a few questions, Codex shows the exact answers you might retain and asks
which ones belong in your profile. It prepares proposals from only those chosen
statements, preserving their wording and evidence spans. Retention saves them
as pending facts with user-statement origin; it does not approve them. Review
and approve each fact through the same manageable batches used for a CV.

Skip or stop without retaining an answer. Unretained answers and the raw
conversation are not saved to the profile. On resumption, Codex can consult the
retained facts; there is no saved transcript or interview
position. Preferences are choices for search setup, not verified qualifications.
Sensitive eligibility, legal attestations and signatures stay separate.

## Resume from retained facts

For an existing authorized profile, Codex uses `profile inventory` for a compact
topic overview, then opens a small page from the topic you want to discuss.
The overview contains only counts by topic, claim status and approval status:
no names, claim text,
identifiers, source references or evidence excerpts. Topic detail supplies the
exact retained wording and its status, origin, scope and sensitivity, without
loading unrelated topics into the conversation.

Contact, education, experience, research, projects, skills, publications,
achievements and Other account for every retained claim, including pending,
rejected and retired records. Topics follow explicit stored claim types, not
guesses from the wording. Earlier research stays in its original Experience or
Projects classification; unrecognized types appear in Other. Search preferences
remain separate from career claims.

These counts do not establish which interview questions were answered, whether
your profile or extraction is complete, or which facts are usable in an
application. Even a recorded approval does not establish current scope, time or
evidence eligibility. Codex uses the wording as context, asks useful follow-up
questions and lets you skip or stop. It does not automatically skip questions
or treat a zero count as proof that you lack that experience.

Inventory is read-only. It saves no answer, page position, interview progress or
approval. For a pending fact you want to approve, Codex obtains its evidence and
current review token through `profile review` and records only your explicit
decision. New chosen answers still use statement-origin import and separate
approval. A corrupt profile blocks the entire inventory, even if the affected
record is outside the displayed topic or page.

When you choose a pending fact from the inventory, Codex can show just that fact
and its current supporting evidence
without finding its position in the wider review queue. The total still pending
stays visible. This read does not approve anything; ordinary batch review still
starts with five facts when you want to work through the queue.

## CLI contract for operators

These are example placeholders for Codex, not extra setup work for the user:

```bash
./scripts/gapply profile extract --source-file /absolute/private/cv.pdf \
  --extractor-version 4 --json
./scripts/gapply profile onboard --source-file /absolute/private/cv.pdf \
  --source-sha256 EXTRACTED_TEXT_HASH --document-sha256 ORIGINAL_DOCUMENT_HASH \
  --extractor-version 4 --select all --idempotency-key onboarding-1 --dry-run --json
./scripts/gapply profile onboard --source-file /absolute/private/cv.pdf \
  --source-sha256 EXTRACTED_TEXT_HASH --document-sha256 ORIGINAL_DOCUMENT_HASH \
  --extractor-version 4 --select all --idempotency-key onboarding-1 --json
./scripts/gapply profile review --limit 5 --json
./scripts/gapply profile interview --topic research --depth 2 --limit 3 --json
```

Codex continues the paged review with:

```bash
./scripts/gapply profile review --limit 5 --after CLAIM_ID_FROM_NEXT_AFTER --json
```

`--limit` accepts 1–50; `--after` requires a limit. Continue with the response's
`data.page.next_after` when it is present. A null cursor ends this pass, not
necessarily all pending review: check `data.pending_count` and the page's earlier
pending count. Omit `--after` to restart. With neither option, `profile review
--json` still returns the complete pending queue in its original response shape.
Every pending record is validated before a page is displayed; pagination does
not hide an invalid off-page record or weaken per-item decisions.

For resumed interview context:

```bash
./scripts/gapply profile inventory --json
./scripts/gapply profile inventory --topic research --limit 5 --json
./scripts/gapply profile inventory --topic research --limit 5 \
  --after='CLAIM_ID_FROM_NEXT_AFTER' --json
```

Without `--topic`, inventory shows only counts and accepts no pagination flags.
Topic pages default to 20 records; `--limit` accepts 1–50. Continue with that
topic's `data.page.next_after`; `--after` and `--after-json` are mutually exclusive
and require a topic, but not an explicit limit. Codex quotes continuation arguments
safely, using `--after=...` or a quoted JSON string with `--after-json=...` for
nonprinting IDs. Existing IDs are not restricted to UUIDs.

Each page reports the topic total and before/returned/after counts; the before
count includes the anchor. Records remain in the inventory after approval,
rejection or retirement, so those decisions preserve continuation. Each call is
a fresh snapshot. A null `next_after` means the end of this pass, not a complete
profile or interview. Restart without the anchor to see earlier or newly added
records; an unknown or wrong-topic anchor requires a restart rather than silently
changing the position. No inventory page authorizes a decision or application use.

`--select all` means all supported proposals, not all unknown source content.
Onboarding reports selected/unselected counts and lines needing attention.
An incomplete document requires review and an explicit `--allow-partial` choice;
that choice does not resolve the missing content. Use `--source-format` to
override format detection or identify stdin. No intermediate text file is needed.

Numeric selections require the extractor version displayed during extraction.
Version 1 preserves the old heading rules and proposal indexes for recovery;
version 2 adds CV/research headings. Version 3 keeps the same inventory and maps
the three explicit research headings to the distinct research type. Version 4
adds the four neutral boundaries above; versions 1, 2 and 3 keep their original
proposals, indexes and replay behavior. Omitting the version for a numeric
selection fails rather than risking selection of a different fact after an
upgrade. Extraction and `--select all` still default to version 2; the examples
explicitly use 4 for new intake. Keep the original version when retrying an
earlier selection. Indexed selections can add
`--retain-all-facts` to use policy 3; ordinary legacy selections retain policy 2.
Structured `profile import --retain-all-facts` supports the same policy with
exact UTF-8 source spans for separately classified resume facts.
For an unclassified PDF/TeX line, Codex can build a proposal using the displayed
span and import from the original file with `--source-format auto` and
`--document-sha256`; the manifest hash/span refer to the extracted text. The
same partial-extraction guard applies. No manual text export is necessary.
Use the [complete-inventory recipe](#complete-inventory-manifest-for-operators)
to preserve every supported proposal while adding explicitly classified rows.
Structured import defaults to exact text for compatibility with older manifests.
Policy changes require a new idempotency key; existing recorded imports retain
their original policy and approval/replay history.

For chosen interview answers, use explicit statement-origin intake:

```bash
./scripts/gapply profile import --source-kind user-statement \
  --source-file /absolute/private/chosen-answers.txt \
  --proposals-file /absolute/private/answer-proposals.json \
  --retain-all-facts --idempotency-key answers-1 --dry-run --json
./scripts/gapply profile import --source-kind user-statement \
  --source-file /absolute/private/chosen-answers.txt \
  --proposals-file /absolute/private/answer-proposals.json \
  --retain-all-facts --idempotency-key answers-1 --json
./scripts/gapply profile review --limit 5 --json
```

Codex prepares the existing schema-2 proposal manifest with exact source-text
spans and its SHA-256 assertion. Statement intake accepts UTF-8 text only, with
`--source-format text` as the default; it does not interpret PDF or LaTeX.
Either input may use `-` for stdin, but not both. The application assigns the
source identity and origin; the manifest cannot choose them. Keep the same
request and key for retries. Omitting `--source-kind` preserves resume import
and does not convert older records into statements.

### Review one pending fact

To inspect a pending fact already shown in inventory:

```bash
./scripts/gapply profile review --claim-id='CLAIM_ID_FROM_INVENTORY' --json
```

Use the exact ID and shell-quote it safely. For an ID containing nonprinting
characters, including NUL, encode the exact ID once as a JSON string and pass
the safely quoted result with `--claim-id-json` instead. IDs are not restricted
to UUIDs. The two selector options are mutually exclusive and cannot be combined
with `--limit`, `--after` or `--after-json`.

The response has one `data.items` entry, the global `data.pending_count`, and
`read_only: true`, with no `page`. It exposes only the chosen pending item's
existing evidence and review token. A missing or no-longer-pending fact produces
a fixed private refusal, not a fallback to the whole queue. Refresh inventory
if its state has changed. The entire pending queue is validated in one read
snapshot before selection; unrelated corruption still blocks the read.

A generic pending fact may retain a null review token; do not manufacture a
token or treat readable evidence as approval authority. Use the normal explicit
decision flow only with its required current token. Selecting a fact saves no
cursor, decision or profile changes. Default full review and `--limit 5` batch
review keep their existing outputs and behavior.

### Complete-inventory manifest for operators

Codex prepares this file; the user reviews wording, classification and retention
choices rather than managing JSON. Use it when the user wants all supported facts
plus specific unclassified rows. `--select all` alone does not classify those rows.

1. Extract the original file with the displayed version (4 for new intake).
   Keep `data.proposals`, `data.inventory`, `data.source_sha256` and
   `data.document` together. Show every fact row and all gaps. Ask for a supported
   type when an unclassified row's meaning is unclear; do not guess from its
   heading. For example, a user-confirmed name can use `candidate_name`, an email
   `contact_email`, or an explicitly described research contribution
   `research_description`. These are classification choices, not approvals.
2. Preserve **every supported proposal**, copying only `claim_type`, `value`,
   `canonical_text`, `span` and, if present, `confidence`. Copy their values
   exactly. Do not copy extraction-only `scope`, `sensitivity`, `subject_type`,
   `subject_id`, inventory metadata or the response envelope: the import format
   rejects extra fields. If publication grouping was already chosen, start from
   its complete `data.manifest` instead of restoring the original fragments.
3. For each explicitly chosen unclassified row with a supported **scalar-text
   type** (such as the three examples above), copy its returned `start`, `end`
   and `text` into `span`. Use that exact text as both `value` and
   `canonical_text`, with the reviewed `claim_type`; `confidence` is optional.
   Structured types such as `employment_title` and `employment_dates` require
   their [registered value schema](DEVELOPMENT.md#structured-profile-import-and-review)
   and separate explicit review; this recipe does not implicitly convert text
   into those objects. Insert the chosen proposals
   in original source order among the preserved proposals. Do not reconstruct
   offsets, decode the document separately, invent missing context or relabel a
   blocked sensitive row to bypass its restriction.
4. Wrap the proposals with **only** the six top-level fields below. Use
   `data.source_sha256` for the manifest hash. Keep a separate review list of
   included rows, unresolved or explicitly excluded rows, structural headings
   and document issues; that list does not belong in the closed manifest.

This illustrates the shape of one fictional, explicitly classified row. A real
complete-inventory manifest also contains every other supported proposal and
chosen row; do not replace the full list with this example.

```json
{
  "schema_version": 2,
  "source_sha256": "EXTRACTED_TEXT_HASH_FROM_DATA_SOURCE_SHA256",
  "span_index_base": 0,
  "span_unit": "unicode_codepoint",
  "span_end": "exclusive",
  "proposals": [
    {
      "claim_type": "candidate_name",
      "value": "Avery Quill",
      "canonical_text": "Avery Quill",
      "span": {"start": 0, "end": 11, "text": "Avery Quill"}
    }
  ]
}
```

Save the complete manifest to a chosen private temporary path outside the
checkout. It contains candidate evidence; keep it out of Git and diagnostics.
The manifest hash and Unicode-codepoint spans bind **extracted text**, not TeX
bytes or PDF layout. Separately, pass `data.document.document_sha256` as the
original-document guard. Preview against that same unchanged original file:

```bash
./scripts/gapply profile import --source-file /absolute/private/cv.tex \
  --source-format auto --document-sha256 ORIGINAL_DOCUMENT_HASH \
  --proposals-file /absolute/private/complete-proposals.json --retain-all-facts \
  --idempotency-key complete-intake-1 --dry-run --json
```

The same command accepts an original `.pdf`; no manual text export or direct
Python adapter is needed. Use `--allow-partial` only after reviewing and accepting
reported document issues; it does not resolve unknown classifications. Check
that the preview's proposal/claim/evidence counts equal the complete intended
list. A refusal requires correction and review, not silently dropping a row.

Dry-run retains and approves nothing. After a successful preview and explicit
retention choice, repeat without `--dry-run` under the authorized initialized
private profile. Use the same request and key for an identical retry. All facts
remain pending until the usual evidence review and separate per-fact approval.

The original extraction report stays unchanged: a row explicitly classified in
this manifest may still be reported as unclassified/skipped in that report.
Headings remain source context, not extra facts. Preserve unresolved items and
document gaps separately; neither count matching nor a valid hash/span proves
semantic completeness, correct classification or preservation of meaning in
arbitrary rewritten proposals. This recipe copies exact wording and does not
change the generic import contract or durable original-document provenance.

### Publication grouping for operators

Use the actual displayed indexes and version. `7,8` and `10,11` below are
examples of two explicitly chosen works;
omit the second `--indexes` for the existing single-group flow.

```bash
./scripts/gapply profile group-publication --source-file /absolute/private/cv.pdf \
  --source-format auto --source-sha256 EXTRACTED_TEXT_HASH \
  --document-sha256 ORIGINAL_DOCUMENT_HASH --extractor-version DISPLAYED_VERSION \
  --indexes 7,8 --indexes 10,11 --json
```

Both selections refer to original proposal indexes, even after the first group
would reduce the manifest's size. Reversing the two options changes only the
group-report order, not the manifest or source-index mapping. See the
[reference](REFERENCE.md#explicit-publication-grouping) for report versions and bounds.

Codex saves only `data.manifest`, not the surrounding report, to the private
`grouped-proposals.json` path used below. The manifest includes all supported
proposals; grouping alone does not authorize retention of them.

```bash
./scripts/gapply profile import --source-file /absolute/private/cv.pdf \
  --source-format auto --document-sha256 ORIGINAL_DOCUMENT_HASH \
  --proposals-file /absolute/private/grouped-proposals.json --retain-all-facts \
  --idempotency-key grouped-intake-1 --dry-run --json
```

After a successful preview and authorized retention, repeat without `--dry-run`.
Use the same file, manifest and key for retries; a changed grouping is a new
request. Each imported fact remains pending. Pass `--allow-partial` to grouping
and import only when the document's reported gaps have been reviewed and accepted.

Sources, extracted responses and review output are private. Keep them outside
Git and out of diagnostic logs. Use [the quickstart](QUICKSTART.md) for approval
commands and [the development guide](DEVELOPMENT.md) for the full data contract.
