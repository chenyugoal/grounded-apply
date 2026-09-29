from __future__ import annotations

import builtins
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import grounded_apply.cli as cli
from grounded_apply.config import RuntimePaths


PRIVATE = "SYNTHETIC_PRIVATE_PROBE /private/fictional-user/dependency"


class MaterialsPreflightCliTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.home = self.workspace / "unused-private-runtime"
        environment = patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(arguments)
        return code, output.getvalue(), errors.getvalue()

    def assert_events(self, errors: str, outcome: str) -> None:
        events = [json.loads(line) for line in errors.splitlines()]
        self.assertEqual([event["outcome"] for event in events], ["started", outcome])
        for event in events:
            self.assertEqual(set(event), {"schema_version", "event", "run_id", "at", "command", "outcome", "recovery"})
            self.assertEqual(event["command"], "doctor")
            self.assertIsNone(event["recovery"])
        self.assertNotIn(PRIVATE, errors)
        self.assertNotIn(str(self.home), errors)
        self.assertNotIn("pypdf", errors)
        self.assertNotIn("pdflatex", errors)

    def test_four_presence_combinations_report_intake_and_output_separately(self) -> None:
        for pypdf_present, pdflatex_present in ((True, True), (True, False), (False, True), (False, False)):
            with self.subTest(pypdf=pypdf_present, pdflatex=pdflatex_present):
                with patch.object(importlib.util, "find_spec", return_value=SimpleNamespace(origin=PRIVATE) if pypdf_present else None) as find, \
                     patch.object(shutil, "which", return_value=PRIVATE if pdflatex_present else None) as which:
                    code, output, errors = self.invoke("--log-events", "doctor", "--materials", "--json")
                find.assert_called_once_with("pypdf")
                which.assert_called_once_with("pdflatex")
                ok = pypdf_present and pdflatex_present
                self.assertEqual(code, 0 if ok else 2)
                result = json.loads(output)
                self.assertEqual(result["command"], "doctor")
                self.assertIs(result["ok"], ok)
                self.assertEqual(result["data"], {
                    "schema_version": 1, "check_method": "dependency_presence@1",
                    "dependencies": {"pypdf": "present" if pypdf_present else "missing",
                                     "pdflatex": "present" if pdflatex_present else "missing"},
                    "pdf_intake": {"prerequisites": ["pypdf"], "status": "present" if pypdf_present else "missing"},
                    "pdf_materials": {"prerequisites": ["pypdf", "pdflatex"], "status": "present" if ok else "missing"},
                    "functional_tests_run": False, "profile_read": False, "read_only": True,
                })
                if ok:
                    self.assertIsNone(result["error"])
                else:
                    self.assertEqual(result["error"]["type"], "MaterialsPrerequisitesUnavailable")
                    self.assertIn("docs/QUICKSTART.md#start", result["error"]["message"])
                self.assertNotIn(PRIVATE, output)
                self.assertNotIn(str(self.home), output)
                self.assert_events(errors, "succeeded" if ok else "failed")
                self.assertFalse(self.home.exists())

    def test_probe_exceptions_are_unknown_and_do_not_prevent_other_probe(self) -> None:
        for pypdf_state, pdflatex_state, output_state in (
            ("unknown", "present", "unknown"), ("present", "unknown", "unknown"),
            ("unknown", "unknown", "unknown"), ("unknown", "missing", "missing"),
            ("missing", "unknown", "missing"),
        ):
            with self.subTest(pypdf=pypdf_state, pdflatex=pdflatex_state):
                with patch.object(importlib.util, "find_spec",
                                  side_effect=ValueError(PRIVATE) if pypdf_state == "unknown" else None,
                                  return_value=object() if pypdf_state == "present" else None) as find, \
                     patch.object(shutil, "which",
                                  side_effect=OSError(PRIVATE) if pdflatex_state == "unknown" else None,
                                  return_value=PRIVATE if pdflatex_state == "present" else None) as which:
                    code, output, errors = self.invoke("--log-events", "doctor", "--materials", "--json")
                self.assertEqual(code, 2)
                find.assert_called_once_with("pypdf")
                which.assert_called_once_with("pdflatex")
                result = json.loads(output)
                self.assertEqual(result["data"]["dependencies"], {"pypdf": pypdf_state, "pdflatex": pdflatex_state})
                self.assertEqual(result["data"]["pdf_intake"]["status"], pypdf_state)
                self.assertEqual(result["data"]["pdf_materials"]["status"], output_state)
                self.assertEqual(result["error"]["type"], "MaterialsPrerequisitesUnavailable")
                self.assertNotIn(PRIVATE, output + errors)
                self.assert_events(errors, "failed")

    def test_presence_check_never_imports_provider_runs_tools_or_resolves_profile(self) -> None:
        original_import = builtins.__import__
        attempted_imports: list[str] = []

        def guard_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "pypdf" or name.startswith("pypdf."):
                attempted_imports.append(name)
                raise AssertionError("Provider import is forbidden")
            return original_import(name, *args, **kwargs)

        sentinel = self.workspace / "fictional-existing-file"
        sentinel.write_bytes(b"Fictional source must remain unchanged.")
        before = sentinel.read_bytes(), sentinel.stat().st_mtime_ns
        targets = ("grounded_apply.cli.resolve_runtime_paths", "grounded_apply.cli._read_schema_version",
                   "grounded_apply.cli._repository_for", "grounded_apply.cli._read_utf8_input",
                   "sqlite3.connect", "subprocess.Popen", "os.system", "socket.socket")
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target, side_effect=AssertionError(PRIVATE))) for target in targets]
            stack.enter_context(patch.object(builtins, "__import__", side_effect=guard_import))
            stack.enter_context(patch.object(importlib.util, "find_spec", return_value=SimpleNamespace(origin=PRIVATE)))
            stack.enter_context(patch.object(shutil, "which", return_value=PRIVATE))
            code, output, errors = self.invoke("doctor", "--materials", "--json")
            for guard in guards:
                guard.assert_not_called()
        self.assertEqual(code, 0, output + errors)
        self.assertEqual(attempted_imports, [])
        self.assertFalse(self.home.exists())
        self.assertEqual((sentinel.read_bytes(), sentinel.stat().st_mtime_ns), before)
        self.assertEqual(list(self.workspace.iterdir()), [sentinel])

    def test_human_output_shows_each_state_and_limits_without_health_claim(self) -> None:
        for pypdf, pdflatex in ((object(), PRIVATE), (None, PRIVATE), (object(), None)):
            with self.subTest(pypdf=pypdf is not None, pdflatex=pdflatex is not None):
                with patch.object(importlib.util, "find_spec", return_value=pypdf), \
                     patch.object(shutil, "which", return_value=pdflatex):
                    code, output, errors = self.invoke("doctor", "--materials")
                self.assertEqual(code, 0 if pypdf is not None and pdflatex is not None else 2)
                self.assertIn("pypdf: " + ("present" if pypdf is not None else "missing"), output)
                self.assertIn("pdflatex: " + ("present" if pdflatex is not None else "missing"), output)
                self.assertIn("PDF intake prerequisites:", output)
                self.assertIn("PDF material output prerequisites:", output)
                for limitation in ("parsing", "rendering", "fonts", "TeX packages", "binary compatibility"):
                    self.assertIn(limitation, errors)
                self.assertIn("base CLI health is not assessed", errors)
                self.assertNotIn("healthy", output)
                self.assertNotIn("PDF-ready", output)
                self.assertNotIn(PRIVATE, output + errors)

    def test_fixed_output_failure_and_retry_never_disclose_probe_paths(self) -> None:
        original_emit = cli._emit

        def fail_report(*args: object, **kwargs: object) -> None:
            if kwargs.get("data") is not None:
                raise RuntimeError(PRIVATE)
            original_emit(*args, **kwargs)

        with patch.object(importlib.util, "find_spec", return_value=SimpleNamespace(origin=PRIVATE)), \
             patch.object(shutil, "which", return_value=PRIVATE):
            with patch.object(cli, "_emit", side_effect=fail_report):
                code, output, errors = self.invoke("--log-events", "doctor", "--materials", "--json")
            self.assertEqual(code, 2)
            result = json.loads(output)
            self.assertIsNone(result["data"])
            self.assertEqual(result["error"], {"type": "CliInputError", "message":
                "Materials prerequisite output failed; no profile state was changed"})
            self.assertNotIn(PRIVATE, output + errors)
            self.assert_events(errors, "failed")
            self.assertEqual(self.invoke("doctor", "--materials", "--json")[0], 0)
        self.assertFalse(self.home.exists())

    def test_permanent_serialization_failure_remains_fixed_in_human_error(self) -> None:
        with patch.object(importlib.util, "find_spec", return_value=None), \
             patch.object(shutil, "which", return_value=None), \
             patch.object(cli, "dumps", side_effect=ValueError(PRIVATE)):
            code, output, errors = self.invoke("doctor", "--materials", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertEqual(errors, "Error: Materials prerequisite output failed; no profile state was changed\n")
        self.assertNotIn(PRIVATE, errors)

    def test_probe_and_output_interrupts_remain_normal_interruptions(self) -> None:
        for target in ("importlib.util.find_spec", "shutil.which", "grounded_apply.cli._emit"):
            with self.subTest(target=target), patch.object(importlib.util, "find_spec", return_value=None), \
                 patch.object(shutil, "which", return_value=None), patch(target, side_effect=KeyboardInterrupt(PRIVATE)):
                code, output, errors = self.invoke("--log-events", "doctor", "--materials", "--json")
            self.assertEqual(code, 130)
            self.assertEqual(output, "")
            self.assert_events(errors, "interrupted")
            self.assertFalse(self.home.exists())
        for target in ("importlib.util.find_spec", "shutil.which"):
            with self.subTest(target=target), patch(target, side_effect=SystemExit(7)):
                with self.assertRaises(SystemExit) as caught:
                    self.invoke("doctor", "--materials", "--json")
            self.assertEqual(caught.exception.code, 7)

    def test_default_doctor_preserves_preincrement_human_and_json_bytes(self) -> None:
        # Frozen from the previous doctor body, with version/path inputs fixed.
        root = Path("/private/tmp/fictional-doctor-compatibility")
        paths = RuntimePaths(root / "config", root / "data", root / "cache", root / "state", root)
        fixtures = (
            ((), "6dc51b9ff9e2de41ec746d5ec7a1a8656818a123858e33758010da6bf2a717bc",
             "Warning: Profile storage is not initialized; run `gapply profile init`.\n"),
            (("--json",), "bc4f2147f0c111f5b1186f56e55509766353aa1c9278590f1cf2bc0556f75a7c", ""),
        )
        with patch.dict(os.environ, {"GROUNDED_APPLY_HOME": str(root)}), \
             patch.object(cli, "resolve_runtime_paths", return_value=paths), \
             patch.object(cli, "require_runtime_outside_repository"), \
             patch.object(cli, "_read_schema_version", return_value=None), \
             patch("platform.python_version", return_value="3.12.99"), \
             patch.object(importlib.util, "find_spec", side_effect=AssertionError("Unexpected dependency probe")) as find, \
             patch.object(shutil, "which", side_effect=AssertionError("Unexpected executable probe")) as which:
            for arguments, digest, expected_errors in fixtures:
                code, output, errors = self.invoke("doctor", *arguments)
                self.assertEqual(code, 0)
                self.assertEqual(hashlib.sha256(output.replace(cli.__version__, "<version>").encode()).hexdigest(), digest)
                self.assertEqual(errors, expected_errors)
            find.assert_not_called()
            which.assert_not_called()

    def test_help_and_invalid_flags_never_start_presence_or_profile_checks(self) -> None:
        with patch.object(importlib.util, "find_spec") as find, patch.object(shutil, "which") as which, \
             patch.object(cli, "resolve_runtime_paths") as paths:
            with self.assertRaises(SystemExit) as help_exit:
                self.invoke("doctor", "--help")
            self.assertEqual(help_exit.exception.code, 0)
            code, output, errors = self.invoke("--log-events", "doctor", "--materials", PRIVATE, "--json")
            self.assertEqual(code, 2)
            self.assertIsNone(json.loads(output)["data"])
            self.assertNotIn(PRIVATE, output + errors)
            self.assert_events(errors, "failed")
            find.assert_not_called()
            which.assert_not_called()
            paths.assert_not_called()


if __name__ == "__main__":
    unittest.main()
