#!/usr/bin/env python3
"""Verify complete, reviewed onboarding through the actual source or installed CLI.

Uses only newly generated fictional TeX/PDF inputs in an empty external workspace.
The materials extra is required for PDF intake; --with-materials additionally
checks a generated Research-section PDF and requires the normal TeX installation.
The optional source_path points to application source code, never a user's CV.
Each invocation starts a fresh process, including review pages and decisions.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any


FACTS = (
    "Avery Quill",
    "avery.quill@example.com",
    "Contributed Python evaluation tools for Fictional Research Lab; did not lead the study.",
    "Built reproducible synthetic evaluation fixtures.",
    "PhD in Computer Science, Example University, expected May 2027.",
    "Quill, A. Fictional Widget Study; submitted September 2026, not accepted.",
    "Python and synthetic test design.",
)
LATEX = r"""\documentclass{article}
\begin{document}
Name: \textbf{Avery Quill}\\
Email: avery.quill@example.com
\section*{Research Experience}
\begin{itemize}
\item Contributed Python evaluation tools for Fictional Research Lab; did not lead the study.
\item Built reproducible synthetic evaluation fixtures.
\end{itemize}
\section*{Education}
PhD in Computer Science, Example University, expected May 2027.
\section*{Publications}
Quill, A. Fictional Widget Study; submitted September 2026, not accepted.
\section*{Skills}
Python and synthetic test design.
\end{document}
"""
PLAIN = "\n".join(("Name: " + FACTS[0], "Email: " + FACTS[1],
    "Research Experience", FACTS[2], FACTS[3], "Education", FACTS[4],
    "Publications", FACTS[5], "Skills", FACTS[6])) + "\n"
PARTIAL_LATEX = r"""\begin{document}
Name: Avery Quill\\
Email: avery.quill@example.com\\
\section*{Research Experience}
\begin{itemize}
\item Contributed \fictionalUnknown{Unsupported contribution marker} to a study.
\item Built reproducible synthetic evaluation fixtures.
\end{itemize}
\end{document}
"""
UNCLASSIFIED_LATEX = r"""\begin{document}
Contributed fictional benchmark analysis; did not lead the study.
\end{document}
"""
EVENT_FIELDS = {"schema_version", "event", "run_id", "at", "command", "outcome", "recovery"}
INVENTORY_TOPICS = ("contact", "education", "experience", "research", "projects", "skills",
                    "publications", "achievements", "other")
INVENTORY_ITEM_FIELDS = ("id", "claim_type", "canonical_text", "status", "approval_status",
                         "source_type", "scope", "sensitivity")
FIXTURE_TOPICS = {"candidate_name": "contact", "contact_email": "contact", "education": "education",
                  "employment_description": "experience", "research_description": "research",
                  "publication": "publications", "skill_use": "skills"}


def synthetic_pdf(text: str = PLAIN) -> bytes:
    """Build a small valid, selectable-text PDF using only standard-library code."""
    lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
             for line in text.splitlines()]
    content = ("BT /F1 10 Tf 40 750 Td 16 TL\n" +
               "\n".join("(" + line + ") Tj T*" for line in lines) + "\nET").encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
               b"/Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>"]
    raw = b"%PDF-1.4\n"
    offsets = []
    for index, value in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{index} 0 obj\n".encode() + value + b"\nendobj\n"
    xref = len(raw)
    raw += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    raw += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets)
    return raw + (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                  f"startxref\n{xref}\n%%EOF\n").encode()


def _check_neutral_sections(cli: OnboardingCLI) -> None:
    """Read three original formats; neutral content is never imported or rendered."""
    print("Onboarding: extractor 4 neutral boundaries in text, PDF and static TeX...", flush=True)
    segments = (
        ("Publications", FACTS[5], "publication", "Research Interests",
         "Reliable evaluation and reproducible fictional benchmarks."),
        ("Education", FACTS[4], "education", "Academic Research",
         "Contributed fictional replication scripts; did not lead the study."),
        ("Research Experience", FACTS[2], "research_description", "Selected Research",
         "Contributed fictional data checks; did not lead the project."),
        ("Publications", "Quill, A. Fictional Taxonomy; in preparation, not submitted.",
         "publication", "Professional Memberships", "Member of the Fictional Computing Society since 2024."),
    )
    lines = ["Name: " + FACTS[0]]
    latex = [r"\begin{document}", "Name: " + FACTS[0] + r"\par"]
    for heading, fact, _, neutral, body in segments:
        lines.extend((heading, fact, neutral, body))
        latex.extend((r"\section*{" + heading + "}", fact + r"\par",
                      r"\section*{" + neutral + "}", body + r"\par"))
    lines.extend(("Skills", FACTS[6]))
    latex.extend((r"\section*{Skills}", FACTS[6] + r"\par", r"\end{document}"))
    text = "\n".join(lines) + "\n"
    expected = [("candidate_name", FACTS[0]),
                *((claim_type, fact) for _, fact, claim_type, _, _ in segments),
                ("skill_use", FACTS[6])]
    assert not cli.runtime.exists()
    for format, suffix, raw in (("text", "txt", text.encode()),
                               ("pdf", "pdf", synthetic_pdf(text)),
                               ("latex", "tex", ("\n".join(latex) + "\n").encode())):
        source = cli.workspace / ("fictional-neutral-sections." + suffix)
        source.write_bytes(raw)
        before = source.stat().st_mtime_ns
        extracted = cli("profile", "extract", "--source-file", str(source), "--extractor-version", "4")
        assert not cli.runtime.exists() and source.read_bytes() == raw and source.stat().st_mtime_ns == before
        assert extracted["extractor_version"] == 4 and extracted["extractor"] == "grounded-apply.exact-resume-lines@4"
        document = extracted["document"]
        assert document["format"] == format and document["document_sha256"] == sha256(raw).hexdigest()
        assert document["extracted_text_sha256"] == extracted["source_sha256"]
        # Classification gaps are visible even when document decoding is complete.
        assert document["incomplete"] is False and document["issues"] == []
        assert extracted["skipped_lines"] == 4
        inventory = extracted["inventory"]
        assert len(inventory) == 19 and [row["text"] for row in inventory] == lines
        assert sum(row["status"] == "heading" for row in inventory) == 9
        assert [row["text"] for row in inventory if row["status"] == "unclassified"] == [row[4] for row in segments]
        for _, _, _, neutral, _ in segments:
            row = next(row for row in inventory if row["text"] == neutral)
            assert row["status"] == "heading" and row["proposal_index"] is None
            assert row["reason"] == "Neutral section boundary; no fact classification"
        assert [(proposal["claim_type"], proposal["canonical_text"]) for proposal in extracted["proposals"]] == expected
        for index, proposal in enumerate(extracted["proposals"]):
            row = next(row for row in inventory if row["proposal_index"] == index)
            assert proposal["span"] == {"start": row["start"], "end": row["end"], "text": row["text"]}
        if format == "pdf":
            assert document["page_count"] == 1 and document["warnings"]


def _check_publication_grouping(cli: OnboardingCLI) -> None:
    """Keep every supported fact while grouping explicitly chosen publications."""
    print("Onboarding: explicit publication grouping preserves all facts and visible gaps...", flush=True)
    title = "- Quill, A. Fictional Widget Study."
    status = "Submitted September 2026; under review, not accepted."
    second_title = "- Quill, A. Fictional Benchmark Taxonomy."
    second_status = "Manuscript in preparation; not submitted."
    unknown = "Reliable evaluation and reproducible fictional benchmarks."
    text = PLAIN.replace(FACTS[5], title + "\n  " + status + "\n" + second_title + "\n  " + second_status).replace(
        "Skills\n", "Research Interests\n" + unknown + "\nSkills\n")
    latex = LATEX.replace(FACTS[5], (r"\\" + "\n").join((title, status, second_title, second_status))).replace(
        r"\section*{Skills}", r"\section*{Research Interests}" + "\n" + unknown + "\n" + r"\section*{Skills}")
    previous_home = cli.environment["GROUNDED_APPLY_HOME"]
    for format, suffix, raw, gap in (("text", "txt", text.encode(), "\n  "),
                                    ("pdf", "pdf", synthetic_pdf(text), "\n  "),
                                    ("latex", "tex", latex.encode(), "\n")):
        cli.environment["GROUNDED_APPLY_HOME"] = str(cli.workspace / ("runtime-group-" + format))
        source = cli.workspace / ("fictional-grouped-publication." + suffix)
        source.write_bytes(raw)
        before_source = source.stat().st_mtime_ns
        extracted = cli("profile", "extract", "--source-file", str(source), "--extractor-version", "4")
        assert len(extracted["proposals"]) == 10 and extracted["skipped_lines"] == 1
        arguments = ("profile", "group-publication", "--source-file", str(source), "--source-format", "auto",
            "--source-sha256", extracted["source_sha256"],
            "--document-sha256", extracted["document"]["document_sha256"],
            "--extractor-version", "4", "--indexes", "5,6")
        grouped = cli(*arguments)
        assert grouped["schema_version"] == 1 and grouped["read_only"] and grouped["review_required"]
        assert "groups" not in grouped
        assert grouped["content_trust"] == "untrusted"
        assert not grouped["storage_changed"] and not grouped["profile_read"] and grouped["network_requests"] == 0
        assert not cli.runtime.exists()
        assert grouped["source_sha256"] == extracted["source_sha256"]
        assert grouped["extractor"] == extracted["extractor"] and grouped["extractor_version"] == 4
        assert grouped["document"] == extracted["document"]
        assert not grouped["document"]["incomplete"] and not grouped["document"]["original_document_provenance_stored"]
        assert grouped["inventory"] == extracted["inventory"]
        assert grouped["skipped_lines"] == grouped["unclassified_count"] == 1 and grouped["blocked_count"] == 0
        assert [row["text"] for row in grouped["inventory"] if row["status"] == "unclassified"] == [unknown]
        assert grouped["grouped_indexes"] == [5, 6] and grouped["grouped_manifest_index"] == 5
        assert grouped["original_proposal_count"] == 10 and grouped["manifest_proposal_count"] == 9
        assert grouped["index_mapping"] == [{"original_index": index, "manifest_index": destination}
                                             for index, destination in enumerate((0, 1, 2, 3, 4, 5, 5, 6, 7, 8))]
        manifest = grouped["manifest"]
        assert set(manifest) == {"schema_version", "source_sha256", "span_index_base", "span_unit", "span_end", "proposals"}
        assert manifest["schema_version"] == 2 and manifest["source_sha256"] == extracted["source_sha256"]
        assert manifest["span_index_base"] == 0 and manifest["span_unit"] == "unicode_codepoint" and manifest["span_end"] == "exclusive"
        proposals = manifest["proposals"]
        assert len(proposals) == len(FACTS) + 2
        for old_index, new_index in ((0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (7, 6), (8, 7), (9, 8)):
            assert proposals[new_index] == {key: extracted["proposals"][old_index][key] for key in proposals[new_index]}
        for proposal in proposals:
            assert {"claim_type", "value", "canonical_text", "span"} <= set(proposal) <= {"claim_type", "value", "canonical_text", "span", "confidence"}
        joined = proposals[5]
        exact = title + gap + status
        canonical = title + " " + status
        assert joined["claim_type"] == "publication" and joined["canonical_text"] == joined["value"] == canonical
        assert joined["span"] == {"start": extracted["proposals"][5]["span"]["start"],
            "end": extracted["proposals"][6]["span"]["end"], "text": exact}
        assert [proposal["canonical_text"] for proposal in proposals] == [*FACTS[:5], canonical,
            second_title.removeprefix("- "), second_status, FACTS[6]]
        multiple_arguments = (*arguments, "--indexes", "7,8")
        reversed_arguments = (*arguments[:-2], "--indexes", "7,8", "--indexes", "5,6")
        multiple = cli(*multiple_arguments)
        reversed_groups = cli(*reversed_arguments)
        assert set(multiple) == (set(grouped) - {"grouped_indexes", "grouped_manifest_index"}) | {"groups"}
        assert multiple["schema_version"] == reversed_groups["schema_version"] == 2
        assert multiple["groups"] == [{"indexes": [5, 6], "manifest_index": 5}, {"indexes": [7, 8], "manifest_index": 6}]
        assert reversed_groups["groups"] == list(reversed(multiple["groups"]))
        assert {key: value for key, value in multiple.items() if key != "groups"} == {
            key: value for key, value in reversed_groups.items() if key != "groups"}
        for key in set(multiple) - {"schema_version", "groups", "manifest", "manifest_proposal_count", "index_mapping"}:
            assert multiple[key] == grouped[key]
        assert multiple["manifest_proposal_count"] == 8 and not cli.runtime.exists()
        assert multiple["index_mapping"] == [{"original_index": index, "manifest_index": destination}
                                              for index, destination in enumerate((0, 1, 2, 3, 4, 5, 5, 6, 6, 7))]
        combined_manifest = multiple["manifest"]
        assert {key: value for key, value in combined_manifest.items() if key != "proposals"} == {
            key: value for key, value in manifest.items() if key != "proposals"}
        second_canonical = second_title + " " + second_status
        proposals = combined_manifest["proposals"]
        assert len(proposals) == 8 and proposals[:6] == manifest["proposals"][:6]
        assert proposals[7] == manifest["proposals"][8]
        assert proposals[6]["claim_type"] == "publication"
        assert proposals[6]["canonical_text"] == proposals[6]["value"] == second_canonical
        assert proposals[6]["span"] == {"start": extracted["proposals"][7]["span"]["start"],
            "end": extracted["proposals"][8]["span"]["end"], "text": second_title + gap + second_status}
        assert {"claim_type", "value", "canonical_text", "span"} <= set(proposals[6]) <= {"claim_type", "value", "canonical_text", "span", "confidence"}
        assert [proposal["canonical_text"] for proposal in proposals] == [*FACTS[:5], canonical, second_canonical, FACTS[6]]
        for selection in (arguments, multiple_arguments):
            for option in ("--source-sha256", "--document-sha256"):
                wrong = list(selection)
                wrong[wrong.index(option) + 1] = "0" * 64
                cli(*wrong, expected=2)
        if format == "text":
            wrong = list(arguments)
            wrong[wrong.index("--indexes") + 1] = "0,1"
            cli(*wrong, expected=2)
            for conflicting in ("5,6", "6,7"):
                cli(*arguments, "--indexes", conflicting, expected=2)
        assert not cli.runtime.exists()
        manifest_path = cli.workspace / ("fictional-grouped-manifest-" + format + ".json")
        manifest_path.write_text(json.dumps(combined_manifest), encoding="utf-8")
        intake = ("profile", "import", "--source-file", str(source), "--source-format", "auto",
            "--document-sha256", extracted["document"]["document_sha256"],
            "--proposals-file", str(manifest_path), "--retain-all-facts", "--idempotency-key", "fictional-group-" + format)
        preview = cli(*intake, "--dry-run")
        assert preview["proposal_count"] == 8 and preview["review_required"] and not cli.runtime.exists()
        cli("profile", "init")
        imported = cli(*intake)
        assert len(imported["claim_ids"]) == 8 and imported["review_required"]
        before = _inventory_snapshot(cli.runtime)
        review = cli("profile", "review")
        assert review["pending_count"] == 8 and len(review["items"]) == 8
        by_claim = {item["claim"]["id"]: item for item in review["items"]}
        for index, claim_id in enumerate(imported["claim_ids"]):
            item, proposal = by_claim[claim_id], proposals[index]
            assert item["proposal_index"] == index and item["claim"]["canonical_text"] == proposal["canonical_text"]
            assert item["claim"]["claim_type"] == proposal["claim_type"] and item["claim"]["value_json"] == proposal["value"]
            assert item["claim"]["scope"] == {"type": "global", "id": None} and item["claim"]["source_type"] == "imported_resume"
            assert item["claim"]["status"] == "needs_review" and item["claim"]["approval_status"] == "pending" and not item["usable"]
            assert len(item["evidence"]) == 1
            evidence = item["evidence"][0]
            assert evidence["source_text"] == proposal["span"]["text"]
            assert evidence["checksum"] == sha256(evidence["source_text"].encode()).hexdigest()
            assert evidence["locator"]["source_sha256"] == extracted["source_sha256"]
            assert (evidence["locator"]["start"], evidence["locator"]["end"]) == (proposal["span"]["start"], proposal["span"]["end"])
        assert cli(*intake) == imported and cli(*arguments) == grouped
        assert cli(*multiple_arguments) == multiple and cli(*reversed_arguments) == reversed_groups
        assert _inventory_snapshot(cli.runtime) == before
        assert source.read_bytes() == raw and source.stat().st_mtime_ns == before_source
    cli.environment["GROUNDED_APPLY_HOME"] = previous_home
    # A classification gap above is independent of an actual unreadable TeX group.
    partial = cli.workspace / "fictional-grouped-partial.tex"
    partial.write_text(latex.replace(r"\end{document}", r"\par\fictionalUnknown{Unresolved fictional appendix}" + "\n" + r"\end{document}"), encoding="utf-8")
    extracted = cli("profile", "extract", "--source-file", str(partial), "--extractor-version", "4")
    arguments = ("profile", "group-publication", "--source-file", str(partial), "--source-format", "auto",
        "--source-sha256", extracted["source_sha256"], "--document-sha256", extracted["document"]["document_sha256"],
        "--extractor-version", "4", "--indexes", "5,6")
    assert extracted["document"]["incomplete"] and extracted["document"]["issues"]
    cli(*arguments, expected=2)
    grouped = cli(*arguments, "--allow-partial")
    assert grouped["document"]["incomplete"] and grouped["document"]["issues"]
    assert grouped["skipped_lines"] == grouped["unclassified_count"] == 1
    assert grouped["schema_version"] == 1 and grouped["manifest_proposal_count"] == 9 and not cli.runtime.exists()
    cli(*arguments, "--indexes", "7,8", expected=2)
    multiple = cli(*arguments, "--indexes", "7,8", "--allow-partial")
    assert multiple["schema_version"] == 2 and multiple["manifest_proposal_count"] == 8
    assert multiple["document"] == grouped["document"] and multiple["inventory"] == grouped["inventory"]
    assert multiple["skipped_lines"] == multiple["unclassified_count"] == 1 and not cli.runtime.exists()


# The temporary hook proves which package the supplied entry point uses and
# refuses every socket audit event. It lives outside the checkout and is never
# shipped as application code. The isolated PDF worker deliberately ignores it;
# that worker parses only the supplied synthetic bytes using its real dependency.
_TEST_HOOK = r'''
import json
import os
import sys
from pathlib import Path

_trace = Path(os.environ["GAPPLY_ONBOARDING_TEST_TRACE"])

def _record(event, **fields):
    with _trace.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **fields}) + "\n")

def _forbid_network(event, arguments):
    if event.startswith("socket."):
        _record("unexpected_network")
        raise RuntimeError("Synthetic onboarding gate forbids network access")

sys.addaudithook(_forbid_network)
import grounded_apply
_record("hook_loaded", module_file=str(Path(grounded_apply.__file__).resolve()))

if os.environ.get("GAPPLY_DIRECT_REVIEW_PREFLIGHT") == "1":
    import grounded_apply.config as _config

    def _forbid_runtime_resolution(*args, **kwargs):
        _record("unexpected_runtime_resolution")
        raise RuntimeError("Direct review arguments must validate before runtime resolution")

    _config.resolve_runtime_paths = _forbid_runtime_resolution
'''


def runtime_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    """Capture bytes and metadata so read-only checks detect SQLite side effects."""
    if not root.exists():
        return ()
    paths = [root, *sorted(root.rglob("*"))]
    return tuple((str(path.relative_to(root)), path.lstat().st_mode,
                  path.lstat().st_size, path.lstat().st_mtime_ns,
                  sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
                 for path in paths)


class OnboardingCLI:
    """Actual CLI invocation with a private synthetic home and quiet diagnostics."""

    def __init__(self, command: list[str], workspace: Path,
                 *, source_path: Path | None = None) -> None:
        self.command, self.workspace = command, workspace.resolve()
        self.repository = Path(__file__).resolve().parents[1]
        self.source_path = None if source_path is None else source_path.resolve()
        if (not command or not self.workspace.is_dir()
                or self.workspace.is_relative_to(self.repository)
                or list(self.workspace.iterdir())):
            raise RuntimeError("Onboarding gate requires an empty external workspace and CLI command")
        self.workspace.chmod(0o700)
        hooks = self.workspace / "test-hooks"
        hooks.mkdir(mode=0o700)
        (hooks / "sitecustomize.py").write_text(_TEST_HOOK, encoding="utf-8")
        self.hooks, self.trace = hooks, self.workspace / "synthetic-boundary-trace.jsonl"
        self.environment = dict(os.environ)
        self.environment.pop("PYTHONHOME", None)
        self.environment.pop("PYTHONPATH", None)
        paths = [str(hooks)] + ([] if self.source_path is None else [str(self.source_path)])
        self.environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
            PYTHONPATH=os.pathsep.join(paths), GROUNDED_APPLY_HOME=str(self.workspace / "runtime"),
            GAPPLY_ONBOARDING_TEST_TRACE=str(self.trace))

    @property
    def runtime(self) -> Path:
        return Path(self.environment["GROUNDED_APPLY_HOME"])

    def events(self) -> list[dict[str, Any]]:
        return [] if not self.trace.exists() else [json.loads(line) for line in self.trace.read_text().splitlines()]

    def __call__(self, *args: str, expected: int = 0,
                 stdin_text: str | None = None) -> dict[str, Any]:
        previous = len(self.events())
        process = subprocess.run([*self.command, "--log-events", *args, "--json"],
            cwd=self.workspace, env=self.environment,
            stdin=subprocess.DEVNULL if stdin_text is None else None, input=stdin_text,
            text=True, capture_output=True, timeout=60)
        events = self.events()[previous:]
        if len(events) != 1 or events[0]["event"] != "hook_loaded":
            raise RuntimeError("Onboarding test hook did not load cleanly or attempted network access")
        origin = Path(events[0]["module_file"])
        if origin.is_relative_to(self.hooks):
            raise RuntimeError("Onboarding hook shadowed application code")
        if self.source_path is not None:
            if not origin.is_relative_to(self.source_path):
                raise RuntimeError("Onboarding gate imported the wrong source package")
        elif origin.is_relative_to(self.repository) or "site-packages" not in origin.parts:
            raise RuntimeError("Onboarding gate did not use its fresh installed wheel")
        if process.returncode != expected:
            raise RuntimeError(f"Synthetic onboarding CLI failed {args[:2]} ({process.returncode}): "
                               f"{process.stdout}{process.stderr}")
        private_arguments = [args[index + 1] for index, argument in enumerate(args[:-1])
                             if argument in {"--after", "--after-json", "--topic", "--claim-id", "--claim-id-json", "--review-token",
                                             "--source-sha256", "--document-sha256", "--indexes"}]
        for forbidden in ("Avery", "Quill", "example.com", "fictional-intake",
                          "Unsupported contribution", str(self.workspace), "synthetic-private-cursor",
                          *private_arguments):
            if forbidden in process.stderr:
                raise RuntimeError("Synthetic candidate or command data leaked into diagnostics")
        diagnostics = [json.loads(line) for line in process.stderr.splitlines()]
        assert diagnostics, "CLI omitted the requested diagnostic event stream"
        assert all(set(event) == EVENT_FIELDS for event in diagnostics)
        assert all(event["command"] == ".".join(args[:2]) for event in diagnostics)
        if args[:2] in {("profile", "inventory"), ("profile", "group-publication"), ("profile", "review")}:
            assert [event["outcome"] for event in diagnostics] == ["started", "succeeded" if expected == 0 else "failed"]
            assert all(event["recovery"] is None for event in diagnostics)
        envelope = json.loads(process.stdout)
        assert envelope["ok"] is (expected == 0)
        return envelope["data"] if expected == 0 else envelope


def _inventory_snapshot(root: Path) -> tuple[object, ...]:
    """Include stable identity/ownership metadata; ordinary read atime is omitted."""
    paths = [root, *sorted(root.rglob("*"))]
    metadata = []
    for path in paths:
        stat = path.lstat()
        metadata.append((str(path.relative_to(root)), stat.st_dev, stat.st_ino,
                         stat.st_nlink, stat.st_uid, stat.st_gid, stat.st_ctime_ns))
    return runtime_snapshot(root), tuple(metadata)


def _inventory_read(cli: OnboardingCLI, *arguments: str) -> dict[str, Any]:
    before = _inventory_snapshot(cli.runtime)
    result = cli("profile", "inventory", *arguments)
    assert _inventory_snapshot(cli.runtime) == before
    assert set(result) == {"schema_version", "total_claim_count", "topics", "selected_topic", "items", "page",
                           "read_only", "content_trust", "usability_assessed", "completeness_assessed",
                           "interview_progress_assessed"}
    assert result["schema_version"] == 1 and result["read_only"] is True
    assert result["content_trust"] == "untrusted"
    assert all(result[flag] is False for flag in
               ("usability_assessed", "completeness_assessed", "interview_progress_assessed"))
    return result


def _check_direct_review_arguments(cli: OnboardingCLI) -> None:
    """Selectors reject incompatible or malformed inputs before any runtime read."""
    assert not cli.runtime.exists()
    private = "synthetic-private-direct-selector"
    encoded = json.dumps(private)
    cli.environment["GAPPLY_DIRECT_REVIEW_PREFLIGHT"] = "1"
    try:
        for arguments in (
            ("--claim-id", private, "--claim-id-json", encoded),
            ("--claim-id", private, "--limit", "1"),
            ("--claim-id", private, "--after", private),
            ("--claim-id", private, "--after-json", encoded),
            ("--claim-id-json", encoded, "--limit", "1"),
            ("--claim-id-json", encoded, "--after", private),
            ("--claim-id-json", encoded, "--after-json", encoded),
            ("--claim-id-json", '{"synthetic-private-selector":'),
            ("--claim-id-json", json.dumps({"private": private})),
            ("--claim-id-json", json.dumps(" ")),
            ("--claim-id-json", '"\\ud800synthetic-private-selector"'),
        ):
            refused = cli("profile", "review", *arguments, expected=2)
            assert refused["data"] is None and refused["error"] is not None
            assert private not in json.dumps(refused) and "synthetic-private-selector" not in json.dumps(refused)
            assert not cli.runtime.exists()
    finally:
        del cli.environment["GAPPLY_DIRECT_REVIEW_PREFLIGHT"]


def _direct_review_refusal(cli: OnboardingCLI, claim_id: str, *, encoded: bool = False) -> None:
    before = _inventory_snapshot(cli.runtime)
    flag, value = ("--claim-id-json", json.dumps(claim_id)) if encoded else ("--claim-id", claim_id)
    refused = cli("profile", "review", flag, value, expected=2)
    assert refused["data"] is None
    assert refused["error"] == {"type": "CliInputError", "message":
        "Selected pending fact could not be reviewed. Refresh profile inventory or the pending review queue; no state was changed"}
    assert all(private not in json.dumps(refused) for private in (claim_id, value, *FACTS, str(cli.runtime)))
    assert _inventory_snapshot(cli.runtime) == before


def _check_direct_review(cli: OnboardingCLI, items: list[dict[str, Any]], *,
                         source: Path | None = None) -> str:
    """Bridge inventory to one exact pending item while preserving the full queue."""
    print("Onboarding: one inventory-selected pending fact with its exact evidence...", flush=True)
    before = _inventory_snapshot(cli.runtime)
    source_before = None if source is None else (source.read_bytes(), source.stat())
    inventory = _inventory_read(cli, "--topic", "research", "--limit", "1")
    target = inventory["items"][0]
    assert target["status"] == "needs_review" and target["approval_status"] == "pending"
    claim_id = target["id"]
    selected = next(item for item in items if item["claim"]["id"] == claim_id)
    expected = {"items": [selected], "pending_count": len(items), "read_only": True}
    plain = cli("profile", "review", "--claim-id", claim_id)
    encoded = cli("profile", "review", "--claim-id-json", json.dumps(claim_id))
    assert plain == encoded == expected and "page" not in plain
    assert selected["review_token"] and selected["evidence"]
    serialized = json.dumps(plain)
    for item in items:
        if item is not selected:
            assert all(value not in serialized for value in (
                item["claim"]["id"], item["claim"]["canonical_text"], item["review_token"]))
    _direct_review_refusal(cli, "synthetic-private-missing-direct-fact")
    _direct_review_refusal(cli, "synthetic-private-missing / \u2603\n\x00", encoded=True)
    assert cli("profile", "review") == {"items": items, "pending_count": len(items), "read_only": True}
    assert _inventory_snapshot(cli.runtime) == before
    if source is not None:
        assert source_before is not None and source.read_bytes() == source_before[0]
        previous, current = source_before[1], source.stat()
        assert all(getattr(previous, field) == getattr(current, field) for field in
                   ("st_mode", "st_size", "st_mtime_ns", "st_ctime_ns", "st_dev", "st_ino", "st_nlink", "st_uid", "st_gid"))
    return claim_id


def _check_inventory(cli: OnboardingCLI, claims: list[dict[str, Any]]) -> None:
    """Verify compact retained context and exact, bounded research continuation."""
    overview = _inventory_read(cli)
    assert overview["selected_topic"] is None and overview["items"] == [] and overview["page"] is None
    assert overview["total_claim_count"] == len(claims)
    assert [row["topic"] for row in overview["topics"]] == list(INVENTORY_TOPICS)
    for row in overview["topics"]:
        selected = [claim for claim in claims if FIXTURE_TOPICS.get(claim["claim_type"], "other") == row["topic"]]
        assert set(row) == {"topic", "claim_count", "states"} and row["claim_count"] == len(selected)
        states = [{"status": status, "approval_status": approval, "count": count}
            for status in ("verified", "derived", "needs_review", "contradicted", "superseded", "withdrawn")
            for approval in ("pending", "approved", "rejected")
            if (count := sum(claim["status"] == status and claim["approval_status"] == approval for claim in selected))]
        assert row["states"] == states
    serialized = json.dumps(overview)
    assert all(value not in serialized for claim in claims for value in (claim["id"], claim["canonical_text"]))
    assert all(value not in serialized for value in (*FACTS, str(cli.runtime), "source_ref", "value_json", "evidence"))
    research = [claim for claim in claims if claim["claim_type"] == "research_description"]
    after = None
    for index in range(max(1, len(research))):
        arguments = () if after is None else ("--after-json", json.dumps(after))
        page = _inventory_read(cli, "--topic", "research", "--limit", "1", *arguments)
        expected = [{key: claim[key] for key in INVENTORY_ITEM_FIELDS} for claim in research[index:index + 1]]
        assert page["topics"] == overview["topics"] and page["selected_topic"] == "research"
        assert page["items"] == expected
        later = max(0, len(research) - index - len(expected))
        assert page["page"] == {"limit": 1, "total_count": len(research), "returned_count": len(expected),
            "before_count": index, "after_count": later, "next_after": expected[-1]["id"] if later else None}
        if expected:
            after = expected[-1]["id"]
    if after is not None:
        final = _inventory_read(cli, "--topic", "research", "--limit", "1", "--after", after)
        assert final["items"] == [] and final["page"] == {"limit": 1, "total_count": len(research),
            "returned_count": 0, "before_count": len(research), "after_count": 0, "next_after": None}


def _check_inventory_lifecycle(cli: OnboardingCLI) -> None:
    """Use two existing scripted facts in a separate home; render no extra PDF."""
    print("Onboarding: retained inventory survives rejection and retirement...", flush=True)
    previous_home = cli.environment["GROUNDED_APPLY_HOME"]
    cli.environment["GROUNDED_APPLY_HOME"] = str(cli.workspace / "runtime-inventory-lifecycle")
    try:
        source, manifest = statement_fixture()
        manifest["proposals"] = manifest["proposals"][2:4]
        path = cli.workspace / "fictional-inventory-proposals.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        cli("profile", "init")
        _check_inventory(cli, [])
        cli("profile", "import", "--source-kind", "user-statement", "--source-file", "-",
            "--proposals-file", str(path), "--retain-all-facts", "--idempotency-key", "fictional-inventory",
            stdin_text=source)
        items = cli("profile", "review")["items"]
        assert [item["claim"]["canonical_text"] for item in items] == list(FACTS[2:4])
        _check_inventory(cli, cli("profile", "show")["claims"])
        anchor = _inventory_read(cli, "--topic", "research", "--limit", "1")["page"]["next_after"]
        rejected_item = next(item for item in items if item["claim"]["id"] == anchor)
        approved_item = next(item for item in items if item["claim"]["id"] != anchor)
        decision = ("profile", "decide", "--claim-id", anchor, "--review-token", rejected_item["review_token"],
            "--decision", "reject", "--actor-id", "synthetic-reviewer", "--idempotency-key", "fictional-inventory-reject")
        before = _inventory_snapshot(cli.runtime)
        assert cli(*decision)["requires_confirmation"] and _inventory_snapshot(cli.runtime) == before
        rejected = cli(*decision, "--confirm")
        assert rejected["claim_status"] == "withdrawn" and rejected["claim_approval_status"] == "rejected"
        _approve_review_item(cli, approved_item)
        _check_inventory(cli, cli("profile", "show")["claims"])
        continued = _inventory_read(cli, "--topic", "research", "--limit", "1", "--after", anchor)
        assert continued["items"][0]["id"] == approved_item["claim"]["id"]
        retirement = ("profile", "retire", "--claim-id", approved_item["claim"]["id"],
            "--actor-id", "synthetic-reviewer", "--idempotency-key", "fictional-inventory-retire")
        before = _inventory_snapshot(cli.runtime)
        preview = cli(*retirement)
        assert preview["dry_run"] and _inventory_snapshot(cli.runtime) == before
        assert cli(*retirement, "--preview-token", preview["preview_token"], "--confirm")["recorded"]
        claims = cli("profile", "show")["claims"]
        assert all(claim["status"] == "withdrawn" for claim in claims)
        assert [claim["approval_status"] for claim in claims] == ["rejected", "approved"]
        _check_inventory(cli, claims)
        continued = _inventory_read(cli, "--topic", "research", "--limit", "1", "--after", anchor)
        assert continued["items"][0]["status"] == "withdrawn"
        final = _inventory_read(cli, "--topic", "research", "--after", approved_item["claim"]["id"])
        assert final["items"] == [] and final["page"]["before_count"] == 2
    finally:
        cli.environment["GROUNDED_APPLY_HOME"] = previous_home


def _onboard_arguments(path: Path, extraction: dict[str, Any], key: str) -> tuple[str, ...]:
    return ("profile", "onboard", "--source-file", str(path), "--select", "all",
            "--extractor-version", str(extraction["extractor_version"]),
            "--source-sha256", extraction["source_sha256"],
            "--document-sha256", extraction["document"]["document_sha256"],
            "--idempotency-key", key)


def classified_manifest(extraction: dict[str, Any]) -> dict[str, object]:
    """Explicit synthetic classification of one previously unclassified span."""
    assert not extraction["proposals"] and len(extraction["inventory"]) == 1
    line = extraction["inventory"][0]
    assert line["status"] == "unclassified"
    return {"schema_version": 2, "source_sha256": extraction["source_sha256"],
        "span_index_base": 0, "span_unit": "unicode_codepoint", "span_end": "exclusive",
        "proposals": [{"claim_type": "employment_description", "value": line["text"],
            "canonical_text": line["text"],
            "span": {"start": line["start"], "end": line["end"], "text": line["text"]}}]}


def statement_fixture() -> tuple[str, dict[str, object]]:
    """Exact fictional answers with an explicit proposed classification."""
    source = "\n".join(FACTS) + "\n"
    types = ("candidate_name", "contact_email", "research_description",
             "research_description", "education", "publication", "skill_use")
    proposals: list[dict[str, object]] = []
    start = 0
    for claim_type, fact in zip(types, FACTS, strict=True):
        proposals.append({"claim_type": claim_type, "value": fact, "canonical_text": fact,
            "span": {"start": start, "end": start + len(fact), "text": fact}})
        start += len(fact) + 1
    return source, {"schema_version": 2, "source_sha256": sha256(source.encode()).hexdigest(),
        "span_index_base": 0, "span_unit": "unicode_codepoint", "span_end": "exclusive",
        "proposals": proposals}


def _check_statement_intake(command: list[str], workspace: Path, *,
                            source_path: Path | None, include_materials: bool,
                            python: str | None) -> dict[str, object]:
    """Exercise optional interview answers without supplying or extracting a CV."""
    print("Onboarding: exact user statements, separate approval and fresh-process resumption...", flush=True)
    workspace.mkdir(mode=0o700)
    cli = OnboardingCLI(command, workspace, source_path=source_path)
    questions = cli("profile", "interview", "--topic", "research", "--depth", "2", "--limit", "1")
    assert not questions["answers_stored"] and not cli.runtime.exists()
    source, manifest = statement_fixture()
    path = workspace / "fictional-answer-proposals.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    arguments = ("profile", "import", "--source-kind", "user-statement", "--source-file", "-",
        "--proposals-file", str(path), "--retain-all-facts", "--idempotency-key", "fictional-interview-answers")
    preview = cli(*arguments, "--dry-run", stdin_text=source)
    assert preview["source_type"] == "user_statement" and preview["review_required"]
    assert preview["proposal_count"] == len(FACTS) and not cli.runtime.exists()
    cli("profile", "init")
    imported = cli(*arguments, stdin_text=source)
    assert imported["source_type"] == "user_statement" and imported["review_required"]
    before = runtime_snapshot(cli.runtime)
    review = cli("profile", "review")
    assert review["pending_count"] == len(FACTS)
    assert [item["claim"]["canonical_text"] for item in review["items"]] == list(FACTS)
    for item in review["items"]:
        assert item["claim"]["source_type"] == "user_statement"
        assert item["claim"]["status"] == "needs_review"
        assert item["claim"]["approval_status"] == "pending"
        assert all(evidence["source_type"] == "user_statement" and
                   evidence["source_ref"] == imported["source_ref"] for evidence in item["evidence"])
    assert cli(*arguments, stdin_text=source) == imported
    cli("profile", "interview", "--topic", "research", "--depth", "2", "--after", questions["next_cursor"])
    assert runtime_snapshot(cli.runtime) == before
    _check_inventory(cli, cli("profile", "show")["claims"])
    direct_id = _check_direct_review(cli, review["items"])
    _check_paged_approvals(cli, review["items"])
    _direct_review_refusal(cli, direct_id)
    before = runtime_snapshot(cli.runtime)
    profile = cli("profile", "show")
    assert len(profile["claims"]) == len(FACTS)
    assert all(claim["source_type"] == "user_statement" and claim["status"] == "verified"
               for claim in profile["claims"])
    replay = cli(*arguments, stdin_text=source)
    assert replay["claim_ids"] == imported["claim_ids"] and not replay["review_required"]
    wrong_origin = list(arguments)
    wrong_origin[wrong_origin.index("--source-kind") + 1] = "resume"
    cli(*wrong_origin, stdin_text=source, expected=2)
    assert runtime_snapshot(cli.runtime) == before
    _check_inventory(cli, profile["claims"])
    if include_materials:
        _check_research_material(cli, imported["claim_ids"], python=python)
    return {"retained_count": len(FACTS), "approved_count": len(FACTS),
            "source_type": "user_statement", "fresh_process_resume_checked": True,
            "material_checked": include_materials}


def _review_page(cli: OnboardingCLI, expected_items: list[dict[str, Any]], *,
                 pending: int, earlier: int, later: int,
                 after: str | None = None) -> dict[str, Any]:
    """Verify a fresh read-only page retains the original facts and exact tokens."""
    before = runtime_snapshot(cli.runtime)
    arguments = () if after is None else ("--after", after)
    page = cli("profile", "review", "--limit", "2", *arguments)
    assert runtime_snapshot(cli.runtime) == before
    assert set(page) == {"items", "pending_count", "read_only", "page"}
    assert page["items"] == expected_items
    assert page["pending_count"] == pending and page["read_only"] is True
    assert page["page"] == {
        "limit": 2, "returned_count": len(expected_items),
        "pending_before_count": earlier, "pending_after_count": later,
        "next_after": expected_items[-1]["claim"]["id"] if later else None,
    }
    return page


def _approve_review_item(cli: OnboardingCLI, item: dict[str, Any]) -> None:
    """Approve only the displayed item, after a nonmutating explicit preview."""
    claim_id = item["claim"]["id"]
    decision = ("profile", "decide", "--claim-id", claim_id,
        "--review-token", item["review_token"], "--decision", "approve",
        "--actor-id", "synthetic-reviewer", "--idempotency-key", "approve-" + claim_id)
    before = runtime_snapshot(cli.runtime)
    preview = cli(*decision)
    assert preview["decision_recorded"] is False and preview["requires_confirmation"] is True
    assert preview["claim_id"] == claim_id and runtime_snapshot(cli.runtime) == before
    result = cli(*decision, "--confirm")
    assert result["decision_recorded"] is True and result["claim_id"] == claim_id
    assert result["claim_status"] == "verified" and result["claim_approval_status"] == "approved"
    assert result["proposal_index"] == item["proposal_index"]
    assert result["import_workflow_run_id"] == item["import_workflow_run_id"]
    assert result["external_action_taken"] is False


def _check_paged_approvals(cli: OnboardingCLI, items: list[dict[str, Any]]) -> None:
    """Skip the first page, resume after decided anchors, then revisit every skip."""
    first = _review_page(cli, items[:2], pending=len(items), earlier=0, later=len(items) - 2)
    before = runtime_snapshot(cli.runtime)
    cli("profile", "review", "--limit", "2", "--after", "synthetic-private-cursor", expected=2)
    # A token shown beside one fact cannot authorize approving a different fact.
    cli("profile", "decide", "--claim-id", items[1]["claim"]["id"],
        "--review-token", items[0]["review_token"], "--decision", "approve",
        "--actor-id", "synthetic-reviewer", "--idempotency-key", "mismatched-page-token",
        "--confirm", expected=2)
    assert runtime_snapshot(cli.runtime) == before

    anchor = first["page"]["next_after"]
    approved_count = 0
    for start in range(2, len(items), 2):
        expected = items[start:start + 2]
        page = _review_page(cli, expected, pending=len(items) - approved_count,
            earlier=2, later=len(items) - start - len(expected), after=anchor)
        for item in page["items"]:
            _approve_review_item(cli, item)
            approved_count += 1
        # The next CLI process must find the durable anchor even after approval.
        anchor = page["items"][-1]["claim"]["id"]

    # End of this pass is not completion: the first page still needs decisions.
    _review_page(cli, [], pending=2, earlier=2, later=0, after=anchor)
    restarted = _review_page(cli, items[:2], pending=2, earlier=0, later=0)
    for item in restarted["items"]:
        _approve_review_item(cli, item)
    _review_page(cli, [], pending=0, earlier=0, later=0)


def _check_research_material(cli: OnboardingCLI, claim_ids: list[str], *,
                             python: str | None) -> None:
    """Render only the application-owned template, never either input document."""
    print("Onboarding: approved research facts retain their own generated PDF section...", flush=True)
    source = cli.workspace / "fictional-research-job.txt"
    source.write_text("Fictional Research Laboratory\nResearch Engineer\n\nRequirements\n"
                      "- Experience with Python and reproducible evaluation.\n", encoding="utf-8")
    job = cli("jobs", "add", "--url", "https://example.com/jobs/fictional-research",
        "--source-file", str(source), "--idempotency-key", "fictional-research-job")
    arguments = ("materials", "build", "--job-id", job["job_id"],
        "--claim-ids", ",".join(claim_ids), "--idempotency-key", "fictional-research-material")
    before = runtime_snapshot(cli.runtime)
    plan = cli(*arguments, "--dry-run")
    assert runtime_snapshot(cli.runtime) == before
    assert plan["structure"]["transformation"] == "approved_text_selection@3"
    draft = cli(*arguments)
    identifier = draft["material_id"]
    assert not draft["ready"]
    material = cli("materials", "show", "--material-id", identifier)
    assert material["structure"] == plan["structure"]
    assert material["manifest"]["renderer"] == "grounded-apply.latex-resume@3"
    assert material["validation"]["valid"] and material["validation"]["unsupported_factual_units"] == 0
    units = material["structure"]["units"]
    assert {unit["text"] for unit in units} == set(FACTS)
    assert {unit["claim_id"] for unit in units} == set(claim_ids)
    assert all(unit["claim_id"] in unit["packet_claim_ids"] and unit["evidence_ids"] for unit in units)
    assert [unit["text"] for unit in units if unit["claim_type"] == "research_description"] == list(FACTS[2:4])
    destination = cli.workspace / "fictional-research-draft"
    cli("materials", "export", "--material-id", identifier, "--output-dir", str(destination))
    assert (destination / "resume.pdf").read_bytes().startswith(b"%PDF-")
    text = " ".join((destination / "resume.txt").read_text(encoding="utf-8").split())
    assert "Research" in text and "Publications" in text and "Experience" not in text
    assert all(fact in text for fact in FACTS)
    before = runtime_snapshot(cli.runtime)
    export_before = runtime_snapshot(destination)
    assert cli("materials", "export", "--material-id", identifier, "--output-dir", str(destination))["replayed"]
    assert runtime_snapshot(cli.runtime) == before and runtime_snapshot(destination) == export_before
    approval = ("materials", "approve", "--material-id", identifier,
        "--bundle-sha256", draft["bundle_sha256"], "--actor-id", "synthetic-reviewer",
        "--idempotency-key", "fictional-research-material-approval")
    assert cli(*approval)["dry_run"] and runtime_snapshot(cli.runtime) == before
    assert cli(*approval, "--confirm")["ready"]
    replay = cli(*arguments)
    assert replay["replayed"] and replay["ready"] and replay["material_id"] == identifier
    assert replay["bundle_sha256"] == draft["bundle_sha256"]
    assert cli("applications", "list")["applications"] == []
    _audit_research_material(cli, identifier, python=python)


def _audit_research_material(cli: OnboardingCLI, identifier: str, *, python: str | None) -> None:
    """Run actual source or installed historical validators without writes."""
    audit = """
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
def forbid(event, arguments):
    if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system'}:
        raise RuntimeError('Historical research audit attempted an external action')
    if event == 'open' and arguments[2] & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
        raise RuntimeError('Historical research audit attempted a write')
