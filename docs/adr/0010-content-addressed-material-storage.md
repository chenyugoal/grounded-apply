# ADR 0010: Exact-byte material storage and conversion into a new home

- **Status:** Proposed; planned, not accepted or implemented
- **Date:** 2026-09-20
- **Related:** [ADR 0003](0003-encrypted-profile-backup.md),
  [ADR 0004](0004-portable-data-deletion.md),
  [ADR 0008](0008-versioned-resume-presentation.md), and
  [ADR 0009](0009-daily-discovery-and-draft-queue.md)

## Problem and measured scope

The current schema stores PDF, LaTeX and extracted text in every immutable
`material_versions` row. Distinct jobs need distinct material identities and
evidence bindings even when the rendered bytes happen to be identical.

A synthetic workload using one shared approved selection completed twenty daily
runs and 200 real, one-page PDFs. Its database reached 16,023,552 bytes. Day 21
raised `ScheduleCapacityError` before a new occurrence, network request or write.
The remaining 753,664 file bytes were below the 768 KiB schedule reserve. All 200
materials, read-only review and encrypted restoration remained valid. This is
one fixture's observed boundary, not a general twenty-day capacity guarantee.

Read-only inspection found that `material_versions` occupied 12,292,096 bytes.
Its PDF values totaled 10,662,600 bytes with one unique byte digest; LaTeX totaled
626,600 bytes, also with one unique digest. Job identities, manifests and workflow
records were still distinct. Documents with different approved selections or
presentation may share much less content.

An external, in-memory feasibility experiment interned the three payload kinds
and reconstructed all 200 original material records exactly. All 34 other data
tables remained logically identical. Its three unique payloads totaled 56,724
bytes. Allocation remained 16,101,376 bytes before compaction, then fell to
4,706,304 bytes after compaction, 70.63% below the source. Integrity and foreign
key checks passed; the original database was unchanged. The experiment used an
unregistered scratch schema, which product snapshot validation correctly refused.
It did not implement a migration, current-fact validation, a filesystem workflow,
or a supported command. The fixture had no material approvals or applications;
those require separate acceptance fixtures.

Reproduction artifacts are synthetic and local to the development session:
`/private/tmp/gapply-synthetic-daily-capacity-zr0kddk2/FINDINGS.md` and
`artifact-intern-prototype.json` in that directory. They are evidence, not a
runtime dependency; the future acceptance gate must generate its own fixtures.

## Constraints retained by this proposal

- The database snapshot limit stays 16 MiB and the encrypted archive limit stays
  24 MiB. Current material, search and schedule reserves are 256, 512 and 768 KiB.
  File headroom remains different from capacity for a particular next operation.
- Backup remains one complete SQLite database, encrypted through the existing
  format and provider. External exports, source documents, config and logs retain
  their documented exclusions. No external artifact tree is introduced.
- Every job version, material ID, bundle digest, selected claim mapping, question
  bundle, approval, submission reference and append-only event remains intact.
  Storage conversion neither grants approval nor changes evidence authority.
- Per-record deletion, automatic retention, lossy compression, document rewriting,
  semantic deduplication, and increasing capacity limits are separate decisions.
  Historical checks and current-fact checks retain their existing boundaries.
- Filesystem privacy, exact registered SQL schemas, migration checksums, foreign
  keys, read-only sidecar/WAL refusal and sampled same-UID TOCTOU limits continue
  to apply. Diagnostics contain fixed outcomes and aggregate counts, never bytes
  from documents, profile facts, arbitrary paths or raw exception text.

## Proposed artifact representation

After the conversion-only release boundary below is established, add a
registered migration, provisionally schema 008, with an immutable payload
table keyed by **payload kind and SHA-256**. Each row records its exact byte length
and bytes. The first scope has only PDF, LaTeX UTF-8 and extracted-text UTF-8.
Material records reference one payload of each required kind through constrained
foreign keys. Per-job structure, manifest, validation, bundle identity, timestamps
and workflow links remain per material. No historical bundle is rehashed into a
new identity merely because its physical storage changed.

Repository reads reconstruct the existing material record shape, so material
validation, export, approval and application services receive the same values as
before. UTF-8 round trips must preserve exact text: no normalization, trimming,
line-ending conversion or regeneration is allowed. Repeated insertion verifies
kind, digest, length **and byte equality** before sharing an existing row. A hash
is an index and integrity check, not authentication or proof of candidate truth.

