"""SQLite schema discovery, validation, and migration application.

This module is deliberately private.  ``SQLiteRepository.initialize()`` is the
public entry point so callers cannot accidentally skip connection safety
settings or schema compatibility checks.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType


LATEST_SCHEMA_VERSION = 7
REGISTERED_SCHEMA_VERSION = 7
SCHEMA_OBJECTS_QUERY = "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"

_MIGRATION_NAME = re.compile(
    r"^(?P<version>[0-9]{3})_(?P<description>[a-z][a-z0-9_]*)\.sql$"
)


class SchemaError(RuntimeError):
    """The database schema cannot be used safely by this repository version."""


class FutureSchemaError(SchemaError):
    """The database was created by a newer Grounded Apply version."""


class MigrationError(SchemaError):
    """A migration is missing, invalid, or could not be applied atomically."""


class ConversionRequiredError(MigrationError):
    """A registered storage change cannot run through ordinary initialization."""


class MigrationExecution(StrEnum):
    IN_PLACE = "in_place"
    CONVERSION_ONLY = "conversion_only"


# Availability does not authorize execution against an existing profile. A new
# SQL file needs an explicit classification, independently of the active version.
_MIGRATION_EXECUTION_POLICY: Mapping[int, MigrationExecution] = MappingProxyType({
    1: MigrationExecution.IN_PLACE,
    2: MigrationExecution.IN_PLACE,
    3: MigrationExecution.IN_PLACE,
    4: MigrationExecution.IN_PLACE,
    5: MigrationExecution.IN_PLACE,
    6: MigrationExecution.IN_PLACE,
    7: MigrationExecution.IN_PLACE,
})


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable migration loaded from disk."""

    version: int
    name: str
    sql: str
    checksum_sha256: str
    execution: MigrationExecution


