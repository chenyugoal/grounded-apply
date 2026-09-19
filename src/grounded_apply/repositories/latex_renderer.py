"""Escaped LaTeX rendering in private temporary storage, with PDF text checks."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from grounded_apply.services.material_models import (
    HEADING_TYPES, MaterialValidationError, RenderedResume, ResumeStructure, heading_fields, presented_text,
)

RENDERER = "grounded-apply.latex-resume@2"
_RENDERERS = {"approved_text_selection@1": "grounded-apply.latex-resume@1", "approved_text_selection@2": RENDERER}
_SECTIONS = (
    ("Experience", {"employment_description", "employment_title", "employment_dates", "achievement", "project_outcome"}),
    ("Projects", {"portfolio_item", "project_contribution"}),
    ("Skills", {"skill_use", "language"}),
    ("Education", {"education", "education_degree", "education_field"}),
    ("Certifications", {"certification"}),
    ("Publications", {"publication"}),
)
_ESCAPE = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&",
           "#": r"\#", "%": r"\%", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def escaped(text: str) -> str:
    if any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for c in text):
        raise MaterialValidationError("Resume text contains unsupported control characters")
    # Zero-width explicit kerns prevent font kerning/ligature substitutions from causing PDF
    # readers to invent word boundaries (for example, "A very" for "Avery").
    # The visible characters and whitespace remain exactly the approved text.
    return r"\ ".join(r"\kern0pt{}".join(_ESCAPE.get(c, c) for c in word) for word in text.split(" "))


def _legacy_latex_source(structure: ResumeStructure) -> str:
    names = [u for u in structure.units if u.claim_type == "candidate_name"]
    if len(names) != 1:
        raise MaterialValidationError("Resume requires exactly one approved name")
    contacts = [u for u in structure.units if u.claim_type.startswith("contact_")]
    lines = [r"\documentclass[10pt,letterpaper]{article}", r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}", r"\usepackage{lmodern}",
        r"\usepackage[margin=0.7in]{geometry}", r"\usepackage{enumitem}",
        r"\input{glyphtounicode}\pdfgentounicode=1", r"\pagestyle{empty}",
        r"\setlength{\parindent}{0pt}\setlength{\parskip}{3pt}",
        r"\hyphenpenalty=10000\exhyphenpenalty=10000",
        r"\setlist[itemize]{leftmargin=1.2em,nosep,topsep=3pt}",
        r"\begin{document}", r"{\LARGE\bfseries " + escaped(names[0].text) + r"}\par\vspace{5pt}",
        r"{\small " + r" \enspace | \enspace ".join(escaped(u.text) for u in contacts) + r"}\par",
        r"\vspace{7pt}\hrule\vspace{5pt}"]
    for title, types in _SECTIONS:
        units = [u for u in structure.units if u.claim_type in types]
        if not units:
            continue
        lines.append(r"\vspace{6pt}{\large\bfseries " + title + r"}\par")
        in_list = False
        for unit in units:
            if unit.presentation not in {"bullet", "paragraph"}:
                raise MaterialValidationError("Unsupported factual presentation")
            if unit.presentation == "bullet":
                if not in_list:
                    lines.append(r"\begin{itemize}")
                    in_list = True
                lines.append(r"\item " + escaped(unit.text))
            else:
                if in_list:
                    lines.append(r"\end{itemize}")
                    in_list = False
                lines.append(escaped(unit.text) + r"\par")
        if in_list:
            lines.append(r"\end{itemize}")
    known = {"candidate_name"} | {u.claim_type for u in contacts} | set().union(*(types for _, types in _SECTIONS))
    if any(u.claim_type not in known for u in structure.units):
        raise MaterialValidationError("Resume contains an unsupported factual section")
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def _emphasized(text: str) -> str:
    label, colon, rest = text.partition(": ")
    if colon and 1 <= len(label) <= 65:
        return r"\textbf{" + escaped(label + ": ") + "}" + escaped(rest)
    return escaped(text)


def latex_source(structure: ResumeStructure) -> str:
    if structure.transformation == "approved_text_selection@1":
        return _legacy_latex_source(structure)
    if structure.transformation not in _RENDERERS:
        raise MaterialValidationError("Unsupported material transformation")
    names = [u for u in structure.units if u.claim_type == "candidate_name"]
    contacts = [u for u in structure.units if u.claim_type.startswith("contact_")]
    if len(names) != 1:
        raise MaterialValidationError("Resume requires exactly one approved name")
    lines = [r"\documentclass[11pt,letterpaper]{article}", r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}", r"\usepackage{lmodern}",
        r"\renewcommand{\familydefault}{\sfdefault}", r"\usepackage[margin=0.55in]{geometry}",
        r"\usepackage{enumitem}", r"\usepackage{needspace}",
        r"\input{glyphtounicode}\pdfgentounicode=1\pdfinterwordspaceon", r"\pagestyle{empty}",
        r"\setlength{\parindent}{0pt}\setlength{\parskip}{2pt}",
        r"\raggedright\hyphenpenalty=10000\exhyphenpenalty=10000",
        r"\setlength{\emergencystretch}{2em}",
        r"\setlist[itemize]{leftmargin=1.3em,labelsep=0.5em,itemsep=2pt,parsep=0pt,topsep=3pt,partopsep=0pt}",
        r"\begin{document}", r"{\centering{\fontsize{22}{25}\selectfont\bfseries " + escaped(names[0].text) + r"}\par",
        r"\vspace{3pt}{\small " + r" \enspace | \enspace ".join(escaped(u.text) for u in contacts) + r"}\par}"]
    for title, types in _SECTIONS:
        units = [u for u in structure.units if u.claim_type in types]
        if not units:
            continue
        lines.append(r"\par\Needspace{7\baselineskip}\vspace{9pt}{\large\bfseries " + title + r"}\par\nobreak\vspace{1pt}\hrule height 0.4pt\nobreak\vspace{5pt}")
        in_list = False
        for index, unit in enumerate(units):
            if unit.presentation not in {"bullet", "paragraph", "heading"}:
                raise MaterialValidationError("Unsupported factual presentation")
            if unit.presentation == "bullet":
                if not in_list:
                    lines.append(r"\begin{itemize}")
                    in_list = True
                lines.append(r"\item " + _emphasized(unit.text))
                continue
            if in_list:
                lines.append(r"\end{itemize}")
                in_list = False
            if unit.presentation == "heading":
                if unit.claim_type not in HEADING_TYPES:
                    raise MaterialValidationError("Unsupported heading claim type")
                fields = heading_fields(unit)
                lines.append(r"\par\Needspace{5\baselineskip}" + (r"\vspace{5pt}" if index else ""))
                if len(fields) == 4:
                    for left, right, style in ((fields[0], fields[1], r"\bfseries"), (fields[2], fields[3], r"\itshape")):
                        lines.append(r"\noindent\parbox[t]{0.65\linewidth}{\raggedright " + style + " " + escaped(left + " ")
                            + r"\strut}\hfill\parbox[t]{0.33\linewidth}{\raggedleft " + (r"\itshape " if style == r"\itshape" else "")
                            + escaped(right) + r"\strut}\par\nobreak")
                else:
                    lines.append(r"{\bfseries " + escaped(unit.text) + r"}\par\nobreak")
            else:
                lines.append(_emphasized(unit.text) + r"\par")
        if in_list:
            lines.append(r"\end{itemize}")
    known = {"candidate_name"} | {u.claim_type for u in contacts} | set().union(*(types for _, types in _SECTIONS))
    if any(u.claim_type not in known for u in structure.units):
        raise MaterialValidationError("Resume contains an unsupported factual section")
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


def normalized(text: str) -> str:
    # Whitespace and Unicode presentation ligatures may differ after PDF text
    # extraction. No alphanumeric character, ownership word, or metric is dropped.
    return " ".join(unicodedata.normalize("NFKC", text).split()).replace("• ", "•")


def expected_text(structure: ResumeStructure) -> str:
    lines = [next(u.text for u in structure.units if u.claim_type == "candidate_name"),
        " | ".join(u.text for u in structure.units if u.claim_type.startswith("contact_"))]
    for title, types in _SECTIONS:
        units = [u for u in structure.units if u.claim_type in types]
        if units:
            lines.append(title)
            lines.extend(("•" if u.presentation == "bullet" else "") + presented_text(structure, u) for u in units)
    return "\n".join(lines)


class LatexResumeRenderer:
    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or shutil.which("pdflatex")

    def _extract(self, pdf: bytes) -> tuple[str, int]:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise MaterialValidationError("PDF verification requires the optional grounded-apply[materials] dependency") from None
        if not pdf.startswith(b"%PDF-") or len(pdf) > 2 * 1024 * 1024:
            raise MaterialValidationError("PDF is invalid or exceeds the size limit")
        try:
            reader = PdfReader(io.BytesIO(pdf), strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= 2:
                raise ValueError
            return "\n".join(page.extract_text() or "" for page in reader.pages), len(reader.pages)
        except Exception:
            raise MaterialValidationError("PDF extraction failed or resume exceeds two pages") from None

    def render(self, structure: ResumeStructure) -> RenderedResume:
        if self._executable is None:
            raise MaterialValidationError("PDF generation requires a local pdflatex installation")
        try:
            import pypdf
        except ImportError:
            raise MaterialValidationError("PDF verification requires the optional grounded-apply[materials] dependency") from None
        latex = latex_source(structure)
        # Source/PDF are private build artifacts. Compiler logging is discarded;
        # its bounded-lifetime private stdout is inspected only in memory.
        with tempfile.TemporaryDirectory(prefix="gapply-resume-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            source = root / "resume.tex"
            fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(latex)
            (root / "resume.log").symlink_to(os.devnull)
            env = {"PATH": os.path.dirname(self._executable) + os.pathsep + "/usr/bin:/bin",
                   "TEXMFHOME": str(root), "TEXMFVAR": str(root), "TEXMFCONFIG": str(root),
                   "TMPDIR": str(root), "openin_any": "p", "openout_any": "p",
                   "shell_escape": "f", "SOURCE_DATE_EPOCH": "1788480000", "FORCE_SOURCE_DATE": "1"}
            try:
                process = subprocess.run([self._executable, "-no-shell-escape", "-halt-on-error",
                    "-interaction=nonstopmode", "-jobname=resume", "resume.tex"], cwd=root, env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
                if process.returncode != 0:
                    raise MaterialValidationError("LaTeX could not render the selected text; check unsupported characters or missing TeX packages")
                if len(process.stdout) > 4 * 1024 * 1024:
                    raise MaterialValidationError("Compiler output exceeded its supported limit")
                if b"Overfull \\hbox" in process.stdout or b"Overfull \\vbox" in process.stdout:
                    raise MaterialValidationError("Resume layout overflows; shorten the selected content")
                pdf = (root / "resume.pdf").read_bytes()
            except (OSError, subprocess.TimeoutExpired):
                raise MaterialValidationError("Local PDF rendering failed or exceeded its time limit") from None
        text, pages = self._extract(pdf)
        rendered = RenderedResume(pdf, latex, text, pages, _RENDERERS[structure.transformation])
        self.validate(structure, rendered)
        return rendered

    def validate(self, structure: ResumeStructure, rendered: RenderedResume) -> None:
        if rendered.renderer != _RENDERERS.get(structure.transformation) or rendered.latex != latex_source(structure):
            raise MaterialValidationError("Resume template or LaTeX does not match approved structure")
        text, pages = self._extract(rendered.pdf)
        if text != rendered.extracted_text or pages != rendered.page_count:
            raise MaterialValidationError("PDF text or page count differs from validation")
        extracted = normalized(text)
        for unit in structure.units:
            if normalized(presented_text(structure, unit)) not in extracted:
                raise MaterialValidationError("PDF is missing a critical factual unit or contains unsupported glyphs")
        if extracted != normalized(expected_text(structure)):
            raise MaterialValidationError("PDF contains text outside the approved factual units and fixed headings")
