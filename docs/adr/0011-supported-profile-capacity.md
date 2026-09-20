# ADR 0011: Supported profile capacity for repeated daily use

- **Status:** Accepted; release verification is recorded in SESSION_HANDOFF.md
- **Date:** 2026-09-20
- **Related:** [ADR 0003](0003-encrypted-profile-backup.md),
  [ADR 0009](0009-daily-discovery-and-draft-queue.md),
  [ADR 0010](0010-content-addressed-material-storage.md)

## Decision

Raise supported database snapshots and automatic discovery/preparation storage
from 16 MiB to **256 MiB**. Raise the encrypted archive allowance from 24 MiB to
**384 MiB**, retaining room for Fernet's Base64 encoding and authenticated
envelope. Existing checkpoint reserves and the ninety-percent advisory remain.
Keep resume imports, public HTTP responses, individual documents and review
exports at their existing independent limits.

The earlier 16 MiB limit was a conservative lifecycle implementation bound. A
synthetic daily workload exhausted it after 200 PDFs. Retaining that bound makes
normal accumulated history a recurring obstacle. Exact-byte document sharing is
useful but requires a separately verified migration and is not a prerequisite
for increasing capacity. No schema change, conversion, history removal or
regeneration is needed for this release.

Keep archive format 1, Argon2id/Fernet and exact registered schema-4–7 restoration.
New code reads existing supported archives. Older software still refuses
snapshots/archives above its smaller allowance; this release does not make them
compatible with older executables. No automatic runtime selection or scheduling
changes accompany the increase.

## Resource and safety boundaries

Snapshot capture and validation each use a thirty-second cooperative deadline,
including checks after serialization and at completion of validation. The budget
does not hard-preempt blocking native work or bound the whole backup command.
Encryption remains whole-buffer and in memory, without plaintext staging or
custom cryptographic primitives. The canonical-encoding check uses aligned
64 KiB chunks to avoid an additional full decoded-token allocation; Fernet still
authenticates and decrypts the entire token.

Deletion inventory hashes the database in bounded chunks, with the same private
single-link, no-follow and sampled identity checks. The database inventory bound
matches supported snapshot capacity; configuration/restore receipt limits remain
16 MiB each. Preview tokens, explicit confirmation and external receipts retain
their existing semantics. No automatic deletion is authorized by storage pressure.

The 256 MiB value is supported backup and automated-workflow capacity, not a
universal SQLite write limit. Older manual job capture and standalone material
build paths can exceed it. Doctor reports exceeded capacity; those paths do not
gain a retention or universal quota policy here. File headroom does not prove
that the next run fits or that a backup validates. Same-UID TOCTOU limitations,
sidecar/WAL refusal and untrusted-input rules remain unchanged.

## Verification and limits of the evidence

The required capacity gate is `python -W error scripts/check_storage_capacity.py`.
It generates its own external fictional runtime through application services,
prepares real PDFs above the former 16 MiB boundary, exercises daily replay and
encrypted restoration, then measures a profile near the new ceiling. Backup and
restore probes run sequentially in separate processes and report elapsed time
and peak resident memory. It makes no live network or model calls.

The September 20 local full probe passed in 59.300s: 267,415,552 database bytes,
356,554,263 encrypted bytes and three real PDFs. At that size, a new daily draft
took 2.618s, backup 2.411s, restore preview 2.985s, confirmed restore 3.138s and
exact restore replay 3.058s. Peak RSS was 2,471,247,872 bytes for backup and
2,437,218,304 bytes for confirmed restore (about 2.3 GiB each). These are
single-host observations, not memory or latency guarantees. The generated
evidence is `/private/tmp/gapply-storage-capacity-full-20260920/capacity-summary.json`;
the script recreates its fixture independently of that path.

The base suite retains small injected capacity boundaries for rollback and
advisory failure tests. Real larger-profile tests cover accepting work above the
old limit, exact restoration, unchanged schema/history, and bounded deletion.
Archive tampering, noncanonical encodings across chunk boundaries, time-budget
failure, partial-work reporting and legacy schemas must continue to fail closed.

Byte capacity and material-history performance are separate measurements. A
near-full profile with synthetic job text does not establish the cost of
validating thousands of historical PDFs, a universal months-of-use guarantee,
or the full hosted OS matrix. Streaming archives, document deduplication and
long-history performance remain separately measurable improvements.