Every payload read validates its length and digest. Existing renderer, bundle,
workflow, claim-to-output, question and approval checks still run at their normal
boundaries. Corrupt shared bytes fail every affected material closed; sharing
must not downgrade corruption to an ordinary job blocker. Missing, wrong-kind,
cross-material or mutated references likewise fail closed. Artifact rows and
material references cannot be updated or deleted through ordinary operations.
Unused payload collection is outside this increment.

The migration must account for all explicit keys and references. Nothing may
depend on an implicit SQLite row ID or physical row order. After release, empty
databases use the registered new schema. An existing database must not silently undergo this
table rewrite during an ordinary read, job run or backup.

## Proposed conversion contract

Expose a separate, explicit conversion into a **new private portable home**.
CLI spelling and service types remain to be designed; this document introduces
no available command. A normal restore remains an exact snapshot restore and
does not silently compact, deduplicate or migrate it.

1. Preview validates an explicit source, explicit new target and supported source
   schema through guarded adapters. Capture one consistent bounded snapshot;
   never copy a live database file directly. Refuse active workflow leases,
   unsafe paths, sidecars, persistent WAL, unsupported schemas or broken custody.
   The legacy capture path must explicitly allow registered versions; do not
   weaken the normal repository's current-schema requirement.
2. Validate the snapshot's exact SQL schema before reading application tables.
   Migrate only the isolated in-memory copy using trusted local migration code,
   intern exact payloads, then compact that copy. Validate the resulting schema,
   all references, profile import/review/retirement provenance and complete
   historical material custody, including every existing approval. Legitimately retired
   evidence and unanswered required questions remain historical records; they
   must not be approved, dropped or silently repaired to permit conversion.
3. Compare the complete logical before/after inventory. Preserve original IDs,
   factual values, per-material reconstructed bytes, bundles, decisions, scopes,
   source cursors, leases, checkpoints and notification acknowledgments. Report
   source/result snapshot hashes, schema versions, counts, size and limitations.
   Preview creates no target or receipt and changes no source bytes.
4. Confirmation binds the reviewed source snapshot, target, transformation version
   and preview result. Recapture and revalidate before publishing; changed input
   requires a new preview. Bound source/result snapshots by 16 MiB and specify
   finite working-memory, time and page-count budgets before implementation.
   Refuse an output that exceeds existing capacity or fails validation.
5. Create the new home using restore-style private, exclusive, no-follow writes,
   safe default config and fsync ordering. A separate versioned conversion receipt
   binds source and result hashes and is written last. The registered runtime
   layout, replay and whole-home deletion inventory must recognize this receipt.
   Do not reinterpret an existing restore receipt or copy stale receipt hashes.
6. Validate the persisted target and exact retries. A complete, unchanged target
   can replay its receipt; a pre-existing, partial or changed target fails without
   overwrite, repair or deletion. Failure leaves the source unchanged and any
   incomplete target private. Retry interrupted creation into a new destination.

This is a point-in-time conversion, not a merge of two evolving profiles. It does
not retarget external wake-up mechanisms, switch `GROUNDED_APPLY_HOME`, modify the
source's schedules or delete the old home. Preview must disclose enabled schedules
and pending work. The activation workflow must ensure the operator selects one
home and separately redirects its authorized runner; two writable copies do not
constitute coordinated execution. Repeated source checks cannot promise atomic
exclusion of a same-UID writer after the final check. Race tests and that residual
limitation are part of the acceptance contract, not grounds for an implicit
source mutation or a stronger security claim.

