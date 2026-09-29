"""Local document-to-text adapters; extracted text is untrusted review material.

LaTeX is statically read, never compiled. PDF parsing runs in a short-lived
process. Neither adapter follows document links, reads includes, or uses a model.
This is extraction, not proof that the text matches a rendered document.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_PDF_PAGES = 100
PDF_TIMEOUT_SECONDS = 15


class ResumeDocumentError(ValueError):
    """A fixed, content-free document intake failure."""


@dataclass(frozen=True, slots=True)
class DocumentIssue:
    code: str
    line: int
    end_line: int
    excerpt: str
    message: str


@dataclass(frozen=True, slots=True)
class ResumeDocument:
    text: str
    format: str
    raw_source_sha256: str
    text_sha256: str
    extractor: str
    warnings: tuple[str, ...] = ()
    issues: tuple[DocumentIssue, ...] = ()
    page_count: int | None = None
    incomplete: bool = False


def _read_document_bytes(path: Path) -> bytes:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ResumeDocumentError("Resume input must be a direct regular file")
        nofollow, nonblock = getattr(os, "O_NOFOLLOW", 0), getattr(os, "O_NONBLOCK", 0)
        if not nofollow or not nonblock:
            raise ResumeDocumentError("Secure resume document capture is unavailable on this platform")
        descriptor = os.open(path, os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0))
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ResumeDocumentError("Resume document changed before it could be read")
            if not 1 <= opened.st_size <= MAX_DOCUMENT_BYTES:
                raise ResumeDocumentError("Resume document must be nonempty and at most 16 MiB")
            chunks: list[bytes] = []
            remaining = MAX_DOCUMENT_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            final = os.fstat(descriptor)
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if identity(opened) != identity(final) or len(raw) != final.st_size:
                raise ResumeDocumentError("Resume document changed while it was being read")
            return raw
        finally:
            os.close(descriptor)
    except OSError:
        raise ResumeDocumentError("Could not read the local resume document") from None


def read_resume_document(path: str | Path, *, format: str = "auto") -> ResumeDocument:
    """Read one bounded local file; format can be auto, text, latex, or pdf."""
    location = Path(path)
    return resume_document_from_bytes(_read_document_bytes(location), format=format, filename=location.name)


def resume_document_from_bytes(raw: bytes, *, format: str = "auto", filename: str | None = None) -> ResumeDocument:
    """Decode bounded bytes in memory without creating a source-text export."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_DOCUMENT_BYTES:
        raise ResumeDocumentError("Resume document must be nonempty and at most 16 MiB")
    if format not in {"auto", "text", "latex", "pdf"}:
        raise ResumeDocumentError("Resume format must be auto, text, latex, or pdf")
    suffix = Path(filename).suffix.casefold() if filename else ""
    selected = format
    if selected == "auto":
        if raw.startswith(b"%PDF-") or suffix == ".pdf":
            selected = "pdf"
        elif suffix in {".txt", ".text", ".md"}:
            selected = "text"
        elif suffix in {".tex", ".latex"} or re.search(rb"\\(?:documentclass|begin\s*\{document\})", raw):
            selected = "latex"
        elif suffix in {".doc", ".docx", ".rtf", ".odt"}:
            raise ResumeDocumentError("This resume format is not supported yet; use PDF, LaTeX, or UTF-8 text")
        else:
            selected = "text"
    warnings: tuple[str, ...] = ()
    issues: tuple[DocumentIssue, ...] = ()
    pages: int | None = None
    incomplete = False
    if selected == "pdf":
        text, pages, blank_pages, method = _extract_pdf(raw)
        warnings = ("PDF text order, glyphs, and completeness require comparison with the original document; no OCR is performed.",)
        if blank_pages:
            incomplete = True
            warnings += ("Some PDF pages contain no extractable text; those pages need separate review or OCR.",)
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ResumeDocumentError("Text and LaTeX resumes must use UTF-8 encoding") from None
        if "\x00" in text:
            raise ResumeDocumentError("Resume text contains unsupported null characters")
        if selected == "latex":
            text, issues = _LatexReader(text).read()
            method = "grounded-apply.static-latex-text@1"
            warnings = ("LaTeX was read statically without compilation, macro expansion, or included files; compare extracted facts with the original.",)
            if issues:
                incomplete = True
                warnings += ("Unsupported LaTeX content is listed separately and excluded from extracted text; the inventory is incomplete until reviewed.",)
        else:
            method = "grounded-apply.utf8-text@1"
    if not text.strip():
        raise ResumeDocumentError("No readable resume text was extracted; use a selectable-text PDF or review the source manually")
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise ResumeDocumentError("Extracted resume text exceeds the 16 MiB limit")
    return ResumeDocument(text, selected, sha256(raw).hexdigest(), sha256(text.encode("utf-8")).hexdigest(),
                          method, warnings, issues, pages, incomplete)