sys.addaudithook(forbid)
from grounded_apply.config import resolve_runtime_paths
from grounded_apply.repositories import SQLiteRepository
from grounded_apply.repositories.latex_renderer import LatexResumeRenderer
from grounded_apply.services.materials import MaterialService
expected = Path(sys.argv[2] or sys.prefix).resolve()
for implementation in (SQLiteRepository, LatexResumeRenderer, MaterialService):
    if not Path(sys.modules[implementation.__module__].__file__).resolve().is_relative_to(expected):
        raise RuntimeError('Historical research audit imported a different installation')
paths = resolve_runtime_paths()
if paths.portable_root != Path(os.environ['GROUNDED_APPLY_HOME']).resolve() or not paths.portable_root.is_relative_to(Path.cwd()):
    raise RuntimeError('Historical research audit escaped its fictional workspace')
with SQLiteRepository(paths.database, read_only=True) as repository, repository.read_transaction():
    service = MaterialService(repository, LatexResumeRenderer())
    if service.get(sys.argv[1])['structure']['transformation'] != 'approved_text_selection@3':
        raise RuntimeError('Historical research material lost its version')
    service.validate_historical_inventory()
    service.validate_historical_approval_eligibility(sys.argv[1])
    service.validate_historical_use(sys.argv[1], used_at=datetime.now(UTC).isoformat())