Compaction belongs in the conversion because unused pages still occupy the
database file. SQLite documents that VACUUM repacks such space and can alter
implicit row IDs; rebuilding a live file can also require extra temporary space.
The design therefore requires a bounded isolated copy and explicit logical
identity checks. Direct `VACUUM INTO` to a user pathname is not a replacement for
the application's exclusive filesystem guards. [SQLite VACUUM documentation](https://www.sqlite.org/lang_vacuum.html)
and the [online backup API](https://www.sqlite.org/backup.html) describe the
underlying mechanisms; their guarantees do not establish this product workflow.

## Compatibility and failure boundaries

Existing version-4 through version-7 archives remain authenticated, inspected
and restored at their original schema. The new version is added to snapshot
validation only after its exact registered SQL schema and migration ledger pass
tests. Older application versions must reject the new schema. The archive
format need not change if the same bounded database envelope remains valid;
the supported-schema allowlist still changes explicitly.

The conversion's first material-storage slice targets a validated version-7
database. Earlier supported schemas need a tested sequence of trusted migrations
inside the isolated copy; otherwise conversion refuses them while ordinary
restore remains available. Existing-file upgrade behavior must be explicit and
tested before schema 008 becomes the default. A near-capacity profile must never
need an in-place rewrite or an increased limit merely to reach the conversion.

Refuse malformed or mismatched artifact digests, lengths and kinds; invalid UTF-8;
missing references; corrupt source workflows or approvals; altered schema or
migration history; future versions; incomplete receipts; changed source/target
identity; exhausted budgets; and interrupted writes. A shared artifact failure
must remain visible in full review and saved-search history. Failed conversion
does not authorize cleanup, approval, recovery from a journal, or fallback to
unprotected files. No promise of secure erasure or power-loss testing is added.

## Current implementation boundaries and touchpoints

The existing adapters supply some guards, but do not yet implement this
conversion. In particular:

- `_schema.load_migrations` requires exactly migrations 001 through
  `LATEST_SCHEMA_VERSION`. Adding 008 while leaving the latest version at 7
  makes ordinary opens fail; raising it to 8 makes writable initialization
  attempt an in-place upgrade. `initialize_schema` executes SQL statements and
  checks the 16 MiB allocation bound before each migration commits, before any
  later compaction. It has no registered Python data-transformation stage.
  A conversion copy needs its own finite temporary-allocation budget; this must
  not weaken the live-runtime or final-snapshot cap.
  Registration and activation therefore require a tested conversion-only
  policy first. Existing-file initialization must not enter this rewrite; do
  not ship a version bump before the safe conversion path is available.
- `SQLiteRepository.initialize`, `_require_initialized` and `__enter__` require
  the current schema. `LocalBackupStorage.capture_profile` uses that public
  adapter, so it cannot become the legacy capture path merely by accepting a
  version argument at the service layer. Add a dedicated guarded capture path
  for explicitly supported source versions, retaining identity, sidecar/WAL,
  snapshot-size and timeout checks. Normal opens must stay strict.
- `validate_profile_snapshot` compares exact registered SQL, migration history,
  integrity and foreign keys. It does **not** validate full profile provenance,
  material PDFs, bundle bindings or approvals. Its defensive connection is
  closed on return, and no public validated in-memory repository constructor
  currently exists. Conversion needs a deliberate isolated-copy adapter and
  complete historical audit; ordinary current-readiness checks would wrongly
  reject legitimate retired facts. The generic `artifacts` table is restricted
  to profile-import digest records by snapshot validation, so payloads belong
  in a dedicated table, not that existing artifact registry.
- `restore_profile` and `_verify_restored_target` bind exact snapshot bytes to
  an archive digest and `restore-receipt.json`. They cannot represent conversion
  by substituting a new snapshot or receipt. Reuse their private-file primitives
  under a separate publisher and verifier. Whole-home deletion currently
  allowlists only the database, default config and optional restore receipt;
  the new conversion receipt needs explicit inventory support.

The implementation map is intentionally narrow. Existing material consumers
should retain their record contract rather than learn the physical layout.

| Boundary | Production entry points | Focused verification |
|---|---|---|
| Payload insertion and reconstruction | `repositories/sqlite.py`: `insert_material_version`, `get_material_version`; new bounded payload helper | New `tests/test_material_artifact_storage.py`: exact sharing by kind/bytes, distinct jobs and bundles, changed payloads, invalid UTF-8, missing/wrong-kind/corrupt references, transaction rollback |
| Registration and conversion-only migration | `repositories/_schema.py`: `load_migrations`, `initialize_schema`; public repository initialization; eventual migration 008 | `tests/test_schema.py`, `tests/test_migration_capacity.py`: ordinary version-7 files stay untouched, fresh target construction, future refusal, ledger checks and bounded isolated conversion |
| Snapshot compatibility and legacy capture | `repositories/snapshots.py`, `SQLiteRepository.snapshot_bytes`, dedicated guarded legacy capture adapter | `tests/test_backup_schema_compatibility.py`, `tests/test_backup.py`: exact v4–v7 restore, new-schema checks, bounded consistent capture, unsafe SQL/sidecar/WAL refusal |
| Full conversion custody | New conversion service and isolated-copy adapter; existing profile, material, application, batch, search and schedule validators | New `tests/test_profile_conversion.py`; retain `tests/test_materials.py`, `tests/test_applications.py`, `tests/test_batch_search_history.py`, `tests/test_search_history.py` and `tests/test_search_review_exports.py` |
| New-home publication and recovery | New conversion filesystem adapter using `backup_files.py` primitives; `deletion_files.py` receipt inventory | New `tests/test_conversion_files.py` plus `tests/test_deletion.py`: confirmation binding, exclusive creation, interrupted writes, changed targets, exact receipt replay and deletion inventory |
| Operator and installed contract | Future CLI/diagnostic command; `scripts/check_package.py` migration inventory; unchanged encrypted archive service | New `tests/test_conversion_cli.py` and real-PDF/encrypted conversion gate; full and fresh installed-package gates |

The full audit must include every historical approval, even for a partial or
retired bundle. Reconstructed records must contain the original PDF bytes and
exact LaTeX/text strings; payload references must not change their public shape.
Insertion remains inside the material transaction, so a failed material or
capacity guard rolls back newly interned payloads as well as the material row.

## Bounded acceptance gate and next task

Before marking conversion or expanded storage implemented, generate a new external synthetic
fixture and run an actual CLI gate with the real renderer and encrypted backup
provider. The bounded gate must demonstrate:

1. Two hundred material versions with repeated payloads compact below their
   original allocation, with one row per exact payload/kind. Include genuinely
   different PDFs, LaTeX and text so similar content is never merged. Report
   measured bytes; require no universal compression ratio or retention duration.
2. Exact reconstructed values and unchanged identities across every material and
   all non-storage records. Include approved and unapproved bundles, retired
   evidence, required-answer blockers, submission snapshots, interrupted child
   checkpoints, source-window generations and acknowledged/pending notices.
3. Every converted material passes historical PDF and custody validation. Current
   reuse/readiness still rejects retired or insufficient evidence. Old and new
   encrypted archives round-trip without changing approval or application state.
4. Read-only preview and refused inputs leave source and target untouched. Test
   substitution, links, broad permissions, WAL/sidecars, forged receipts, changed
   preview input, crash points, exact replay and source writes during conversion.
5. Missing, corrupted, wrong-kind and deceptively reused payload references fail
   closed. Source/output/memory/time bounds refuse without an oversized runtime
   write. Full and installed-package gates cover migration and old-schema restore.
6. After explicit activation of the converted fixture, an unchanged daily run
   reuses valid history without duplicate PDFs or notices; a new posting creates
   its own material and resumes existing queue semantics. No external submission,
   new approval, automatic deletion or personal schedule setup occurs.

The next bounded task, **after this proposal is accepted**, is an isolated
exact-byte payload primitive in a new
`src/grounded_apply/repositories/material_payloads.py`, with synthetic
in-memory table fixtures in `tests/test_material_artifact_storage.py`. It should
intern and reconstruct PDF/LaTeX/text payloads with kind, length, digest and byte
equality checks, and prove rollback and corruption behavior. Keep production
schema 7, the migration directory, normal repository reads/writes, filesystem
publication and CLI unchanged in this first slice. This is a preparatory internal
primitive, not an available conversion or a storage-capacity release.

Begin with these exact inspections and existing boundary regressions:

```bash
sed -n '22,46p' migrations/004_application_pilot.sql
rg -n 'insert_material|get_material_version|load_migrations|initialize_schema|snapshot_bytes|restore_profile' src/grounded_apply/repositories
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src sh scripts/python -W error -m unittest tests.test_schema tests.test_backup_schema_compatibility tests.test_migration_capacity -v
```

After that primitive is verified, design and test conversion-only registration
and guarded legacy capture, then integrate the explicit conversion and target
publisher. Add migration 008 and activate its default only as part of the gated
release that can preserve and convert existing profiles safely. The external
experiment establishes feasibility of byte interning and compaction; it is not
that release evidence.