def _extract_pdf(raw: bytes) -> tuple[str, int, int, str]:
    if not raw.startswith(b"%PDF-"):
        raise ResumeDocumentError("Resume input is not a supported PDF document")
    worker = Path(__file__).with_name("_resume_pdf_worker.py")
    try:
        process = subprocess.run([sys.executable, "-I", str(worker)], input=raw, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=PDF_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ResumeDocumentError("Local PDF extraction failed or exceeded its time limit") from None
    if process.returncode == 3:
        raise ResumeDocumentError("PDF intake requires the optional grounded-apply[materials] dependency")
    if process.returncode == 4:
        raise ResumeDocumentError("Encrypted PDFs are not supported; provide an unencrypted copy you are authorized to use")
    if process.returncode == 5:
        raise ResumeDocumentError("PDF intake supports at most 100 pages and bounded text streams")
    if process.returncode or len(process.stdout) > MAX_DOCUMENT_BYTES * 6 + 1024:
        raise ResumeDocumentError("Local PDF extraction could not safely read this document")
    try:
        data = json.loads(process.stdout)
        if type(data) is not dict or set(data) != {"text", "pages", "blank_pages", "version"}:
            raise ValueError
        text, pages, blank_pages, version = data["text"], data["pages"], data["blank_pages"], data["version"]
        if (type(text) is not str or type(pages) is not int or not 1 <= pages <= MAX_PDF_PAGES
                or type(blank_pages) is not int or not 0 <= blank_pages <= pages
                or type(version) is not str or re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version) is None
                or len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES or "\x00" in text):
            raise ValueError
        return text, pages, blank_pages, "grounded-apply.pypdf-text@1/pypdf-" + version
    except (ValueError, TypeError, UnicodeError):
        raise ResumeDocumentError("Local PDF extraction returned an invalid result") from None


_WRAPPERS = {"textbf", "textit", "emph", "underline", "textrm", "textsf", "texttt", "textnormal", "textsc", "mbox"}
_STYLES = {"bfseries", "itshape", "scshape", "mdseries", "normalfont", "rmfamily", "sffamily", "ttfamily", "small", "footnotesize", "scriptsize", "tiny", "normalsize", "large", "Large", "LARGE", "huge", "Huge", "centering", "raggedright", "raggedleft", "noindent", "hfill", "strut", "selectfont", "nobreak"}
_SPACE = {"quad", "qquad", "enspace", "enskip", "space", "thinspace", "medspace", "thickspace"}
_LAYOUT = {"vspace": 1, "hspace": 1, "fontsize": 2, "setlength": 2, "addtolength": 2, "color": 1, "pagecolor": 1, "vphantom": 1, "hphantom": 1, "phantom": 1}
_HEADINGS = {"section", "subsection", "subsubsection", "chapter", "paragraph"}
_ENVIRONMENTS = {"document", "itemize", "enumerate", "description", "center", "flushleft", "flushright", "tabular", "tabular*", "tabularx", "minipage", "multicols"}
_SYMBOLS = {"LaTeX": "LaTeX", "TeX": "TeX", "textbackslash": "\\", "textasciitilde": "~", "textasciicircum": "^", "textbullet": "•", "textendash": "–", "textemdash": "—", "ldots": "…", "dots": "…", "%": "%", "&": "&", "_": "_", "#": "#", "$": "$", "{": "{", "}": "}", " ": " ", ",": " ", ";": " ", ":": " ", "!": ""}
_ACCENTS = {"'": "\u0301", "`": "\u0300", '"': "\u0308", "^": "\u0302", "~": "\u0303", "=": "\u0304", ".": "\u0307", "c": "\u0327", "v": "\u030c", "u": "\u0306", "H": "\u030b"}
_COMMAND = re.compile(r"\\([A-Za-z@]+|.)", re.S)
_LITERAL = re.compile(r"[^\\{}$^_~&\n\x00]+")
_PREAMBLE = {"documentclass", "usepackage", "RequirePackage", "PassOptionsToPackage", "PassOptionsToClass",
             "geometry", "pagestyle", "thispagestyle", "setlength", "addtolength", "setcounter", "addtocounter",
             "hypersetup", "urlstyle", "setlist", "setlistdepth", "newlength", "newcommand", "renewcommand",
             "providecommand", "DeclareRobustCommand", "newenvironment", "renewenvironment", "begin",
             "pdfgentounicode", "pdfinterwordspaceon", "hyphenpenalty", "exhyphenpenalty"}
