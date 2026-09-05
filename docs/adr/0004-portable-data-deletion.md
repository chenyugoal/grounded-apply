# ADR 0004: Explicit portable-home deletion

- **Status:** Accepted for implementation; verification belongs in the handoff
- **Date:** 2026-09-04

The first deletion command accepts an explicit absolute portable home and an
explicit receipt destination outside it. Preview is read-only. Its token binds
the canonical target, receipt destination, complete allowlisted inventory,
file digests, and sampled file/directory identity. Confirmation requires that
token and a fresh matching inventory. Environment defaults never select a
destructive target. The service owns confirmation policy; the adapter repeats
guards for direct callers.

Only the known initialized runtime layout, current profile database, config,
and optional restore receipt are accepted. Unknown entries, symlinks, extra file
hard links, broad permissions, unsafe owners, Git paths, SQLite sidecars, WAL,
and changed state fail closed. The initial scope requires empty artifact,
generated, snapshot, backup, browser, cache, and log directories. File-bearing
extensions require a later explicit inventory policy. No recursive remover is
used. Known files are unlinked and known empty directories removed through
checked directory descriptors, with repeated inventory/identity checks.

A private exclusive receipt is created outside the target before removal. It
contains only a version, opaque operation ID, target/preview digests, counts,
UTC time, and phase. Completion is appended and fsynced only after removal.
Successful exact retries require a valid complete receipt and an absent target.
Partial receipts or partial targets are refused; no retry silently adopts,
repairs, or continues them. Ambiguous failures instruct the user to inspect the
receipt. The receipt itself, separately saved backups, exported copies, and source
files outside the target are retained. These exclusions appear in preview.

Retention is explicit manual retention until confirmed deletion; there is no
background expiry or destructive scheduler. Receipts provide consistency and
audit evidence, not authentication against the user who owns the filesystem.
Descriptor-relative operations narrow path races but do not defeat a malicious
same-UID process. This is logical deletion, not secure erasure, snapshot removal,
or proof of power-loss durability. No real personal data is used in development.
