#!/usr/bin/env python3
"""Required optional-provider gate: never report skipped crypto tests as passing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


def check() -> int:
    sys.dont_write_bytecode = True
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / "src"), str(root)]
    from grounded_apply.repositories.backup_crypto import FernetBackupCipher

    FernetBackupCipher()  # Fail before tests if the optional provider is missing.
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_backup")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(check())