_PREAMBLE.update(prefix + middle + "DocumentCommand"
                 for prefix in ("New", "Renew", "Provide", "Declare") for middle in ("", "Expandable"))
_PREAMBLE.update(prefix + "DocumentEnvironment" for prefix in ("New", "Renew", "Provide", "Declare"))
_DEFERRED_CONTENT = {"input", "include", "bibliography", "addbibresource", "AtBeginDocument", "AtEndDocument",
                     "AddToHook", "AddToHookNext", "title", "author", "date"}
_PREAMBLE_ARGUMENTS = dict.fromkeys(_PREAMBLE | _DEFERRED_CONTENT, 1)
_PREAMBLE_ARGUMENTS.update(dict.fromkeys({"PassOptionsToPackage", "PassOptionsToClass", "setlength", "addtolength",
    "setcounter", "addtocounter", "newcommand", "renewcommand", "providecommand", "DeclareRobustCommand",
    "AddToHook", "AddToHookNext"}, 2))
_PREAMBLE_ARGUMENTS.update(dict.fromkeys({"newenvironment", "renewenvironment"}, 3))
_PREAMBLE_ARGUMENTS.update({command: 3 for command in _PREAMBLE if command.endswith("DocumentCommand")})
_PREAMBLE_ARGUMENTS.update({command: 4 for command in _PREAMBLE if command.endswith("DocumentEnvironment")})
_PREAMBLE_ARGUMENTS.update(dict.fromkeys(_STYLES | {"pdfgentounicode", "pdfinterwordspaceon", "hyphenpenalty", "exhyphenpenalty"}, 0))


