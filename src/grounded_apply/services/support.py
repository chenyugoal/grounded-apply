"""Fixed-schema support data, excluding all user-provided values."""

from __future__ import annotations

from typing import Any

from grounded_apply import __version__
from grounded_apply.repositories import SQLiteRepository


class SupportService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository

    def report(self) -> dict[str, Any]:
        with self._repository.read_transaction():
            return {"schema_version": 1, "application_version": __version__,
                "database_schema_version": self._repository.schema_version,
                "counts": self._repository.support_counts(),
                "contains_personal_values": False, "contains_identifiers": False,
                "contains_paths": False, "contains_logs": False}