"""
    before = runtime_snapshot(cli.runtime)
    environment = dict(cli.environment)
    environment.pop("PYTHONPATH", None)
    arguments = [python or sys.executable, "-B"]
    if cli.source_path is None:
        arguments.append("-I")
    else:
        environment["PYTHONPATH"] = str(cli.source_path)
    process = subprocess.run([*arguments, "-c", audit, identifier,
        "" if cli.source_path is None else str(cli.source_path)],
        cwd=cli.workspace, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60)
    if process.returncode or process.stdout or process.stderr:
        raise RuntimeError("Historical research material audit failed")
    assert runtime_snapshot(cli.runtime) == before


def check_onboarding(command: list[str], workspace: Path,
                     source_path: Path | None = None, *,
                     include_materials: bool = False,
                     python: str | None = None) -> dict[str, object]:
    """Require source/installed TeX and PDF intake; no personal files are accepted."""
    if sys.flags.optimize:
        raise RuntimeError("Onboarding verification requires Python assertions")
    cli = OnboardingCLI(command, workspace, source_path=source_path)
    _check_direct_review_arguments(cli)
    print("Onboarding: optional interview and offline source setup...", flush=True)
    first = cli("profile", "interview")
    assert len(first["questions"]) == 3 and not first["answers_stored"] and not first["profile_read"]
    continued = cli("profile", "interview", "--after", first["next_cursor"], "--limit", "2")
    assert len(continued["questions"]) == 2
    assert not {q["id"] for q in first["questions"]} & {q["id"] for q in continued["questions"]}
    cli("profile", "interview", "--after", "synthetic-private-cursor", expected=2)
    cli("profile", "interview", "--depth", "2", "--after", first["next_cursor"], expected=2)
    sources = cli("jobs", "sources", "--url", "https://boards.greenhouse.io/fictional-intake",
        "--url", "https://boards.greenhouse.io/fictional-intake/jobs/123",
        "--url", "https://example.com/fictional-careers")
    assert sources["automatic_source_count"] == sources["manual_source_count"] == 1
    assert sources["inputs"][1]["duplicate"] and len(sources["manifest"]["sources"]) == 2
    assert sources["network_requests"] == 0 and not sources["storage_changed"]
    assert not sources["live_boards_verified"] and not cli.runtime.exists()
    _check_neutral_sections(cli)
    _check_publication_grouping(cli)

    approved: dict[str, int] = {}
    for format, filename, raw in (("latex", "fictional-resume.tex", LATEX.encode()),
                                  ("pdf", "fictional-resume.pdf", synthetic_pdf())):
        print(f"Onboarding: {format} inventory, all-fact preview, approval, and replay...", flush=True)
        cli.environment["GROUNDED_APPLY_HOME"] = str(cli.workspace / ("runtime-" + format))
        source = cli.workspace / filename
        source.write_bytes(raw)
        before = source.stat().st_mtime_ns
        legacy = cli("profile", "extract", "--source-file", str(source))
        assert legacy["extractor_version"] == 2
        assert [p["claim_type"] for p in legacy["proposals"]][2:4] == ["employment_description"] * 2
        extracted = cli("profile", "extract", "--source-file", str(source), "--extractor-version", "4")
        document = extracted["document"]
        assert document["format"] == format and not document["incomplete"]
        assert document["document_sha256"] == sha256(raw).hexdigest()
        assert document["extracted_text_sha256"] == extracted["source_sha256"]
        assert document["original_document_provenance_stored"] is False
        assert extracted["extractor_version"] == 4 and extracted["skipped_lines"] == 0
        assert [p["claim_type"] for p in extracted["proposals"]][2:4] == ["research_description"] * 2
        assert [p["span"] for p in legacy["proposals"]] == [p["span"] for p in extracted["proposals"]]
        assert [p["canonical_text"] for p in extracted["proposals"]] == list(FACTS)
        assert sum(row["status"] == "proposed" for row in extracted["inventory"]) == len(FACTS)
        assert all(row["status"] in {"heading", "proposed"} for row in extracted["inventory"])
        if format == "pdf":
            assert document["page_count"] == 1 and document["warnings"]
        args = _onboard_arguments(source, extracted, "fictional-intake-" + format)
        preview = cli(*args, "--dry-run")
        assert preview["selected_count"] == preview["proposed_count"] == len(FACTS)
        assert preview["content_policy_version"] == 3 and preview["review_required"]
        assert preview["unselected_proposal_count"] == preview["lines_needing_attention"] == 0
        assert preview["storage_checked"] is False and not cli.runtime.exists()
        assert source.read_bytes() == raw and source.stat().st_mtime_ns == before

        bad_source = list(args)
        bad_source[bad_source.index("--source-sha256") + 1] = "0" * 64
        cli(*bad_source, "--dry-run", expected=2)
        bad_document = list(args)
        bad_document[bad_document.index("--document-sha256") + 1] = "0" * 64
        cli(*bad_document, "--dry-run", expected=2)
        missing_document = list(args)
        index = missing_document.index("--document-sha256")
        del missing_document[index:index + 2]
        cli(*missing_document, "--dry-run", expected=2)
        assert not cli.runtime.exists()

        cli("profile", "init")
        before_preview = runtime_snapshot(cli.runtime)
        cli(*args, "--dry-run")
        assert runtime_snapshot(cli.runtime) == before_preview
        imported = cli(*args)
        assert len(imported["claim_ids"]) == len(FACTS) and imported["review_required"]
        before_review = runtime_snapshot(cli.runtime)
        review = cli("profile", "review")
        assert set(review) == {"items", "pending_count", "read_only"} and review["read_only"] is True
        assert review["pending_count"] == len(FACTS)
        assert {item["claim"]["id"] for item in review["items"]} == set(imported["claim_ids"])
        assert {item["claim"]["canonical_text"] for item in review["items"]} == set(FACTS)
        assert all(item["claim"]["status"] == "needs_review" and
                   item["claim"]["approval_status"] == "pending" for item in review["items"])
        assert cli(*args) == imported and runtime_snapshot(cli.runtime) == before_review
        _check_inventory(cli, cli("profile", "show")["claims"])
        direct_id = _check_direct_review(cli, review["items"], source=source)
        _check_paged_approvals(cli, review["items"])
        _direct_review_refusal(cli, direct_id)
        before_projection = runtime_snapshot(cli.runtime)
        profile = cli("profile", "show")
        assert profile["read_only"] and len(profile["claims"]) == len(FACTS)
        assert {claim["canonical_text"] for claim in profile["claims"]} == set(FACTS)
        assert all(claim["status"] == "verified" and claim["approval_status"] == "approved"
                   for claim in profile["claims"])
        assert cli("profile", "review") == {"items": [], "pending_count": 0, "read_only": True}
        replay = cli(*args)
        assert replay["claim_ids"] == imported["claim_ids"]
        assert replay["workflow_run_id"] == imported["workflow_run_id"] and not replay["review_required"]
        assert runtime_snapshot(cli.runtime) == before_projection
        _check_inventory(cli, profile["claims"])
        approved[format] = len(profile["claims"])
        if format == "pdf" and include_materials:
            _check_research_material(cli, imported["claim_ids"], python=python)
        if format == "latex":
            source.write_bytes(raw + b"% Changed fictional document, same extracted facts.\n")
            refreshed = cli("profile", "extract", "--source-file", str(source), "--extractor-version", "4")
            assert refreshed["source_sha256"] == extracted["source_sha256"]
            assert refreshed["document"]["document_sha256"] != document["document_sha256"]
            cli(*args, expected=2)
            assert runtime_snapshot(cli.runtime) == before_projection

    print("Onboarding: unresolved TeX content requires explicit partial retention...", flush=True)
    cli.environment["GROUNDED_APPLY_HOME"] = str(cli.workspace / "runtime-partial")
    partial = cli.workspace / "fictional-partial.tex"
    partial.write_text(PARTIAL_LATEX, encoding="utf-8")
    extracted = cli("profile", "extract", "--source-file", str(partial))
    assert extracted["document"]["incomplete"] and extracted["document"]["issues"]
    assert len(extracted["proposals"]) == 3
    assert all("Unsupported contribution" not in proposal["canonical_text"] for proposal in extracted["proposals"])
    args = _onboard_arguments(partial, extracted, "fictional-intake-partial")
    cli(*args, "--dry-run", expected=2)
    cli(*args, expected=2)
    assert not cli.runtime.exists()
    allowed = cli(*args, "--allow-partial", "--dry-run")
    assert allowed["selected_count"] == 3 and allowed["document"]["incomplete"]
    assert not cli.runtime.exists()
    cli("profile", "init")
    partial_result = cli(*args, "--allow-partial")
    assert partial_result["review_required"] and len(partial_result["claim_ids"]) == 3
    assert cli("profile", "review")["pending_count"] == 3

    print("Onboarding: explicit classification uses the original document and exact extracted span...", flush=True)
    cli.environment["GROUNDED_APPLY_HOME"] = str(cli.workspace / "runtime-classified")
    unclassified = cli.workspace / "fictional-unclassified.tex"
    unclassified.write_text(UNCLASSIFIED_LATEX, encoding="utf-8")
    extracted = cli("profile", "extract", "--source-file", str(unclassified))
    manifest = cli.workspace / "fictional-classification.json"
    manifest.write_text(json.dumps(classified_manifest(extracted)), encoding="utf-8")
    structured = ("profile", "import", "--source-file", str(unclassified), "--source-format", "auto",
        "--document-sha256", extracted["document"]["document_sha256"],
        "--proposals-file", str(manifest), "--retain-all-facts", "--idempotency-key", "fictional-classification")
    assert cli(*structured, "--dry-run")["proposal_count"] == 1
    assert not cli.runtime.exists()
    cli("profile", "init")
    classified = cli(*structured)
    assert classified["review_required"]
    review = cli("profile", "review")
    assert review["pending_count"] == 1
    assert review["items"][0]["claim"]["canonical_text"] == extracted["inventory"][0]["text"]
    statements = _check_statement_intake(command, workspace / "statement-session",
        source_path=source_path, include_materials=include_materials, python=python)
    _check_inventory_lifecycle(cli)
    report: dict[str, object] = {"formats": ["latex", "pdf"], "approved_counts": approved,
        "partial_pending_count": 3, "classified_pending_count": 1,
        "research_material_checked": include_materials,
        "statement_intake": statements,
        "network_requests": 0, "external_action_taken": False}
    print("PASS — TeX/PDF all-fact onboarding, paged review with skipped facts and decided anchors, "
          "explicit approval, replay, partial refusal, "
          "private diagnostics, user-statement retention, retained inventory, neutral section boundaries, publication grouping, "
          "interview, and offline source setup", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-materials", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="grounded-apply-onboarding-") as directory:
        check_onboarding([sys.executable, "-m", "grounded_apply"], Path(directory).resolve(),
                         source_path=Path(__file__).resolve().parents[1] / "src",
                         include_materials=args.with_materials)
