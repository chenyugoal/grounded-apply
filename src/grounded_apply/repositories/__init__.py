"""Public persistence API.

The first runnable slice uses only Python's standard-library ``sqlite3``.  It
returns mapping records rather than domain objects so domain reconstruction and
truth-policy decisions remain explicit at the application boundary.
"""

from ._schema import (
    LATEST_SCHEMA_VERSION,
    FutureSchemaError,
    MigrationError,
    SchemaError,
)
from .sqlite import (
    Record,
    RecordNotFoundError,
    RepositoryClosedError,
    RepositoryError,
    RepositoryNotInitializedError,
    SQLiteRepository,
    inspect_schema,
)

__all__ = [
    "LATEST_SCHEMA_VERSION",
    "FutureSchemaError",
    "MigrationError",
    "Record",
    "RecordNotFoundError",
    "RepositoryClosedError",
    "RepositoryError",
    "RepositoryNotInitializedError",
    "SQLiteRepository",
    "SchemaError",
    "inspect_schema",
]
