from __future__ import annotations

import datetime as dt
import enum
import json
import unittest
from dataclasses import dataclass
from pathlib import Path

from grounded_apply.json_support import dumps, to_jsonable


class ExampleStatus(enum.Enum):
    READY = "ready"


@dataclass(frozen=True)
class Example:
    status: ExampleStatus
    created_at: dt.datetime
    path: Path


class JsonSupportTests(unittest.TestCase):
    def test_nested_domain_values_become_json_safe(self) -> None:
        value = Example(
            status=ExampleStatus.READY,
            created_at=dt.datetime(2026, 8, 11, 12, 30, tzinfo=dt.UTC),
            path=Path("/private/example"),
        )

        payload = to_jsonable(value)

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["created_at"], "2026-08-11T12:30:00+00:00")
        self.assertEqual(json.loads(dumps(value))["path"], "/private/example")


if __name__ == "__main__":
    unittest.main()
