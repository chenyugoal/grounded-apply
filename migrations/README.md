# SQLite migrations

`SQLiteRepository.initialize()` applies these files in numeric order. Migration
filenames must use `NNN_description.sql`, beginning with `001` and without
gaps. The repository records each filename and SHA-256 checksum in
`schema_migrations`, and records the current application schema in SQLite's
`PRAGMA user_version`.

Applied migrations are immutable. Add the next numbered file and increment
`LATEST_SCHEMA_VERSION` in
`src/grounded_apply/repositories/_schema.py`; never edit a migration that may
already exist in a runtime profile. The initializer refuses future versions,
missing/gapped histories, checksum drift, and unversioned databases containing
user tables.

Migration files must not contain transaction control or set `user_version`.
The initializer wraps each file, its ledger entry, and its version update in one
transaction so an unsuccessful migration rolls back completely. Earlier
successful migrations in the same upgrade remain committed.