def utc_now() -> str:
    """Return a lexically sortable UTC timestamp."""

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def default_migrations_directory() -> Path:
    """Find migrations in an installed package or a source checkout.

    A wheel can place the root ``migrations`` directory at
    ``grounded_apply/migrations``.  The second candidate is the normal source
    tree layout used by this repository.
    """

    module_path = Path(__file__).resolve()
    candidates = (
        module_path.parents[1] / "migrations",
        module_path.parents[3] / "migrations",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    checked = ", ".join(str(candidate) for candidate in candidates)
    raise MigrationError(
        "Could not locate Grounded Apply migrations. "
        f"Checked: {checked}. Pass migrations_dir explicitly."
    )


def load_migrations(directory: Path) -> tuple[Migration, ...]:
    """Load the complete registered sequence and its closed execution policy."""

    policy = _registered_execution_policy()

    if not directory.is_dir():
        raise MigrationError(f"Migration directory does not exist: {directory}")

    migrations_by_version: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            raise MigrationError(
                f"Invalid migration filename {path.name!r}; expected NNN_description.sql"
            )
        version = int(match.group("version"))
        if version < 1:
            raise MigrationError(f"Migration versions start at 1: {path.name}")
        if version not in policy:
            raise MigrationError("Migration file has no registered execution policy")
        if version in migrations_by_version:
            other = migrations_by_version[version].name
            raise MigrationError(
                f"Duplicate migration version {version}: {other!r} and {path.name!r}"
            )
        sql = path.read_text(encoding="utf-8")
        if not sql.strip():
            raise MigrationError(f"Migration is empty: {path}")
        migrations_by_version[version] = Migration(
            version=version,
            name=path.name,
            sql=sql,
            checksum_sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            execution=policy[version],
        )

    expected_versions = list(range(1, REGISTERED_SCHEMA_VERSION + 1))
    actual_versions = sorted(migrations_by_version)
    if actual_versions != expected_versions:
        raise MigrationError(
            "Migration files do not match this repository version: "
            f"expected {expected_versions}, found {actual_versions}"
        )
    return tuple(migrations_by_version[version] for version in expected_versions)


def _registered_execution_policy() -> dict[int, MigrationExecution]:
    if (type(LATEST_SCHEMA_VERSION) is not int
            or type(REGISTERED_SCHEMA_VERSION) is not int
            or not 1 <= LATEST_SCHEMA_VERSION <= REGISTERED_SCHEMA_VERSION <= 999
            or type(_MIGRATION_EXECUTION_POLICY) is not MappingProxyType):
        raise MigrationError("Invalid migration execution policy")
    policy = dict(_MIGRATION_EXECUTION_POLICY)
    if (any(type(version) is not int or type(mode) is not MigrationExecution
            for version, mode in policy.items())
            or set(policy) != set(range(1, REGISTERED_SCHEMA_VERSION + 1))):
        raise MigrationError("Incomplete or invalid migration execution policy")
    return policy


def reference_schema_objects(
    migrations_directory: Path, *, target_version: int,
) -> tuple[tuple[str, str, str, str | None], ...]:
    """Build trusted SQL structure in a fresh, privately owned in-memory DB.

    This is a schema inventory, not a conversion path: no caller database or
    snapshot is accepted and no writable connection escapes. Registered SQL may
    describe a future conversion-only version without activating it for profiles
    or adding that version to the independent backup-restore allowlist.
    """
    migrations = load_migrations(migrations_directory)
    if type(target_version) is not int or not 1 <= target_version <= REGISTERED_SCHEMA_VERSION:
        raise MigrationError("Unsupported reference schema version")
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as reference:
        reference.execute("PRAGMA temp_store = MEMORY")
        _apply_schema_migrations(
            reference, migrations, current=0, target=target_version,
            supported_version=REGISTERED_SCHEMA_VERSION, max_database_bytes=None,
        )
        return tuple(reference.execute(SCHEMA_OBJECTS_QUERY).fetchall())


def read_schema_version(connection: sqlite3.Connection) -> int:
    """Read SQLite's explicit application schema version."""

    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise SchemaError("SQLite did not return PRAGMA user_version")
    return int(row[0])


def initialize_schema(
    connection: sqlite3.Connection,
    migrations_directory: Path,
    *,
    target_version: int | None = None,
    max_database_bytes: int | None = None,
) -> int:
    """Validate and migrate a database, returning its current version.

    The optional target selects an ordinary migration prefix; it never permits
    downgrades or conversion-only execution. Normal repository opens use the
    current version. Snapshot validation builds its empty trusted reference
    through ``reference_schema_objects`` instead.

    An optional allocation bound is checked inside each migration transaction;
    an oversized migration rolls back without making its prior version
    unrestorable. Earlier successful migration versions remain committed.

    Each schema migration, migration-ledger insert, and ``user_version`` update
    commits as one transaction.  A future or internally inconsistent schema is
    rejected before any schema write occurs.
    """

    if connection.in_transaction:
        raise MigrationError("Cannot initialize the schema inside an active transaction")

    migrations = load_migrations(migrations_directory)
    target = LATEST_SCHEMA_VERSION if target_version is None else target_version
    if type(target) is not int or not 1 <= target <= LATEST_SCHEMA_VERSION:
        raise MigrationError("Unsupported target schema version")
    if max_database_bytes is not None and (type(max_database_bytes) is not int or max_database_bytes <= 0):
        raise MigrationError("Migration storage limit must be a positive integer")
    current = _validate_schema_state(connection, migrations)
    if current > target:
        raise MigrationError("Schema migration cannot downgrade an existing database")
    if any(migration.execution is MigrationExecution.CONVERSION_ONLY
           for migration in migrations[current:target]):
        raise ConversionRequiredError("Schema change requires explicit conversion into a new home")

    return _apply_schema_migrations(
        connection, migrations, current=current, target=target,
        supported_version=LATEST_SCHEMA_VERSION, max_database_bytes=max_database_bytes,
    )


def _apply_schema_migrations(
    connection: sqlite3.Connection,
    migrations: tuple[Migration, ...],
    *,
    current: int,
    target: int,
    supported_version: int,
    max_database_bytes: int | None,
) -> int:
    """Execute a preflighted ordinary plan or privately owned empty reference."""
    for migration in migrations[current:target]:
        try:
            connection.execute("BEGIN IMMEDIATE")
            locked_version = _validate_schema_state(
                connection, migrations, supported_version=supported_version,
            )
            if locked_version > target:
                raise MigrationError("Database advanced beyond the requested schema target")
            if locked_version >= migration.version:
                connection.execute("COMMIT")
                continue
            if locked_version != migration.version - 1:
                raise MigrationError(
                    f"Cannot apply migration {migration.name!r} after schema "
                    f"version {locked_version}"
                )
            for statement in _sql_statements(migration.sql):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migrations (
                    version,
                    name,
                    checksum_sha256,
                    applied_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum_sha256,
                    utc_now(),
                ),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
            if max_database_bytes is not None:
                allocated = (connection.execute("PRAGMA page_count").fetchone()[0]
                             * connection.execute("PRAGMA page_size").fetchone()[0])
                if allocated > max_database_bytes:
                    raise MigrationError("Migration exceeds the supported profile storage limit; prior schema retained")
            connection.execute("COMMIT")
        except (OSError, sqlite3.Error, SchemaError) as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise MigrationError(
                f"Could not apply migration {migration.name!r}: {exc}"
            ) from exc

    final_version = _validate_schema_state(
        connection, migrations, supported_version=supported_version,
    )
    if final_version != target:
        raise MigrationError("Database changed beyond the requested schema target")
    return final_version


def validate_schema(
    connection: sqlite3.Connection,
    migrations_directory: Path,
) -> int:
    """Validate schema version and migration history without changing either."""

    migrations = load_migrations(migrations_directory)
    return _validate_schema_state(connection, migrations)


def _sql_statements(script: str) -> tuple[str, ...]:
    """Split a migration with SQLite's own completeness parser."""

    statements: list[str] = []
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise MigrationError("Migration ends with an incomplete SQL statement")
    return tuple(statements)


def _validate_schema_state(
    connection: sqlite3.Connection,
    migrations: tuple[Migration, ...],
    *, supported_version: int | None = None,
) -> int:
    """Read version, tables, and ledger from one consistent snapshot.

    Initial/final validation and existing-only opens run outside the migration
    write lock. Their reads must not straddle another initializer's commit.
    Release our read transaction before acquiring a migration write lock, while
    leaving an existing caller-owned transaction untouched on every exit path.
    """

    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN")
    try:
        return _validate_schema_snapshot(
            connection, migrations,
            supported_version=LATEST_SCHEMA_VERSION if supported_version is None else supported_version,
        )
    finally:
        if owns_transaction and connection.in_transaction:
            connection.execute("ROLLBACK")


def _validate_schema_snapshot(
    connection: sqlite3.Connection,
    migrations: tuple[Migration, ...],
    *, supported_version: int,
) -> int:
    current = read_schema_version(connection)
    if current > supported_version:
        raise FutureSchemaError(
            "Database schema version "
            f"{current} is newer than supported version {supported_version}"
        )

    has_ledger = _table_exists(connection, "schema_migrations")
    applied = _read_applied_migrations(connection) if has_ledger else []
    future_applied = [version for version, _, _ in applied if version > supported_version]
    if future_applied:
        raise FutureSchemaError(
            "Database migration ledger contains future version "
            f"{max(future_applied)}; this code supports {supported_version}"
        )

    if current == 0:
        user_tables = _user_table_names(connection)
        if has_ledger or user_tables:
            found = ", ".join(user_tables) if user_tables else "schema_migrations"
            raise SchemaError(
                "Database has user tables but no supported schema version; "
                f"refusing to adopt an unversioned schema ({found})"
            )
        return 0

    if not has_ledger:
        raise SchemaError(
            f"Database reports schema version {current} but has no schema_migrations table"
        )

    applied_versions = [version for version, _, _ in applied]
    expected_applied = list(range(1, current + 1))
    if applied_versions != expected_applied:
        raise SchemaError(
            "Migration ledger and PRAGMA user_version disagree: "
            f"version is {current}, applied migrations are {applied_versions}"
        )

    known_by_version = {migration.version: migration for migration in migrations}
    for version, name, checksum in applied:
        known = known_by_version.get(version)
        if known is None:
            raise SchemaError(f"No migration file is available for applied version {version}")
        if name != known.name:
            raise SchemaError(
                f"Migration {version} name mismatch: database has {name!r}, code has {known.name!r}"
            )
        if checksum != known.checksum_sha256:
            raise SchemaError(
                f"Migration {version} checksum mismatch for {known.name!r}; "
                "applied migrations must never be edited"
            )
    return current


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _user_table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _read_applied_migrations(
    connection: sqlite3.Connection,
) -> list[tuple[int, str, str]]:
    try:
        rows = connection.execute(
            """
            SELECT version, name, checksum_sha256
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise SchemaError(f"Cannot read schema_migrations: {exc}") from exc
    return [(int(row[0]), str(row[1]), str(row[2])) for row in rows]
