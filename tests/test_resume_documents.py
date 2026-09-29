from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from grounded_apply.repositories.resume_documents import (
    MAX_DOCUMENT_BYTES, ResumeDocumentError, read_resume_document, resume_document_from_bytes,
)


LATEX = r"""\documentclass{article}
\usepackage{hyperref}
\begin{document}
Name: \textbf{Avery Quill}\\
Email: \href{mailto:avery@example.com}{avery@example.com}
\section*{Experience}
Example Robotics LLC --- Research Engineer\\
January 2022--March 2025
\begin{itemize}
\item Contributed \emph{Python} fixtures; did not lead the project.
\item Reduced fictional setup time by 40\% using test\_tools.
\end{itemize}
\section{Publications}
Quill, A. Synthetic Widgets. Under review, 2026.
\end{document}
"""


def synthetic_pdf(text: str = "Name: Avery Quill", *, pages: int = 1) -> bytes:
    """Small conspicuously fictional PDF, without requiring a PDF writer."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = ("BT /F1 12 Tf 72 720 Td (" + escaped + ") Tj ET").encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               ("<< /Type /Pages /Kids [" + " ".join(f"{5 + i} 0 R" for i in range(pages)) + f"] /Count {pages} >>").encode(),
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"]
    objects += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 4 0 R >>"] * pages
    raw = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(raw)
    raw += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    raw += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    return raw + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


class ResumeDocumentTests(unittest.TestCase):
    def test_plain_text_remains_byte_faithful_and_hashes_match(self) -> None:
        raw = "Name: Avery Quill\r\nEducation\r\nExample Institute\r\n".encode()
        document = resume_document_from_bytes(raw, filename="resume.txt")
        self.assertEqual(document.text.encode(), raw)
        self.assertEqual(document.raw_source_sha256, sha256(raw).hexdigest())
        self.assertEqual(document.text_sha256, document.raw_source_sha256)
        self.assertEqual(document.format, "text")
        self.assertEqual(document.issues, ())
        self.assertFalse(document.incomplete)

    def test_plain_text_filename_does_not_interpret_mentioned_tex_commands(self) -> None:
        raw = br"Wrote documentation about \documentclass{article} and TeX."
        self.assertEqual(resume_document_from_bytes(raw, filename="resume.txt").text.encode(), raw)

    def test_latex_local_extraction_preserves_ownership_metrics_status(self) -> None:
        document = resume_document_from_bytes(LATEX.encode(), filename="cv.tex")
        self.assertEqual(document.format, "latex")
        self.assertIn("Name: Avery Quill", document.text)
        self.assertIn("Email: avery@example.com", document.text)
        self.assertIn("Experience\n", document.text)
        self.assertIn("Contributed Python fixtures; did not lead the project.", document.text)
        self.assertIn("40% using test_tools", document.text)
        self.assertIn("Under review, 2026", document.text)
        self.assertNotIn("mailto", document.text)
        self.assertNotIn("documentclass", document.text)
        self.assertNotEqual(document.raw_source_sha256, document.text_sha256)
        self.assertEqual(document.issues, ())

    def test_latex_unknown_macro_discards_whole_affected_paragraph_and_reports_original(self) -> None:
        raw = b"Name: Avery Quill\\par\nExperience\\par\nContributed \\mystery{secret upgrade} Python code.\\par\nBuilt synthetic fixtures.\n"
        document = resume_document_from_bytes(raw, format="latex")
        self.assertNotIn("Contributed", document.text)
        self.assertNotIn("secret", document.text)
        self.assertIn("Built synthetic fixtures.", document.text)
        self.assertEqual(document.issues[0].line, 3)
        self.assertEqual(document.issues[0].end_line, 3)
        self.assertIn("\\mystery{secret upgrade}", document.issues[0].excerpt)
        self.assertTrue(any("incomplete" in warning for warning in document.warnings))
        self.assertTrue(document.incomplete)

    def test_latex_defined_macros_are_not_executed_or_assumed_standard(self) -> None:
        raw = br"""\renewcommand{\textbf}[1]{Led #1}
\newcommand{\resumeItem}[1]{#1}
\begin{document}
Name: Avery Quill
\section{Experience}
\textbf{a project}
\resumeItem{Invented result}
Contributed tests.
\end{document}
"""
        document = resume_document_from_bytes(raw, format="latex")
        self.assertNotIn("a project", document.text)
        self.assertNotIn("Invented", document.text)
        self.assertEqual([issue.code for issue in document.issues], ["latex_custom_macro"] * 2)

    def test_latex_conditionals_math_and_unknown_environments_stay_unresolved(self) -> None:
        raw = br"""Name: Avery Quill\par
Experience\par
\iffalse
Led a private team.
\iftrue Won an invented award.\fi
\else Contributed something.\fi\par
Improved $F_1$ by 40\%.\par
\[
not a career fact
\]\par
\begin{unknown}Secret hidden content.\end{unknown}\par
Built synthetic fixtures.
"""
        document = resume_document_from_bytes(raw, format="latex")
        self.assertEqual(document.text, "Name: Avery Quill\nExperience\nBuilt synthetic fixtures.\n")
        self.assertEqual([issue.code for issue in document.issues],
                         ["latex_conditional", "latex_math", "latex_math", "latex_unknown_environment"])

    def test_latex_does_not_follow_includes_or_execute_shell(self) -> None:
        raw = br"""\input{/private/secret-file}
\begin{document}
Name: Avery Quill
\section{Experience}
\immediate\write18{touch /private/should-not-exist}
\include{private-employment}
Built synthetic fixtures.
\end{document}
"""
        with patch("subprocess.run", side_effect=AssertionError("no process for TeX")), patch("os.open", side_effect=AssertionError("no include reads")):
            document = resume_document_from_bytes(raw, format="latex")
        self.assertNotIn("private-employment", document.text)
        self.assertNotIn("touch", document.text)
        self.assertEqual(document.issues[0].code, "latex_external_or_deferred_content")

    def test_document_boundaries_inside_macro_definitions_are_refused(self) -> None:
        sources = [br"""\newcommand{\fake}{\begin{document}Name: Fictional Wrong\end{document}}
\begin{document}
Name: Avery Quill\par
\section{Experience}
Contributed fictional fixtures.
\end{document}""", br"""\begin{document}
Name: Avery Quill\par
\newcommand{\fake}{\end{document}}
\section{Experience}
Contributed fictional fixtures.
\end{document}"""]
        for raw in sources:
            with self.subTest(raw=raw[:30]), self.assertRaisesRegex(ResumeDocumentError, "nested document"):
                resume_document_from_bytes(raw, format="latex")

    def test_physical_newlines_preserve_qualifiers_and_comments_join_tokens(self) -> None:
        raw = br"""Name: Avery Quill\par
\section{Experience}
Did not
lead fictional tooling.\par
Built 40% This comment does not add a space.
   0 synthetic fixtures.
"""
        document = resume_document_from_bytes(raw, format="latex")
        self.assertEqual(document.text, "Name: Avery Quill\nExperience\nDid not lead fictional tooling.\nBuilt 400 synthetic fixtures.\n")
        self.assertFalse(document.incomplete)

    def test_conditional_fake_document_boundaries_are_refused(self) -> None:
        raw = br"""\iffalse
\begin{document}Name: Fictional Wrong\end{document}
\fi
\begin{document}Name: Avery Quill\end{document}
"""
        with self.assertRaisesRegex(ResumeDocumentError, "unambiguous document"):
            resume_document_from_bytes(raw, format="latex")

    def test_unsupported_multiline_group_withholds_all_ownership_fragments(self) -> None:
        raw = br"""Name: Avery Quill\par
\section{Experience}
Contributed \textbf{
not \unsupported{x}
} led fictional fixtures.\par
Maintained independent fictional tests.
"""
        document = resume_document_from_bytes(raw, format="latex")
        self.assertEqual(document.text, "Name: Avery Quill\nExperience\nMaintained independent fictional tests.\n")
        self.assertTrue(document.incomplete)
        self.assertIn("latex_unsupported_group", [issue.code for issue in document.issues])

    def test_modern_macro_environment_overrides_and_hooks_are_not_silent(self) -> None:
        raw = br"""\RenewDocumentCommand{\textbf}{m}{Led #1}
\renewenvironment{itemize}{Led a team.}{Won an award.}
\AtBeginDocument{\section{Education}Example University degree.}
\begin{document}
Name: Avery Quill\par
\section{Experience}
\textbf{fictional project}\par
\begin{itemize}\item Fictional assignment.\end{itemize}\par
Contributed independent tests.
\end{document}
"""
        document = resume_document_from_bytes(raw, format="latex")
        self.assertTrue(document.incomplete)
        self.assertNotIn("fictional project", document.text)
        self.assertNotIn("Fictional assignment", document.text)
        self.assertIn("Contributed independent tests.", document.text)
        self.assertIn("latex_external_or_deferred_content", [issue.code for issue in document.issues])

    def test_unknown_preamble_controls_cannot_mutate_supported_commands(self) -> None:
        raw = br"\csdef{textbf}[#1]{Led #1}\begin{document}Name: Avery Quill\par\textbf{fictional project}\end{document}"
        with self.assertRaisesRegex(ResumeDocumentError, "preamble"):
            resume_document_from_bytes(raw, format="latex")

    def test_standalone_preamble_groups_cannot_hide_executable_controls(self) -> None:
        for prefix in ("", r"\documentclass{article}", r"\newcommand{\foo}{bar}"):
            for content in (r"\AtBeginDocument{\section{Education}Example University degree.}",
                            r"\csdef{textbf}[#1]{Led #1}"):
                raw = (prefix + "{" + content + "}\\begin{document}Name: Avery Quill\\par\\textbf{fictional project}\\end{document}").encode()
                with self.subTest(prefix=prefix, content=content), self.assertRaisesRegex(ResumeDocumentError, "standalone group"):
                    resume_document_from_bytes(raw, format="latex")

    def test_valid_preamble_declaration_arities_and_options_remain_supported(self) -> None:
        raw = br"""\documentclass[11pt]{article}
\newcommand{\example}[1][default]{#1}
\setlength{\parindent}{0pt}
\newenvironment{example}[1]{#1}{}
\NewDocumentCommand{\other}{m}{#1}
\begin{document}Name: Avery Quill\par\section{Experience}Contributed fictional fixtures.\end{document}
"""
        document = resume_document_from_bytes(raw, format="latex")
        self.assertIn("Contributed fictional fixtures.", document.text)
        self.assertFalse(document.incomplete)

    def test_missing_layout_arguments_and_invalid_starred_commands_are_not_facts(self) -> None:
        for command in (r"\begin{minipage}", r"\vspace", r"\textbf*{fragment}"):
            raw = ("Name: Avery Quill\\par\n" + command + "\nContributed fictional tests.\\par\nIndependent fact.").encode()
            document = resume_document_from_bytes(raw, format="latex")
            self.assertTrue(document.incomplete)
            self.assertNotIn("Contributed", document.text)
            self.assertIn("Independent fact.", document.text)

    def test_color_model_arguments_are_layout_only(self) -> None:
        document = resume_document_from_bytes(br"\color[RGB]{1,0,0}Name: Avery Quill", format="latex")
        self.assertEqual(document.text, "Name: Avery Quill\n")
        self.assertFalse(document.incomplete)

    def test_comments_escaped_symbols_and_accents(self) -> None:
        raw = r"""Name: Ren\'{e} Quill % Not an approved nickname
Experience
Used C\# and R\&D; 50\% fictional improvement.\\% comment after line break
Built fixtures.
""".encode()
        document = resume_document_from_bytes(raw, format="latex")
        self.assertIn("Name: René Quill", document.text)
        self.assertIn("C# and R&D; 50% fictional improvement.", document.text)
        self.assertNotIn("nickname", document.text)
        self.assertNotIn("comment", document.text)
        self.assertEqual(document.issues, ())

    def test_file_identity_does_not_allow_symlinks_or_nonregular_files(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "cv.tex"
            source.write_text(LATEX)
            self.assertEqual(read_resume_document(source).format, "latex")
            link = Path(root) / "link.tex"
            link.symlink_to(source)
            with self.assertRaises(ResumeDocumentError):
                read_resume_document(link)
            fifo = Path(root) / "pipe.tex"
            os.mkfifo(fifo)
            with self.assertRaises(ResumeDocumentError):
                read_resume_document(fifo)
            with self.assertRaises(ResumeDocumentError):
                read_resume_document(Path(root))

    def test_file_change_is_rejected_without_leaking_private_path(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "private-name.txt"
            source.write_text("Name: Avery Quill\n")
            original = os.read
            def mutate(descriptor: int, size: int) -> bytes:
                source.write_text("Name: Rowan Example\n")
                return original(descriptor, size)
            with patch("os.read", side_effect=mutate), self.assertRaises(ResumeDocumentError) as error:
                read_resume_document(source)
            self.assertNotIn("private-name", str(error.exception))
            self.assertNotIn("Rowan", str(error.exception))

    def test_bad_inputs_and_unsupported_formats_fail_closed(self) -> None:
        for raw, format, filename in [(b"", "auto", None), (b"\xff", "text", None),
                                      (b"a\0b", "text", None), (b"word", "auto", "cv.docx"),
                                      (b"word", "pdf", None), (b"word", "wrong", None),
                                      (b"x" * (MAX_DOCUMENT_BYTES + 1), "text", None)]:
            with self.subTest(format=format, filename=filename), self.assertRaises(ResumeDocumentError):
                resume_document_from_bytes(raw, format=format, filename=filename)

    def test_latex_malformed_groups_and_complexity_fail_closed(self) -> None:
        for raw in [b"Name: Avery Quill\n\\textbf{unclosed", b"Name: Avery Quill\nextra}",
                    b"{" * 66 + b"text" + b"}" * 66, b"\n" * 100_002,
                    b"\\catcode`x=1\nName: Avery Quill"]:
            with self.subTest(raw=raw[:25]), self.assertRaises(ResumeDocumentError):
                resume_document_from_bytes(raw, format="latex")

    def test_pdf_timeout_missing_dependency_and_malformed_result_are_fixed_errors(self) -> None:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("private path", 15)):
            with self.assertRaisesRegex(ResumeDocumentError, "time limit"):
                resume_document_from_bytes(b"%PDF-1.4\n")
        for code, stdout, message in [(3, b"", "materials"), (4, b"", "Encrypted"),
                                      (5, b"", "100 pages"), (0, b'{"text":"private"}', "invalid result"),
                                      (2, b"private parser message", "safely read")]:
            with self.subTest(code=code), patch("subprocess.run", return_value=subprocess.CompletedProcess([], code, stdout)):
                with self.assertRaisesRegex(ResumeDocumentError, message) as error:
                    resume_document_from_bytes(b"%PDF-1.4\n")
                self.assertNotIn("private", str(error.exception))

    def test_pdf_worker_receives_bytes_on_stdin_not_in_command_arguments(self) -> None:
        import json
        payload = json.dumps({"text": "Name: Avery Quill", "pages": 1, "blank_pages": 0, "version": "6.10.0"}).encode()
        raw = synthetic_pdf()
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, payload)) as run:
            resume_document_from_bytes(raw, format="pdf")
        arguments, options = run.call_args
        self.assertEqual(arguments[0][1], "-I")
        self.assertNotIn("Avery", repr(arguments))
        self.assertEqual(options["input"], raw)
        self.assertEqual(options["stderr"], subprocess.DEVNULL)
        self.assertEqual(options["timeout"], 15)


@unittest.skipUnless(importlib.util.find_spec("pypdf"), "PDF intake requires the optional materials extra")
class RealResumePdfTests(unittest.TestCase):
    def test_real_pdf_is_extracted_locally_and_identified(self) -> None:
        raw = synthetic_pdf("Name: Avery Quill")
        document = resume_document_from_bytes(raw, filename="resume.pdf")
        self.assertEqual(document.text, "Name: Avery Quill")
        self.assertEqual(document.page_count, 1)
        self.assertEqual(document.raw_source_sha256, sha256(raw).hexdigest())
        self.assertTrue(document.extractor.startswith("grounded-apply.pypdf-text@1/pypdf-"))
        self.assertTrue(document.warnings)
        self.assertFalse(document.incomplete)

    def test_real_blank_and_overlong_pdfs_fail_closed(self) -> None:
        with self.assertRaisesRegex(ResumeDocumentError, "No readable"):
            resume_document_from_bytes(synthetic_pdf(""), format="pdf")
        with self.assertRaisesRegex(ResumeDocumentError, "100 pages"):
            resume_document_from_bytes(synthetic_pdf(pages=101), format="pdf")

    def test_encrypted_pdf_and_malformed_pdf_remain_quiet(self) -> None:
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(synthetic_pdf())))
        writer.encrypt("fictional-password")
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaisesRegex(ResumeDocumentError, "Encrypted"):
            resume_document_from_bytes(output.getvalue())
        with self.assertRaises(ResumeDocumentError):
            resume_document_from_bytes(b"%PDF-1.4\nprivate-injected-error-text")

    def test_mixed_blank_pages_report_incomplete_text(self) -> None:
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(synthetic_pdf())))
        writer.add_blank_page(width=612, height=792)
        output = io.BytesIO()
        writer.write(output)
        document = resume_document_from_bytes(output.getvalue())
        self.assertEqual(document.page_count, 2)
        self.assertTrue(document.incomplete)
        self.assertTrue(any("Some PDF pages" in warning for warning in document.warnings))

    def test_oversized_compressed_content_is_refused(self) -> None:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import DecodedStreamObject, NameObject
        writer = PdfWriter()
        writer.append(PdfReader(io.BytesIO(synthetic_pdf())))
        content = DecodedStreamObject()
        content.set_data(b" " * (4 * 1024 * 1024 + 1))
        writer.pages[0][NameObject("/Contents")] = writer._add_object(content.flate_encode())
        output = io.BytesIO()
        writer.write(output)
        self.assertLess(len(output.getvalue()), 10_000)
        with self.assertRaises(ResumeDocumentError):
            resume_document_from_bytes(output.getvalue())


if __name__ == "__main__":
    unittest.main()
