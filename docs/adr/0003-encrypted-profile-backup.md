# ADR 0003: Bounded encrypted profile snapshots

- **Status:** Accepted for implementation; release evidence belongs in the handoff
- **Date:** 2026-09-04
- **Scope:** POSIX, current-schema profile databases, synthetic data

## Decision

The first lifecycle slice is an encrypted **profile database** backup and restore,
not a filesystem vault or support export. Capture a consistent SQLite backup into
memory through the guarded read-only adapter. Refuse recovery journals, WAL,
future/old schemas, non-digest artifact references, and databases larger than
16 MiB. Validate the exact registered SQL schema, migration ledger, integrity,
and foreign keys before encryption and after decryption. Preserve all claim,
review, provenance, and workflow bytes; restoration never approves a claim.

Use the optional `cryptography` library's Fernet authenticated-encryption recipe
with Argon2id (16 random salt bytes, 32-byte key, 3 iterations, 4 lanes, 64 MiB).
These are the memory-constrained parameters referenced by the
[official recipe](https://cryptography.io/en/stable/fernet/#using-passwords-with-fernet).
The application does not implement encryption primitives. The provider is loaded
only by lifecycle commands and its absence fails closed. The base CLI remains
dependency-free. No plaintext fallback is supported.

Format 1 uses a fixed magic/version prefix, salt, and a canonical Fernet token.
The authenticated plaintext repeats the prefix and salt and contains a fixed
profile-scope marker followed by one bounded SQLite image. Unknown formats and
noncanonical encodings fail closed. There are no filenames to extract, archive
paths, user-selected KDF parameters, or compression. Length and Fernet creation
time are visible; candidate content is encrypted. SHA-256 values identify archive
bytes and snapshots, but are not authentication by themselves.

Passphrases enter through a no-echo terminal prompt (twice for creation), or an
explicit bounded stdin channel. They have no argument, environment, file, log,
configuration, or database field. Python cannot guarantee zeroization or prevent
swap/core-dump capture. The user must retain the passphrase separately; there is
no recovery key service.

Output requires an existing private directory outside Git and a new exclusive,
no-follow, mode-0600 file. Existing ciphertext is never overwritten: the same
path/passphrase/snapshot can return the original archive result after full
authentication, while changed input fails. A partial write is never accepted as
a valid archive. Backup creates no plaintext temporary file or source mutation.

Restore defaults to a write-free inspection. Confirmation requires the exact
archive SHA-256 from inspection and an explicit new absolute target home. The
target is a new mode-0700 root under an existing private directory outside Git;
it never replaces a current profile. Write the validated database exclusively,
then a small content-free completion receipt last. Exact retries validate the
receipt, unchanged restored database, and runtime layout; partial or changed
targets fail without repair. Interrupted incomplete targets remain private and
must be inspected separately; use a new target to retry. No recursive deletion
is performed by this workflow.

## Limits and follow-up

This is a point-in-time database backup, excluding source documents, generated
files, browser state, config, caches, logs, and other filesystem artifacts.
Non-digest artifact rows are refused so referenced file content cannot silently
go missing. Stronger archive authentication does not make candidate assertions
true or authenticate a database against its writer. Existing truth checks still
govern restored data. Filesystem checks retain ADR 0002's sampled same-UID TOCTOU
limit. OS disk encryption remains necessary for plaintext runtime data.

Full-vault backup, support export, deletion/retention, schema upgrades on restore,
large streaming archives, passphrase rotation, and power-loss durability remain
separate work. A profile round trip is not evidence of personal-use MVP readiness.
