"""Required real PDF gate; optional-provider skips cannot count as verification."""
from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if shutil.which("pdflatex") is None:
    raise SystemExit("Required material gate needs pdflatex")
try:
    import pypdf
except ImportError:
    raise SystemExit("Required material gate needs the materials extra") from None

suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_materials")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)
