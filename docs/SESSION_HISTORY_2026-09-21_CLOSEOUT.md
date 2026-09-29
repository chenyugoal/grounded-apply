# September 21 closeout archive

Historical checkpoint preserved during the September 29 release wrap-up.
Its next-task instructions and working-tree status are historical, not current.
Use [SESSION_HANDOFF.md](SESSION_HANDOFF.md) for the live state.

# Session handoff

This is the single live checkpoint. [ROADMAP.md](ROADMAP.md) owns feature scope;
accepted decisions live under `docs/adr/`. Earlier verification is referenced below.

[Current verification](#current-verification) | [Limits](#limits-and-deferred-work) |
[Next task](#next-exact-task) | [First command](#first-command)

## Active window and checkout

- **Updated:** September 21, 2026, 13:29:26 UTC.
- **Window closed out; follow-up paused.** The authorized window ends
  **13:59 UTC / 08:59 CDT** September 21, 2026. In its final thirty-minute period,
  `grounded-apply-usability-window` was paused through the automation tool as
  requested. Its other fields were preserved; the separate
  `grounded-apply-development-window` remains paused and byte-for-byte unchanged.
  No further unattended implementation is selected.
- **Branch / starting HEAD:** `codex/phase-0-truth-layer`, `9a3b4bf`, matching
  origin. All window changes remain local and uncommitted. This window began
  clean; the former `4c16826`/60-file handoff was stale.
- **Working tree:** 91 changed/new files: 74 program files and 17 Markdown files.
  This final closeout changes only this checkpoint; all other 90 file hashes
  match the pre-closeout snapshot. Earlier work and the exact historical archive
  are preserved. No push, PR, publication or deployment. Schema remains 7;
  dependencies are unchanged by closeout. Synthetic runtime fixtures stay outside
  the checkout; static fictional test fixtures remain in Git. No real CV or
  personal runtime was accessed during this usability window.
- **Current freeze:** all 74 program files still match
  `/private/tmp/gapply-materials-preflight-code-freeze-20260921.json`.
  Final hygiene: 91 regular single-link UTF-8 files, 17 Markdown files,
  169 local links and 51 fragments. The current verification below records exact
  commands and results; no gate or subagent implementation remains active.
- **Delivered and locally verified:** all four requested areas have implemented
  usability improvements, described below with explicit limits. All 1604
  regression tests passed with zero skips in 951.950s; eleven source gates,
  fresh installed pilot and sequential maximum-capacity gate passed. No failure
  was waived. Local synthetic verification does not establish public release
  readiness, market-wide coverage, semantic completeness or human time savings.

## Delivered behavior in this window

1. **Discovery without known companies:** `jobs plan-search` emits a pure plan
   from explicit role/location preferences. Codex performs authorized searches,
   inspects actual links and previews feeds without profile or storage setup.
2. **Source setup:** `jobs sources` maps observed Greenhouse, Ashby, Lever,
   Workable company-board and Netflix links to schema-1 manifests offline.
   Deduplication and discarded URL filters are explicit; recognized links select
   whole boards, unsupported links stay manual. Opt-in `--keep-valid` preserves
   usable links and reports invalid/capped inputs together; strict defaults remain.
3. **Workable:** fixed public GETs preserve strict identity, hidden-location
   exclusion, explicit Remote text, partial failures and immutable versions.
   Boardless links remain manual; totals, request bounds and per-instance courtesy
   pacing stay visible. No provider/transport changed in the materials-preflight increment.
4. **Original documents:** text, selectable-text PDF and static LaTeX intake
   replaces mandatory text exports. Local PDF parsing is bounded; TeX is never
   compiled or expanded. Original/extracted hashes and gaps are shown; PDF/TeX
   retention requires the original hash and `--allow-partial` when incomplete.
5. **Complete inventory:** every nonblank extracted line is accounted for as a
   heading, proposal, blocked location or unclassified line. Policy-3 `--select all`
   removes the 80% cap; typed/sensitive/context checks and older histories remain.
6. **Optional interview:** 55 questions, nine topics and three depths, asked a few
   at a time with skip/stop. The catalogue stores no answers, transcript, score or
   progress; explicitly selected statements can be retained through normal import.
7. **Paged review:** `profile review --limit 1..50` shows total/earlier/later
   pending counts and stable continuation after decisions. Skips remain pending;
   all queue provenance is validated before slicing. Reads store no cursor or
   decision; approvals stay audited. Default full review/legacy IDs survive;
   `--after-json` handles nonprinting IDs. The skill starts with five facts.
8. **Research context:** explicit extractor 3 maps Research, Research Experience
   and Research Projects to `research_description` in vocabulary 3. CLI default 2
   and versions 1/2/3 remain; new intake now requests 4. Selected approved resume research
   uses transformation/renderer 3 and Research; Publications stay separate.
   Old facts/materials, unselected or answer-only research preserve their identities.
9. **Correct statement origin:** selected exact answers use user-statement import,
   normal pending review and per-fact approval. Resume ingestion stays the default.
10. **Retained profile inventory:** a read-only counts overview and bounded topic
    detail help Codex resume from supplied facts without dumping unrelated evidence.
    Stored states and exact types remain visible; counts grant no approval or use.
11. **Neutral section boundaries:** explicit extractor 4 stops four observed
    unsupported headings from inheriting the preceding fact type. The heading
    and unclassified body remain visible; document-reading and classification gaps
    stay separate. No new research aliases or stored fact types were added.
12. **Explicit publication grouping:** repeat `--indexes` for separately chosen
    disjoint works. Each retains complete evidence and qualifiers through a
    whitespace-only join. One group preserves the original response; multiple
    groups share one complete source-ordered manifest. All other supported facts
    remain; visible gaps stay in the report. The helper stores and approves nothing.
13. **Standalone location selection:** explicit published-text preferences select
    before the per-source job quota, with visible unknowns and separate selection
    counts. No-profile preview and authorized immutable capture use the same rules;
    provider coverage, saved searches and unfiltered output stay unchanged.
14. **Complete-inventory recipe:** Codex-facing instructions project the closed
    manifest fields, preserve supported proposals and add explicitly classified
    scalar-text unknown rows. Independent review and a fresh typed-CLI walkthrough
    passed; classification, retention and per-fact approval remain separate.
15. **Direct pending-fact review:** select an exact inventory ID to show its current
    evidence/token and the global pending count. Complete-queue audits still run
    in one read snapshot. Default full review and five-fact conversational batches
    retain their contracts; the selector stores or approves nothing.
16. **Materials prerequisite presence:** `doctor --materials` reports PDF-intake
    and material-output prerequisites separately before profile setup. It probes
    only presence; default doctor and functional material checks remain separate.
17. **Release presentation:** README shrank from 527 to 85 lines; linked guides
    retain detail. Direct developer-resume links and a clearly historical archive
    preserve one live checkpoint.

### Keep-valid source setup contract

`jobs sources (--url URL ... | --urls-file FILE) --keep-valid --json` uses
`build_source_manifest_keep_valid` in `src/grounded_apply/services/discovery_sources.py`.
The strict `build_source_manifest`, `SourceSetupReport` and default response stay
unchanged. Recognition remains offline and does not repair URLs or infer boards.
Opt-in data adds `setup_status`, `input_count`, `accepted_input_count` and
`rejected_inputs` containing only original one-based positions and fixed
`invalid_url` / `source_limit_reached` codes. Every input is accounted for once;
file positions refer to nonblank parsed entries, not physical lines.

All accepted means `complete`, `ok:true`, exit 0; some rejected means `partial`,
`ok:false`, exit 2 and fixed `IncompleteSourceSetup`. All rejected means `failed`
with the same error/exit and `manifest:null`, never an empty schema-1 manifest.
Non-null manifests retain the existing schema 1 and feed directly to discovery.
Empty or over-256 input fails at the top level. After 32 accepted distinct sources,
new sources are reported as capped but later retained-source duplicates still count.
Safe unsupported URLs and boardless Workable postings remain manual sources.
No rejected URL or exception text enters reports or fixed diagnostics.

Codex uses one tolerant call and preserves its setup gaps beside later feed
results. It passes only non-null `data.manifest` to unchanged discovery. Default
strict all-or-nothing behavior remains available. Keep-valid setup changed no provider, transport, source manifest, runtime, schema,
migration or diagnostic-enum behavior.

### Planner and conversation contract

`jobs plan-search --role ROLE [--role ROLE] [--location LOCATION] [--json]`
calls `build_public_search_plan` in `src/grounded_apply/services/discovery_plan.py`.
Input bounds apply before exact deduplication: 1–3 roles and 0–2 locations.
Trim outer whitespace; preserve case, punctuation, quotes and valid Unicode.
Controls/line separators, invalid Unicode, blank or over-128-codepoint trimmed
terms fail without echo. Human output escapes format marks; JSON preserves terms.
Eligibility keywords are not candidate facts; diagnostics contain no caller terms.

Schema-1 output contains `roles`, `locations`, `searches` of one-based `position`,
literal `terms` and fixed `domains`, plus `query_count`, `max_queries=9` and
`max_distinct_links=18`. Each role has one location-free row plus each location.
Hosts: both Greenhouse hosts, Ashby, both Lever regions, Workable and Netflix.
`network_requests=0`; `storage_changed`, `profile_read`, `coverage_established=false`.
No file/profile/network/model access, generated URLs, shell strings, rendered
query syntax or source manifest comes from the helper.

Authorized Codex browsing executes the plan and inspects at most 18 distinct
result links. Only actual observed URLs enter `jobs sources`; only non-null `data.manifest`
enters feed preview. Never guess board tokens or turn snippets into snapshots.
Keep manual/invalid/stale/failed/capped leads visible. No browsing means an honest
plan-only outcome; empty results never establish market absence. Preserve exact
published locations: carry an agreed location preference explicitly into standalone
feed preview; browser query terms do not silently become feed filters. Count identical posting URLs once in presentation
without dropping source gaps. Capture/saved searches require an authorized private home.

### Standalone location selection contract

`jobs discover` accepts repeated `--location-contains TERM` and
`--missing-location include|exclude`. Either explicit flag selects data schema 2;
omitting both preserves the entire legacy schema-1 JSON and human presentation.
The existing `DiscoveryService.discover`, `DiscoveryReport` and `SourceReport`
keep their public contracts. `DiscoveryService.discover_with_locations` returns a separate
`LocationDiscoveryReport`; `validate_location_discovery_request` validates title,
location, missing policy and quota before source-file/stdin, runtime or transport.

The report retains `jobs` and `sources`, with `location_contains`,
`missing_location`, `selections` and `filter_method=title_location_substring_or@1`.
Use existing `PreparationFilters`: at most 20 already-trimmed terms of 1–128
codepoints, preserving literal case/text and duplicates. No trimming, synonym
expansion, geocoding, candidate/profile inference or new sensitivity rule is added.
Location terms are casefold substring alternatives; title and location selection
apply together before the selected-job quota. `Remote` can match `Not Remote`;
this does not establish workplace type, geographic eligibility or authorization.

Missing locations default to include and remain explicitly unknown; they bypass
location terms but still pass title filtering. `--missing-location` alone accepts
either policy with no location terms. Invalid blank provider metadata remains an
invalid record, not a new unknown. Human output escapes published location text,
shows retained unknowns and reports selection counts without changing fixed
private diagnostics. Unexpected selection/serialization failures are fixed and
content-free; interrupts retain
existing preview or capture-recovery behavior.

Every input source has one selection row, including all-zero rows for manual,
failed and empty sources: `source_id`, `valid_count`, `title_filtered_count`,
`location_filtered_count`, `unknown_excluded_count`, `unknown_included_count`,
`selected_count`, `selected_unknown_count`, `limit_deferred_count`.

```text
valid_count = title_filtered_count + location_filtered_count
            + unknown_excluded_count + selected_count + limit_deferred_count
```

Unknown-included counts eligible unknowns before quota; selected-unknown is the
selected subset. `SourceReport.filtered_count` totals title, location and unknown
exclusions, not quota-deferred records. Provider `observed_count` remains raw
observations, distinct from valid normalized records and coverage.
Provider errors, indexed/remaining metadata, manual gaps and source scan caps
survive even when no jobs match. No extra fetch fills a filtered quota. Request,
pacing and record bounds, adapters, saved-search policy, schema and persistence
are unchanged. Dry-run opens no profile; capture checks authorized storage before
network and uses existing immutable snapshots/idempotent replay.

### Statement-origin compatibility

`profile import --source-kind user-statement --source-file PATH|-` uses
`--proposals-file PATH|- --retain-all-facts --idempotency-key KEY`; preview with
`--dry-run --json`. Only one stdin; schema-2 binds exact UTF-8 spans/source hash.
This path rejects PDF/LaTeX/auto,
`--document-sha256` and `--allow-partial` before reading inputs.

| Origin | Request | Source identity | Record digest | Stored source type |
|---|---:|---:|---:|---|
| Default resume | 4 | 1 | 1 | `imported_resume` |
| Explicit statement | 5 | 2 | 2 | `user_statement` |

App-owned extractor `grounded-apply.profile-user-statement.manifest@1`, source
`user-statement:sha256:<digest>`, artifact `profile-user-statement-source:sha256:<digest>`
bind origin; decisions inherit the import's digest version. Resume hashes/shapes/
replay, generic statements and manifest/result/record-ID/material/database versions
remain intact. Origin changes cannot reuse a key. Workflow/claim/evidence ownership
is audited through review, decisions, resolution, retirement and historical materials.
No reclassification, implicit approval or saved interview progress.

### Retained inventory contract

`profile inventory [--topic TOPIC [--limit N]
[--after ID|--after-json JSON_STRING]] [--json]` uses the new
`src/grounded_apply/services/profile_inventory.py`. Request validation is pure
and runs before runtime access. The default overview contains all nine topics
and only counts by recorded status/approval pair: no claim IDs, raw types, text,
values, evidence, scope IDs or paths. Unknown types stay Other; old research is
not relabeled. Preferences remain separate from career claims.

One read transaction calls `ProfileService.validated_profile()` once before
counting, filtering or slicing, including audited retirement projection. Every
retained claim counts once, including pending/rejected/retired/generic records.
Off-page or other-topic integrity failure blocks the entire read with a fixed
private error. Borrowed transactions and interrupts remain intact.

The exact type map is contact = candidate_name/contact_email/contact_phone/
contact_location/contact_url; education = education/education_degree/education_field;
experience = employment_dates/employment_description/employment_title;
research = research_description; projects = portfolio_item/project_contribution/
project_outcome; skills = skill_use/language; publications = publication;
achievements = achievement/certification; other = every unknown type.
Schema-1 data includes `total_claim_count`, ordered `topics` with
`{topic, claim_count, states}` and nonzero `{status, approval_status, count}` rows.
Overview has `selected_topic:null`, `items:[]`, `page:null`. Fixed flags are
`read_only:true`, `content_trust:untrusted`, `usability_assessed:false`,
`completeness_assessed:false`, `interview_progress_assessed:false`.

Topic detail includes only `id, claim_type, canonical_text, status, approval_status,
source_type, scope, sensitivity`. It omits evidence, values, source refs, artifacts
and review tokens. All explicit pagination requires a topic; default limit is 20,
exact integer range 1–50. Existing order is `created_at DESC, id ASC`. Page fields
are `limit, total_count, returned_count, before_count, after_count, next_after`.
Before includes the anchor; the three counts sum to the topic total. Exact legacy
UTF-8 IDs, including controls/NUL through JSON, remain valid with no new length or
alphabet constraint. Human output quotes safe continuation and escapes controls.
Unknown/wrong-topic anchors fail with a fixed restart instruction. Decisions and
retirement retain anchors; final anchors can return an empty page. Reads store no
cursor, answer, interview progress or approval. Actual decisions remain audited.

Codex consults the overview and a chosen topic, then asks useful follow-up
questions. Counts do not prove answered questions, extraction/profile completeness
or usable evidence; no automatic question skip or inference from empty topics.
Pending approvals still require existing `profile review` evidence/token and an
explicit decision. New chosen answers use statement-origin import. Existing
show/interview/review responses, schema, vocabulary and material versions remain.
The only new fixed diagnostic command is `profile.inventory`; no caller fields enter
logs. Scope: new service and two test files, CLI/diagnostics/help smoke, existing
onboarding acceptance, guides/skill/checkpoint. No new PDF builds were required.

### Direct pending-fact review contract

`profile review --claim-id ID --json` or `--claim-id-json JSON_STRING` selects one
pending fact already identified in `profile inventory`. The two flags are
mutually exclusive and cannot combine with any pagination flag (`--limit`,
`--after`, `--after-json`). JSON must decode to one exact nonblank UTF-8 string.
Validate all selection arguments before resolving runtime paths or opening storage.
Existing IDs retain spaces, Unicode, slashes, long legacy values and control/NUL
via the JSON-string form; do not trim or add a new UUID/length restriction. Shell-quote
argument values safely; human output escapes nonprinting characters.

`ProfileService.get_review_item(claim_id)` returns frozen `ProfileReviewSelection`
with `item` and positive `pending_count`, after pure
`validate_profile_review_selection_request`. It calls the existing complete
`list_review_items` audit inside one `read_transaction` snapshot. It preserves
borrowed caller-owned transactions and closes its own on success or error.
Requested or unrelated corruption still blocks the read, including managed
statement association loss and malformed generic evidence. Unknown/nonpending
IDs produce the same fixed service refusal: `Requested claim is not pending review.`
The CLI wraps runtime/read/selection/conversion errors in a fixed private refusal,
never echoing the ID, values, evidence or underlying exception.

CLI data contains exactly `items: [existing_item]`, global `pending_count` and
`read_only: true`. No `page`, selector metadata or unrelated item is serialized.
The existing item retains exact evidence, immutable import identity and current
token; supported generic pending items keep null tokens and gain no new decision
authority. Human output shows one selected fact of the total and its evidence,
without page continuation instructions. Usual untrusted/unusable warnings remain.
Serialization/output errors are fixed and private; KeyboardInterrupt/SystemExit
retain their prior handling. No decisions, cursors, runtime or approval are created.
An existing initialized authorized profile is required, as for other review reads.

Default full and paged outputs, item order and tokens remain unchanged. The skill
still starts batch review with five facts; direct review is a convenience when
an inventory fact is already chosen, not a replacement queue or approval workflow.
No schema, diagnostic enum, adapter, import policy, material version or dependency
changed. Only profile service, CLI, two new focused test modules and the shared
onboarding acceptance changed at the program layer.

### Neutral section boundary contract

Extractor 4 adds exactly Research Interests, Academic Research, Selected Research
and Professional Memberships as neutral labels, matched with existing casefold
and trailing-colon normalization. They clear the current section and appear as
`heading` inventory rows with no proposal. Following nonlabel text remains
`unclassified` until a supported section begins. Explicit contact labels still
work; bullets, ordinary prose and near matches are not generic heading detection.
Source spans and wording remain exact; sensitive/instruction guards still apply.

`profile extract` and `profile onboard` accept explicit 4. New-intake examples
and the skill use 4; default remains 2. Complete outputs, proposal indexes and
replay for versions 1/2/3 remain unchanged. Version 4 retains version 3's three
research mappings; no extra research alias, vocabulary/content-policy/manifest/
evidence/material/schema/provider/dependency change occurred. The preview
identifier is `grounded-apply.exact-resume-lines@4`, not a new stored manifest
importer. Same proposals continue through the existing exact-span import path.

A fully readable document can have `document.incomplete:false` and classification
gaps. `--allow-partial` addresses document issues, not unknown career meaning.
`--select all` selects supported proposals while reporting unclassified lines;
unknown text is not silently retained or approved. Codex can explicitly classify
exact lines with the existing structured import and original-document hashes.
The four-label set is not a general cure for arbitrary unsupported headings,
PDF reading order, custom macros or semantic completeness.

New files: `tests/test_neutral_sections.py` (12 tests) and
`tests/test_neutral_sections_cli.py` (5 tests). Changed code is bounded to the
line extractor, CLI version choices/help, shared onboarding acceptance and the
necessary unsupported-extractor-version expectation adjustments in old tests.
Other numeric versions stay unchanged, including rejected vocabulary/policy 4.

### Explicit publication grouping contract

`profile group-publication --source-file FILE --source-format auto
--source-sha256 TEXT_HASH --document-sha256 ORIGINAL_HASH --extractor-version N
--indexes 7,8 [--indexes 10,11] [--allow-partial] --json` groups explicitly chosen
works. Every index refers to the original displayed extraction. Both hashes are
required for all formats, including text; the extractor version has no default.
Hash mismatch or unacknowledged document gaps fail before runtime access.
The helper never infers which adjacent fragments describe the same work.

One `--indexes` occurrence retains the singular API and schema-1 JSON/human output.
Two or more occurrences call `build_publication_groups` in
`src/grounded_apply/services/publication_grouping.py`. Report schema 2 replaces
`grouped_indexes` and `grouped_manifest_index` with
`groups: [{indexes, manifest_index}]`; all other report fields retain their meanings.
The closed import manifest remains schema 2. The pure plural API accepts one group
with a schema-2 report; the CLI deliberately routes a single option to schema 1.

Before source access, validate 1–500 explicit groups and at most 1,000 members
total; each group has 2–1,000 ascending consecutive exact integer indexes from
0 through 999. Refuse overlap and duplicate members/groups; do not merge adjacent
selections. CLI index strings are bounded to 6,000 total characters for multiple
options and retain the previous single-option bound. Accept either request order:
report groups preserve it, while the full manifest and complete index map always
follow source order. Reversing group order yields identical manifest and mapping.

The existing bounded document adapter reads once; extraction and final complete
batch preview occur once. Each group must contain only `publication` proposals
with whitespace-only intervening source gaps. Its exact contiguous first-start/
last-end evidence retains multiline whitespace and Unicode-codepoint offsets;
value/canonical text are only `" ".join(span.split())`. Bullets, punctuation,
words and status qualifiers stay unchanged. Wrong type/out-of-range groups,
non-whitespace gaps or existing content/size limits refuse the complete result;
no partial manifest or truncation is returned.

`data.manifest` contains **all supported proposals**, replacing each selected
group once and preserving all other entries and their order. It projects only
`claim_type`, `value`, `canonical_text`, `span` and optional `confidence`.
Original inventory, skipped/unclassified/blocked counts and document metadata
remain in the report; classification gaps stay distinct from `document.incomplete`.
`read_only` and `review_required` are true; content is untrusted; `storage_changed`
and `profile_read` are false; `network_requests` is zero.

Codex shows the full result. After authorized retention choice, save only
`data.manifest` to a chosen private temporary JSON path outside the checkout,
then use existing `profile import --source-file ORIGINAL --source-format auto
--document-sha256 ORIGINAL_HASH --proposals-file PRIVATE_MANIFEST --retain-all-facts
--idempotency-key KEY`, with `--dry-run` first and normal pending review after
retention. No separate manifests need composition, manual text CV export or Python
operator workaround. The helper creates no runtime, retention choice or approval.
No extractor, stored schema, claim type, material version, document adapter,
provider or generic import policy changed.

Original-document hash remains an invocation guard, not newly stored PDF provenance.
Durable evidence binds extracted-text digest and exact span. Generic pending import
does not prove semantic equivalence of arbitrary proposed wording; the helper
preserves its own whitespace-only transformation. Explicit review still decides
whether the selected fragments belong together and may be approved.

### Materials prerequisite presence contract

`doctor --materials [--json]` is opt-in and branches before runtime resolution.
The unchanged default doctor still checks its existing Python/runtime/database
contract. The new branch independently calls `importlib.util.find_spec("pypdf")`
and `shutil.which("pdflatex")`, returning only fixed names and states `present`,
`missing` or `unknown` when a probe raises. It does not return module origins,
executable paths or exception text. Schema-1 data is closed:

```json
{
  "schema_version": 1,
  "check_method": "dependency_presence@1",
  "dependencies": {"pypdf": "present", "pdflatex": "present"},
  "pdf_intake": {"prerequisites": ["pypdf"], "status": "present"},
  "pdf_materials": {"prerequisites": ["pypdf", "pdflatex"], "status": "present"},
  "functional_tests_run": false,
  "profile_read": false,
  "read_only": true
}
```

The example shows presence only. Each capability is missing if any prerequisite
is missing, otherwise unknown if any is unknown, otherwise present. Exit 0
requires both dependencies present; otherwise exit 2 uses fixed
`MaterialsPrerequisitesUnavailable`. Missing TeX alone can coexist with present
PDF-intake prerequisites. Human output shows the dependencies and both feature
statuses; the batch dependency-recovery hint now says `doctor --materials`.

No runtime/profile/source/SQLite read, provider import, compiler execution,
network access or installation occurs. The feature adds no writes; this is not
an assurance about arbitrary interpreter bytecode behavior. Probe and output
failures stay fixed/private, interrupts retain their behavior, and diagnostic
metadata still uses the existing doctor enum. Presence proves no successful
PDF parsing/rendering, binary compatibility, TeX packages/fonts, layout, base
application health or release readiness. No schema, dependency, material,
provider or profile policy changed.

## Current verification

### Final window closeout

At the September 21 **13:24 UTC** wake-up, current time and mandatory resume
reads were checked. The live First command, `git diff --check`, **PASS, exit 0**.
No new implementation or test-affecting change was made. The completed full,
source, installed and capacity checks below are reused for the identical
74-program freeze; no unchanged gate was rerun merely for the checkpoint.

Before closing, whole-file hygiene and exact-preservation checks both **PASS**
using the two repeatable commands recorded below. A private before snapshot is
`/private/tmp/gapply-window-closeout-before-20260921.json`. The final report is
`/private/tmp/gapply-window-closeout-final-20260921.json`; all 90 other changed/new
files remain byte-identical, as do current contracts, limits, recent operational
evidence and both archived historical blocks. Only this checkpoint changed.
The full incremental checkpoint diff was reviewed; no new personal data,
runtime artifact, debug output or unrelated change was introduced.

The automation tool successfully paused only `grounded-apply-usability-window`
after 13:29 UTC. A fresh settings comparison verified that only its status and
update timestamp changed, and the older development automation remained exactly
unchanged. All gate sessions had exited 0; all independent agents had completed.
A final independent summary review found no overclaim when the documented PDF,
semantic, interview-state, bounded-discovery and local-verification limits are
kept explicit. The user receives the four-area summary; no new task is scheduled.

### Handoff navigation and historical-evidence move

The mandatory First command **PASS, exit 0** created a private external baseline:
`/private/tmp/gapply-handoff-navigation-before-c7ivohqg`; its exact command was
saved as `/private/tmp/gapply-handoff-navigation-first-command-20260921.sh` and
its output as `/private/tmp/gapply-handoff-navigation-baseline-20260921.json`.
It changed no repository files or runtime data.

The two older blocks moved verbatim to `SESSION_HISTORY_2026-09-21.md`:
320 lines / 22,473 bytes, SHA-256
`07fe579cafa556021a6d005755fa3a02e7c5737e9e4a71a46b9c4bbedd04cb06`;
556 lines / 34,128 bytes, SHA-256
`e189ba4504f3f0241ae2395e166681d05805c68f4b6e2059ac236c2fab1f300a`.
Each appears exactly once in the archive and zero times live. Before this
checkpoint refresh, removing the new navigation and restoring the two blocks
reconstructed the 1,665-line baseline byte-for-byte; the live document was 796
lines after the move. The archive is 893 lines, hash
`56e56968809ef78bdc04b3046b450395a8cb50efd940e04f15c3fcf7c25bdf5a`.
No historical wording or relative link needed repair. Current feature contracts,
limits, newest preflight and direct-review operational evidence stayed live.
Evidence: `/private/tmp/gapply-handoff-navigation-move-check-20260921.json` and
`/private/tmp/gapply-handoff-navigation-verified-move-20260921.md`.
Root reconstruction and an independent read-only archive review both passed.

Full regression **PASS: 1604 tests / 951.950s, zero skips**, session 64193
exited 0, verified **13:16:28 UTC**; started **12:59:37 UTC**:
`PYTHONDONTWRITEBYTECODE=1 ./scripts/check`; log
`/private/tmp/gapply-handoff-navigation-full-20260921.log`.
All required gates passed on the same 74-program freeze; no gate remains active
and no failure was waived. The source and installed checks below are separate
from this full regression run.

All eleven source gates **PASS, exit 0**, runner 6433, verified **13:08 UTC**.
Exact command arrays, timings and individual logs:
`/private/tmp/gapply-handoff-navigation-source-gates-20260921.json`.
Repeatable runner:
`PYTHONDONTWRITEBYTECODE=1 sh scripts/python /private/tmp/gapply-handoff-navigation-source-gates-20260921.py`.
Each source command uses `PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error`
followed by the script and arguments below.

| Script under `scripts/` and arguments | PASS / seconds |
|---|---|
| `check_onboarding.py --with-materials` | 57.883 |
| `check_materials.py` | 71.937 |
| `check_backup.py` | 10.139 |
| `check_pilot.py` | 34.733 |
| `check_workable.py` | 16.259 |
| `check_batch.py --with-backup` | 54.728 |
| `check_search.py --with-backup` | 72.880 |
| `check_schedule.py --with-backup` | 73.461 |
| `check_source_window.py --with-backup` | 28.057 |
| `check_search_filters.py --with-backup` | 13.543 |
| `check_source_rotation.py --with-backup` | 27.982 |

Fresh installed pilot **PASS, exit 0**, session 31353, verified **13:06:33 UTC**:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-milestone-wheels-20260918
```

Log: `/private/tmp/gapply-handoff-navigation-package-20260921.log`.
Source archive, wheel contents, isolated installation, entry point, migrations,
preflight and synthetic intake/material/lifecycle/search/daily workflows passed.
Its separate installed capacity smoke (`maximum_probe:false`) passed in
17.226806s with an 18,980,864-byte database, two real generated PDFs, three fixture
GETs and zero real network requests. Exact restoration, unchanged source, quiet
replay and read-only historical material/approval checks with mutation refusal
all passed. It is not the maximum-capacity probe.

Sequential separate maximum-capacity gate **PASS, exit 0 / 82.950514s**, session
43596, verified **13:08 UTC** after starting following installed-pilot completion:

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_storage_capacity.py --workspace /private/tmp/gapply-handoff-navigation-capacity-20260921
```

Log: `/private/tmp/gapply-handoff-navigation-capacity-20260921.log`; summary:
`/private/tmp/gapply-handoff-navigation-capacity-20260921/capacity-summary.json`.
Maximum database 267,415,552 bytes; encrypted archive 356,554,263 bytes; peak
child-process RSS 2,316,992,512 bytes during near-capacity restore replay.
Three real generated PDFs, four fixture GETs and zero real network requests.
Both capacity roundtrips and historical snapshot checks passed with exact
restoration, unchanged sources, quiet replay and mutation refusal. This remains
a byte-capacity fixture, not large material-history scaling, a retention promise
or a machine-wide peak-memory measurement.

Current skill validation and actionlint **PASS**, using the exact commands in the
preceding implementation record below. Root's repeatable preservation check
**PASS**:
`PYTHONDONTWRITEBYTECODE=1 sh scripts/python /private/tmp/gapply-handoff-navigation-preservation-check-20260921.py`;
matching JSON records archive hashes and exact current contracts, limits, prior
code verification and recent operational evidence. All-file hygiene **PASS**:
`PYTHONDONTWRITEBYTECODE=1 sh scripts/python /private/tmp/gapply-handoff-navigation-final-hygiene.py`.
It checks all 91 changed/new files, 74 frozen program hashes, 17 Markdown files,
169 local links and 51 fragments, regular single-link UTF-8 files and
`git diff --check`. No program file changed in this increment. Root reviewed the
complete move and every incremental documentation change; the staged diff is
empty. No accidental personal data, generated runtime artifact, debug output or
unrelated edit was found. Archive bytes and the full prior feature-contract,
limits, preflight-verification and recent operational-evidence blocks remain exact.

Independent read-only archive and checkpoint-delta reviews found no issue.
A separate first-minute navigation confirmation reached current verification,
limits, next task and First command directly from README/CONTRIBUTING/live links;
it used inherited context and is not blind first-use testing. The historical
wrapper points back to the one live authority, and the completed archive move
is no longer a next-task instruction. A read-only release-claims audit of README,
PROFILE_SETUP, JOB_DISCOVERY and RELEASE_READINESS found no substantive overclaim;
it did not establish release readiness, market coverage or active user-time
savings. No runtime, network, CLI or application test was used by these reviewers.

### Materials-prerequisite implementation verification (12:24–12:49 UTC)

The following records the preceding code increment. Its code remains unchanged;
its 90-file/84-line-README counts describe that earlier documentation snapshot.

Mandatory baseline **PASS: six tests / 1.947s, zero skips**:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest \
  tests.test_cli.CliTests.test_doctor_reports_uninitialized_without_writing \
  tests.test_cli.CliTests.test_doctor_rejects_runtime_paths_inside_checkout \
  tests.test_cli.CliTests.test_doctor_rejects_a_future_database_schema \
  tests.test_batch_cli.BatchCliTests.test_shared_failure_returns_validated_progress_and_resumes \
  tests.test_resume_documents.ResumeDocumentTests.test_pdf_timeout_missing_dependency_and_malformed_result_are_fixed_errors \
  tests.test_logging.DiagnosticTests.test_unknown_arguments_errors_and_warnings_never_enter_event_stream \
  -v
```

Log: `/private/tmp/gapply-materials-preflight-baseline-20260921.log`.
Focused preflight/batch/logging **PASS on first run: 25 / 11.541s, zero skips**:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_materials_preflight_cli tests.test_batch_cli tests.test_logging -v
```

Log: `/private/tmp/gapply-materials-preflight-cli-initial-20260921.log`.
Nine new test methods cover all four presence combinations, five unknown/missing
combinations with missing precedence, exact closed data and fixed human output,
no provider/process/network/runtime access, private probe/output/serialization
errors, interrupts, help/usage and preincrement default-doctor human/JSON byte
comparisons under fixed paths/versions. The existing batch test adds only the
corrected recovery-hint assertion. No initial failure or production waiver.
Independent incremental CLI/test and installed-acceptance reviews, plus root's
complete increment review, found no actionable issue.

Initial base fresh installed package **PASS, exit 0**:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py
```

Log: `/private/tmp/gapply-materials-preflight-package-base-20260921.log`.
The gate built source archive/wheel, checked guarded schema-4–7 capture and
isolated installed CLI/migrations/decisions/replay, and passed the prior synthetic
workflow plus read-only snapshot reads/mutation refusal. Before profile init,
the actual installed preflight accepts only exit 0/2 consistent with its result
and proves the runtime absent. Eight controlled installed states exercise
missing and unknown prerequisites with import/process/network/runtime guards.
This runs without a pilot wheelhouse, installs no optional dependency and builds
no extra PDF. It does not substitute for the full pilot-package gate below.

Full: `PYTHONDONTWRITEBYTECODE=1 ./scripts/check` — **PASS: 1604 tests /
952.874s, zero skips**, session 58329 exited 0, verified **12:48:02 UTC**.
Log: `/private/tmp/gapply-materials-preflight-full-20260921.log`.
Final source gates **PASS: all eleven exit 0**, session 28516 completed and
verified at **12:41 UTC**. Exact command arrays, timings and individual logs:
`/private/tmp/gapply-materials-preflight-source-gates-20260921.json`.
Prefix each source script with
`PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/`.

| Script and arguments | Result / wall time |
|---|---|
| `check_onboarding.py --with-materials` | PASS / 56.432s |
| `check_materials.py` | PASS / 69.813s |
| `check_backup.py` | PASS / 9.645s |
| `check_pilot.py` | PASS / 34.101s |
| `check_workable.py` | PASS / 16.082s |
| `check_batch.py --with-backup` | PASS / 53.772s |
| `check_search.py --with-backup` | PASS / 72.661s |
| `check_schedule.py --with-backup` | PASS / 73.742s |
| `check_source_window.py --with-backup` | PASS / 28.872s |
| `check_search_filters.py --with-backup` | PASS / 13.043s |
| `check_source_rotation.py --with-backup` | PASS / 27.911s |

Full fresh installed package **PASS: session 30480 exited 0**, verified
**12:43:10 UTC**; started **12:35 UTC**:

```bash
PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python scripts/check_package.py --pilot-wheelhouse /private/tmp/grounded-apply-milestone-wheels-20260918
```

Log: `/private/tmp/gapply-materials-preflight-package-20260921.log`.
Final PASS covers source archive, wheel contents, isolated installed CLI,
migrations, the actual new presence branch and eight controlled states before
profile setup, plus all existing synthetic workflows. Installed onboarding,
Workable, publication grouping, direct review, materials/lifecycle, pilot, batch,
search/daily, source windows, filters and rotation passed with their real PDF
checks. Its separate capacity smoke (`maximum_probe:false`) passed in
**16.857556s**: maximum database **18,980,864 bytes**, two generated PDFs, three
fixture GETs and zero real network requests. It reports exact snapshot restore,
unchanged source, quiet replay and exact read-only historical inventory/material/
approval-record/approval-eligibility checks with mutation refusal. This smoke
is distinct from the near-capacity maximum probe below.

Sequential separate near-capacity probe **PASS: exit 0 / 80.893973s**, session
51973, completed **12:44:43 UTC** after starting about **12:43:20 UTC** following
package completion:

```bash
PYTHONDONTWRITEBYTECODE=1 sh scripts/python -W error scripts/check_storage_capacity.py --workspace /private/tmp/gapply-materials-preflight-capacity-20260921
```

Log: `/private/tmp/gapply-materials-preflight-capacity-20260921.log`;
summary: `/private/tmp/gapply-materials-preflight-capacity-20260921/capacity-summary.json`.
`maximum_probe:true`; maximum database **267,415,552 bytes**, encrypted archive
**356,554,263 bytes**, peak child-process RSS **2,439,135,232 bytes** during
near-capacity restore preview. Three real generated PDFs, four fixture GETs and
zero real network requests. Both beyond-old-cap and near-capacity roundtrips
report exact snapshot restoration, unchanged source and quiet restored replay.
Their snapshot reads report exact historical inventory/material/approval-record/
approval-eligibility checks, blocked mutations and unchanged sources. These are
byte-capacity fixtures with large fictional job text; they do not measure
thousands of material records, promise retention duration or report machine-wide
peak memory. Root and documentation reviewer read the complete current summary. All required
code gates have passed on the same 74-program freeze; no gate remains active.

Owned four-guide/staged-skill links **PASS: 90 local links / 21 fragments**;
README is 84 lines and `git diff --check` is clean. Stage is
`/private/tmp/gapply-materials-preflight-SKILL.md`; root installed it through the
protected-path boundary. Current skill validator and actionlint both **PASS**.
Exact commands remain:
`PYTHONDONTWRITEBYTECODE=1 /private/tmp/grounded-apply-milestone-tools-20260918/bin/python "$HOME/.codex/skills/.system/skill-creator/scripts/quick_validate.py" .agents/skills/grounded-apply`;
`/private/tmp/gapply-actionlint-1.7.12 -shellcheck= -pyflakes= .github/workflows/check.yml`.
Root whole-tree hygiene after protected-skill and checkpoint installation **PASS: 90 regular
single-link UTF-8 files / 74 matching frozen program hashes / 16 Markdown files /
157 local links / 42 fragments**. Evidence:
`/private/tmp/gapply-materials-preflight-final-hygiene-20260921.json`.
Repeatable command:
`PYTHONDONTWRITEBYTECODE=1 sh scripts/python /private/tmp/gapply-materials-preflight-final-hygiene.py`.
Root reviewed the complete incremental diff and all new files. Previously
reviewed program files are unchanged except CLI and package acceptance; the
existing batch test gains one assertion and the preflight test module is new.
No accidental personal data, generated runtime files or unrelated changes were
found. `git diff --check` is clean; all 74 program hashes still match.

## Conversational evidence

Current materials-preflight operational confirmation **PASS**:
`/private/tmp/gapply-materials-preflight-forward-5bfg_tb2/evaluation.md`,
`summary.json`, `exactcommands.txt` and `audit.jsonl`. This is fresh operational
confirmation, **not blind independent testing**: the evaluator inherited context
and reviewed installed acceptance. It used current staged skill/guides and actual
help with a new private external workspace, without reusing previous outputs.
The report's pending-doc wording describes the draft it saw; those availability
labels were removed after focused and base installed verification passed.

Four actual CLI calls—root help, doctor help, host JSON preflight and restricted-
PATH JSON preflight—exited 0, 0, 0 and 2. The host found both prerequisites.
Restricting PATH to `/usr/bin:/bin:/usr/sbin:/sbin` while keeping the identical
trusted `GAPPLY_PYTHON` made `pdflatex` missing while `pypdf` and the intake status
remained present. No fake executable or probe mock was used. The separate feature
statuses make it clear that missing TeX alone need not block an independently
authorized PDF-intake attempt; this trial read no CV and proved no parsing success.
Both responses keep `functional_tests_run:false`, `profile_read:false` and
`read_only:true`. No concrete flow friction was found.

The runtime stayed absent. The audit recorded eight interpreter starts, four
correct source CLI origins and four fixed-schema doctor diagnostic events,
without private paths or dependency names in diagnostic stderr. There were zero
provider-import, compiler/CLI-child, network or runtime-open attempts; `pypdf`
was never loaded. Shell/interpreter startup is necessary to run the CLI and is
not included in the forbidden child-action claim. No profile, document, material,
retention, approval, installation, repository edit or test/gate action occurred.
This verifies the synthetic presence flow and explanatory boundaries, not all
missing-dependency combinations, a filesystem lock or functional PDF readiness.

Previous direct-review fresh operational confirmation **PASS**:
`/private/tmp/gapply-direct-review-forward-CDZ5qafr/evaluation.md`, `summary.json`,
`exactcommands.txt`, `selected-review.json` and matching runtime snapshots.
The evaluator authored acceptance and inherited project context: this is **not
blind independent testing**. It used current guides/staged skill and actual CLI
help/output only, with new fictional text and a fresh external runtime; no prior
operational output, implementation/module inspection or direct database query.

Eleven actual CLI calls all exited 0 on the first intended pass: two helps;
extraction, retention preview, profile initialization, doctor and authorized
pending retention; counts-only overview and Publications inventory; direct review
and a fresh-process same-ID resume. Exactly six facts were retained pending,
zero approved. Initialization and pending retention were the only intentional
runtime mutations. No decision command was called.

The publication ID from inventory returned exactly one existing claim, one exact
evidence record, its current review token and global pending count six. The
submitted/under-review/not-accepted qualifier remained verbatim. No full-queue
or predecessor lookup was made. The targeted-review phase serialized no unrelated
claim/evidence records; overview/topic category counts remain visible. Initial
onboarding did show all six source facts under the scripted full-retention choice,
so zero unrelated serialization is not a claim about the whole conversation.

A private synthetic evaluator checkpoint held the chosen ID to model a known
prior/conversational ID. A new process used `--claim-id-json` and returned exact
equal data, including evidence/token/status/count. This fixture is not product-
created interview/session/cursor persistence and does not prescribe saving a
profile summary. Normal missing/nonpending recovery is refresh inventory, not
unrelated full-queue fallback; the valid-ID trial needed no recovery detour.

Source bytes and size/security/identity/mtime/ctime were unchanged per call;
runtime byte/identity/mode/mtime/ctime snapshots matched across inventory and both
reads, with atime excluded. The hook observed 22 interpreter starts, 11 correct
source CLI origins, zero real network/compiler/child-process attempts and 22 fixed
private diagnostics. No new material/PDF, approval, test/gate or repository edit
occurred. No concrete friction remained. This confirms the documented synthetic
flow, not independent re-verification of every corruption/generic-ID/paging case,
actual human time savings, qualification or completeness.

Earlier conversational trials and completed verification are preserved in the
[historical evidence archive](SESSION_HISTORY_2026-09-21.md). Those results describe
earlier snapshots; the current verification above remains authoritative.

## Limits and deferred work

- Materials preflight checks presence only. Present modules/executables can still
  fail to parse/render; fonts, TeX packages, binary compatibility and layout are
  untested by this command. Missing/unknown optional prerequisites do not prove
  base CLI failure. Ordinary doctor remains a separate runtime check.
- Source-line accounting is not semantic completeness. OCR, DOCX and arbitrary
  TeX expansion remain absent. PDF reading order needs review; resource limits
  are not a universal memory sandbox. Original-document metadata is preview-only;
  durable provenance retains exact extracted spans and digest identity.
- Generic pending import validates exact evidence and typed/content bounds, not
  semantic equivalence between evidence and canonical/value wording. A qualified
  evidence span can accompany a title-only pending proposal in dry-run. Explicit
  review remains essential; the grouping helper preserves its own output words
  without changing the generic import contract.
- Four neutral labels do not recognize arbitrary unsupported headings. PDF
  wrapping can split a publication title and its status into separate proposals;
  do not count fragments as separate works or drop an under-review/not-accepted
  qualifier during selection. The explicit helper joins separately chosen disjoint groups; it does not infer
  relationships or automatically group all publications.
  Unknown/blocked lines are not a saved intake ledger; interviews save no progress.
  Teaching/volunteer/service/summary classification is unchanged. Headings prove
  no employer, paid status, dates or eligibility. Approval remains explicit.
- Literal standalone location filters select among already-fetched bounded records,
  not additional feeds. Unknowns remain unknown; `Remote` can match `Not Remote`.
  No geocoding, suitability or candidate-eligibility inference is provided.
- Source finding and configured feeds do not establish LinkedIn/Indeed/Simplify
  coverage parity. Workable totals/pagination remain unverified; unexpected root fields
  can refuse future changes. Browser filling, outbound messages and submission are absent.
- Hosted OS/Python matrix and active user-time savings remain unverified. Local
  checks use macOS/Python 3.12.14. Preserve 256 MiB database/384 MiB archive bounds,
  whole-buffer backup memory costs and sampled same-UID TOCTOU limits.
- Strict search-checkpoint raw row/JSON custody remains deferred: a synthetic
  duplicate phase key survived parsed-value/hash checks. Full checkpoint/schedule
  custody, aggregate workflow inventory, active-lease admission, production sharing
  and new-home conversion are unfinished. Prior storage work is at HEAD.
- No Recruitee or SmartRecruiters adapter was added. Recruitee's
  [authentication reference](https://docs.recruitee.com/reference/authentication-1)
  requires a company JSON token from February 10, 2027; its
  [XML exemption](https://support.recruitee.com/en/articles/8213076-faq-api) needs
  separate review. SmartRecruiters' [platform policy](https://developers.smartrecruiters.com/docs/the-smartrecruiters-platform)
  links [SAP API policy](https://help.sap.com/doc/sap-api-policy/latest/en-US/API_Policy_latest.pdf),
  whose section 2.2.2 requires an endorsed authorization route. Public access alone is insufficient.

## Next exact task

**Await the user's review; no unattended implementation is authorized beyond
this closed-out window.** The scheduled usability follow-up is paused. Preserve
all uncommitted work. Do not automatically restart it, push, publish or turn
local verification into a release claim.

For the next user-authorized development session, follow AGENTS.md and run the
First command below. The concrete proposed next milestone is a supervised
first-use acceptance review from `docs/RELEASE_READINESS.md`: a reviewer who has
not seen the implementation follows README and the workflow on a new fictional
CV and one fictional posting, records active user effort and remaining friction,
and reviews the generated package. This is **planned, not started**. Keep any
fixtures outside the checkout. Hosted platform checks and the other limitations
above remain separate unfinished work; do not silently expand this milestone.

## First command

After the mandatory resume reads, run this read-only baseline from the repository
root. It checks whitespace errors without opening any runtime or changing files:

```bash
git diff --check
```

This command passed during final closeout. If it fails, inspect and record the
exact file/line before changing anything; preserve the existing user's and
agents' work. Further work requires the user's next scope or explicit resumption.
