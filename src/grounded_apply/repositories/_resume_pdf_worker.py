"""Private subprocess entry point for bounded, quiet PDF intake.

Run by resume_documents with an isolated interpreter and bytes on stdin. Limits
reduce parser resource exposure; they are not an operating-system sandbox.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import warnings

MAX_BYTES = 16 * 1024 * 1024
MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_PAGES = 100


def main() -> int:
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    try:
        import resource
        for kind, value in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, 12),
                            (resource.RLIMIT_AS, 768 * 1024 * 1024)):
            try:
                _, hard = resource.getrlimit(kind)
                cap = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(kind, (cap, cap))
            except (OSError, ValueError):
                # Some platforms refuse address-space limits. The parent timeout
                # and input/page/text/decompression limits still apply.
                pass
    except ImportError:
        pass
    try:
        import pypdf
        from pypdf import PdfReader, filters
    except ImportError:
        return 3
    # pypdf's documented decompression controls apply before stream parsing.
    filters.ZLIB_MAX_OUTPUT_LENGTH = MAX_STREAM_BYTES
    filters.LZW_MAX_OUTPUT_LENGTH = MAX_STREAM_BYTES
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if not raw.startswith(b"%PDF-") or len(raw) > MAX_BYTES:
            return 2
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted:
            return 4
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            return 5
        pages: list[str] = []
        size = 0
        blank_pages = 0
        for page in reader.pages:
            content = page.get_contents()
            if content is not None and len(content.get_data()) > MAX_STREAM_BYTES:
                return 5
            text = page.extract_text() or ""
            size += len(text.encode("utf-8")) + 1
            if size > MAX_BYTES:
                return 5
            if not text.strip():
                blank_pages += 1
            pages.append(text)
        result = {"text": "\n".join(pages), "pages": len(pages), "blank_pages": blank_pages,
                  "version": pypdf.__version__}
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        # Never leak document text, parser warnings, or paths to diagnostics.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