class _LatexReader:
    def __init__(self, source: str) -> None:
        self.original = source
        # Inert padding preserves source positions. A TeX comment removes its
        # terminating newline too; retaining a space could change 40%\n0 to 40 0.
        if source.count("\n") > 100_000:
            raise ResumeDocumentError("LaTeX line count exceeds the supported limit; use its PDF")
        uncommented: list[str] = []
        for line in source.splitlines(keepends=True):
            leading = len(line) - len(line.lstrip(" \t"))
            line = "\x00" * leading + line[leading:]
            for percent in re.finditer("%", line):
                preceding = percent.start() - 1
                while preceding >= 0 and line[preceding] == "\\":
                    preceding -= 1
                if (percent.start() - preceding - 1) % 2 == 0:
                    line = line[:percent.start()] + "\x00" * (len(line) - percent.start())
                    break
            uncommented.append(line)
        self.source = "".join(uncommented)
        if re.search(r"\\(?:catcode|endlinechar|escapechar)\b", self.source):
            raise ResumeDocumentError("LaTeX character-code changes cannot be read statically; use its PDF")
        self.line_starts = [0] + [match.end() for match in re.finditer("\n", source)]
        self.issues: list[DocumentIssue] = []
        self.parts: list[tuple[str, int]] = []
        self.commands = 0
        declarations = self.source.replace("\x00", " ")
        self.overridden = set(re.findall(r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand|(?:New|Renew|Provide|Declare)(?:Expandable)?DocumentCommand)\*?\s*\{?\s*\\([A-Za-z@]+)", declarations))
        self.overridden.update(re.findall(r"\\(?:def|gdef|edef|xdef|let)\s*\\([A-Za-z@]+)", declarations))
        self.overridden_environments = set(re.findall(r"\\(?:newenvironment|renewenvironment|(?:New|Renew|Provide|Declare)DocumentEnvironment)\*?\s*\{([^{}]+)\}", declarations))

    def _line(self, position: int) -> int:
        return bisect.bisect_right(self.line_starts, position)

    def _issue(self, code: str, start: int, end: int, *, taint: bool = True) -> None:
        first, last = self._line(start), self._line(max(start, end - 1))
        excerpt = self.original[self.line_starts[first - 1]:self.line_starts[last] if last < len(self.line_starts) else len(self.original)]
        self.issues.append(DocumentIssue(code, first, last, excerpt[:2000],
            "This source content was not interpreted; review it in the original document or provide its explicit text."))
        if taint:
            # Keep a marker even when an unsupported token has no visible text.
            # It prevents surrounding fragments becoming an altered fact.
            self.parts.append(("", first))
        if len(self.issues) > 1000:
            raise ResumeDocumentError("LaTeX intake exceeds the supported unresolved-content limit; use its PDF")

    def _emit(self, text: str, position: int) -> None:
        self.parts.append((text, self._line(position)))

    def _group(self, position: int, opening: str = "{") -> tuple[int, int, int] | None:
        while position < len(self.source) and (self.source[position].isspace() or self.source[position] == "\x00"):
            position += 1
        if position >= len(self.source) or self.source[position] != opening:
            return None
        closing = "}" if opening == "{" else "]"
        start, depth, position = position + 1, 1, position + 1
        while position < len(self.source):
            if self.source[position] == "\\":
                position += 2
                continue
            if self.source[position] == opening:
                depth += 1
                if depth > 64:
                    raise ResumeDocumentError("LaTeX grouping exceeds the supported depth; use its PDF")
            elif self.source[position] == closing:
                depth -= 1
                if depth == 0:
                    return start, position, position + 1
            position += 1
        raise ResumeDocumentError("LaTeX contains an unclosed group; use its PDF or repair the source")

    def _arguments_end(self, position: int) -> int:
        for _ in range(16):
            group = self._group(position, "[") or self._group(position)
            if group is None:
                return position
            position = group[2]
        raise ResumeDocumentError("LaTeX command exceeds the supported argument limit; use its PDF")

    def _parse_group(self, group: tuple[int, int, int], depth: int) -> None:
        issue_count = len(self.issues)
        self._parse(group[0], group[1], depth + 1)
        if len(self.issues) != issue_count:
            self._issue("latex_unsupported_group", group[0] - 1, group[2])

    def _parse(self, start: int, end: int, depth: int = 0) -> None:
        if depth > 64:
            raise ResumeDocumentError("LaTeX nesting exceeds the supported depth; use its PDF")
        position = start
        while position < end:
            character = self.source[position]
            if character == "\\":
                command_start = position
                match = _COMMAND.match(self.source, position)
                if match is None:
                    self._issue("latex_unresolved_command", position, position + 1)
                    break
                command = match[1]
                position += len(match[0])
                if (position < end and self.source[position] == "*"
                        and command in _HEADINGS | {"\\", "hspace", "vspace"}):
                    position += 1
                self.commands += 1
                if self.commands > 100_000:
                    raise ResumeDocumentError("LaTeX command count exceeds the supported limit; use its PDF")
                if command in {"(", "["}:
                    delimiter = "\\)" if command == "(" else "\\]"
                    closing = self.source.find(delimiter, position, end)
                    position = closing + 2 if closing >= 0 else end
                    self._issue("latex_math", command_start, position)
                elif command.startswith("if"):
                    nesting = 1
                    for token in re.finditer(r"\\([A-Za-z@]+)", self.source[position:end]):
                        if token[1].startswith("if"):
                            nesting += 1
                        elif token[1] == "fi":
                            nesting -= 1
                            if nesting == 0:
                                position += token.end()
                                break
                    else:
                        position = end
                    self._issue("latex_conditional", command_start, position)
                elif command in self.overridden:
                    position = self._arguments_end(position)
                    self._issue("latex_custom_macro", command_start, position)
                elif command in _WRAPPERS | _HEADINGS or command in {"href", "url", "textcolor"}:
                    if command in _HEADINGS:
                        option = self._group(position, "[")
                        if option:
                            position = option[2]
                        self._emit("\n", command_start)
                    if command in {"href", "textcolor"}:
                        hidden = self._group(position)
                        if hidden:
                            position = hidden[2]
                    group = self._group(position)
                    if group is None:
                        position = self._arguments_end(position)
                        self._issue("latex_missing_argument", command_start, position)
                    else:
                        self._parse_group(group, depth)
                        position = group[2]
                    if command in _HEADINGS:
                        self._emit("\n", command_start)
                elif command in {"begin", "end"}:
                    group = self._group(position)
                    if group is None:
                        self._issue("latex_missing_environment", command_start, position)
                        continue
                    environment, position = self.source[group[0]:group[1]], group[2]
                    if environment not in _ENVIRONMENTS or environment in self.overridden_environments:
                        closing = re.search(r"\\end\s*\{" + re.escape(environment) + r"\}", self.source[position:end])
                        if command == "begin":
                            position = position + closing.end() if closing else end
                        self._issue("latex_unknown_environment", command_start, position)
                    else:
                        self._emit("\n", command_start)
                        if command == "begin":
                            option = self._group(position, "[")
                            if option:
                                position = option[2]
                            count = 2 if environment in {"tabular*", "tabularx"} else int(environment in {"tabular", "minipage", "multicols"})
                            for _ in range(count):
                                argument = self._group(position)
                                if argument:
                                    position = argument[2]
                                else:
                                    self._issue("latex_missing_argument", command_start, position)
                elif command in _STYLES:
                    pass
                elif command in _SPACE:
                    self._emit(" ", command_start)
                elif command in _SYMBOLS:
                    self._emit(_SYMBOLS[command], command_start)
                elif command in _ACCENTS:
                    group = self._group(position)
                    if group:
                        letter, position = self.source[group[0]:group[1]], group[2]
                    else:
                        letter, position = self.source[position:position + 1], position + 1
                    if len(letter) == 1 and letter.isalpha():
                        self._emit(unicodedata.normalize("NFC", letter + _ACCENTS[command]), command_start)
                    else:
                        self._issue("latex_unresolved_accent", command_start, position)
                elif command in {"\\", "par", "newline", "linebreak", "newpage", "clearpage", "item"}:
                    self._emit("\n" if command != "item" else "\n- ", command_start)
                    option = self._group(position, "[")
                    if option:
                        if command == "item":
                            self._parse(option[0], option[1], depth + 1)
                            self._emit(" ", command_start)
                        position = option[2]
                elif command in _LAYOUT:
                    option = self._group(position, "[")
                    if option:
                        position = option[2]
                    for _ in range(_LAYOUT[command]):
                        group = self._group(position)
                        if group:
                            position = group[2]
                        else:
                            self._issue("latex_missing_argument", command_start, position)
                else:
                    position = self._arguments_end(position)
                    self._issue("latex_unresolved_command", command_start, position)
            elif character == "{":
                group = self._group(position)
                assert group is not None
                self._parse_group(group, depth)
                position = group[2]
            elif character == "\x00":
                position += 1
            elif character == "\n":
                whitespace = re.match(r"[\s\x00]*", self.source[position:end])
                assert whitespace is not None
                value = whitespace[0]
                paragraphs = value.count("\n") > 1
                if paragraphs and depth:
                    raise ResumeDocumentError("LaTeX grouped paragraphs require review in the PDF")
                self._emit("\n" if paragraphs else " ", position)
                position += len(value)
            elif character in {"$", "^", "_"}:
                # Mathematical notation is never guessed into an ordinary fact.
                if character == "$":
                    marker = "$$" if self.source.startswith("$$", position) else "$"
                    closing = self.source.find(marker, position + len(marker), end)
                    next_position = closing + len(marker) if closing >= 0 else end
                else:
                    next_position = position + 1
                self._issue("latex_math", position, next_position)
                position = next_position
            elif character == "}":
                raise ResumeDocumentError("LaTeX contains an unmatched group; use its PDF or repair the source")
            else:
                literal = _LITERAL.match(self.source, position, end)
                if literal:
                    self._emit(literal[0], position)
                    position = literal.end()
                else:
                    self._emit(" " if character == "~" else " | " if character == "&" else character, position)
                    position += 1

    def _document_bounds(self) -> tuple[int, int]:
        # Delimiters inside macro definitions/groups are not document boundaries.
        # Refuse these ambiguous sources rather than executing TeX to interpret them.
        position, depth = 0, 0
        boundaries: list[tuple[str, int, int]] = []
        while position < len(self.source):
            match = _COMMAND.match(self.source, position)
            if match:
                if match[1] in {"begin", "end"}:
                    group = self._group(match.end())
                    if group and self.source[group[0]:group[1]] == "document":
                        if depth:
                            raise ResumeDocumentError("LaTeX contains a nested document boundary; use its PDF")
                        boundaries.append((match[1], position, group[2]))
                        position = group[2]
                        continue
                position = match.end()
            else:
                if self.source[position] == "{":
                    depth += 1
                elif self.source[position] == "}":
                    depth -= 1
                    if depth < 0:
                        raise ResumeDocumentError("LaTeX contains an unmatched group; use its PDF or repair the source")
                position += 1
        if depth:
            raise ResumeDocumentError("LaTeX contains an unclosed group; use its PDF or repair the source")
        if not boundaries:
            return 0, len(self.source)
        if len(boundaries) != 2 or boundaries[0][0] != "begin" or boundaries[1][0] != "end":
            raise ResumeDocumentError("LaTeX requires one unambiguous document environment; use its PDF")
        return boundaries[0][2], boundaries[1][1]

    def _check_preamble(self, end: int) -> None:
        position = 0
        while position < end:
            match = _COMMAND.match(self.source, position)
            if match:
                command = match[1]
                after = match.end()
                if after < end and self.source[after] == "*":
                    if command not in {"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand", "newenvironment", "renewenvironment"}:
                        raise ResumeDocumentError("LaTeX preamble contains an unsupported starred command; use its PDF")
                    after += 1
                if command in _DEFERRED_CONTENT:
                    position = self._preamble_arguments(command, after)
                    self._issue("latex_external_or_deferred_content", match.start(), position, taint=False)
                elif command in _PREAMBLE | _STYLES:
                    position = self._preamble_arguments(command, after)
                else:
                    raise ResumeDocumentError("LaTeX preamble contains unsupported commands; use its PDF")
            elif self.source[position] == "{":
                # Bare groups execute their contents in TeX. Only arguments
                # consumed by the known declarations above may remain inert.
                raise ResumeDocumentError("LaTeX preamble contains an unsupported standalone group; use its PDF")
            else:
                position += 1

    def _preamble_arguments(self, command: str, position: int) -> int:
        # Do not consume extra groups as arguments: they execute in TeX.
        for _ in range(_PREAMBLE_ARGUMENTS[command]):
            for _ in range(2):
                option = self._group(position, "[")
                if option is None:
                    break
                position = option[2]
            argument = self._group(position)
            if argument is None:
                raise ResumeDocumentError("LaTeX preamble command lacks a supported explicit argument; use its PDF")
            position = argument[2]
        return position

    def read(self) -> tuple[str, tuple[DocumentIssue, ...]]:
        start, end = self._document_bounds()
        if start:
            self._check_preamble(start)
        self._parse(start, end)
        blocked = set()
        for issue in self.issues:
            blocked.update(range(issue.line, issue.end_line + 1))
        lines: list[str] = []
        fragments: list[str] = []
        sources: set[int] = set()
        # Drop an entire affected output line, rather than joining fragments
        # around unsupported content into a plausible but incomplete fact.
        for value, source_line in self.parts + [("\n", 0)]:
            for index, piece in enumerate(value.split("\n")):
                if index:
                    if not sources.intersection(blocked):
                        text = re.sub(r"[ \t\r]+", " ", "".join(fragments)).strip()
                        if text:
                            lines.append(text)
                    fragments, sources = [], set()
                if piece:
                    fragments.append(piece)
                    if piece.strip():
                        sources.add(source_line)
                elif len(value.split("\n")) == 1:
                    sources.add(source_line)
        return "\n".join(lines) + ("\n" if lines else ""), tuple(self.issues)
