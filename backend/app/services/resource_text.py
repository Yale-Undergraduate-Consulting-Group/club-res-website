"""Plain text out of club files, for search.

The Office libraries read text runs and tables without loading embedded image
bytes; pypdf reads a bounded number of pages. Extraction never raises: a file
that cannot be read is simply not searchable by its text.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

MAX_CHARS = 300_000
MAX_PDF_PAGES = 400
SECTION_CHARS = 3000


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _bounded(parts) -> str:
    """Join non-empty text, stopping before an unusual file can fill SQLite."""
    out: list[str] = []
    size = 0
    for value in parts:
        text = _clean(value)
        if not text:
            continue
        out.append(text)
        size += len(text)
        if size >= MAX_CHARS:
            break
    return " ".join(out)[:MAX_CHARS]


def _limited(pages) -> list[str]:
    """One cleaned string per page, empty pages kept so numbering holds, stopping
    once MAX_CHARS of text is in."""
    out: list[str] = []
    size = 0
    for page in pages:
        text = _clean(page)[: MAX_CHARS - size]
        out.append(text)
        size += len(text)
        if size >= MAX_CHARS:
            break
    return out


def sections(text: str, size: int = SECTION_CHARS) -> list[str]:
    """``text`` cut into pieces of about ``size`` characters, at spaces."""
    text = _clean(text)[:MAX_CHARS]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        if end < len(text):
            cut = text.rfind(" ", start, end)
            end = cut if cut > start else end
        out.append(text[start:end].strip())
        start = end
    return [piece for piece in out if piece]


def _shape_text(shape):
    if getattr(shape, "has_text_frame", False):
        for paragraph in shape.text_frame.paragraphs:
            yield paragraph.text
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            for cell in row.cells:
                yield cell.text
    # GroupShape exposes its children as ``shapes``; ordinary shapes do not.
    for child in getattr(shape, "shapes", ()):
        yield from _shape_text(child)


def _pptx_pages(path: Path) -> list[str]:
    from pptx import Presentation

    presentation = Presentation(str(path))
    return _limited(
        " ".join(text for shape in slide.shapes for text in _shape_text(shape))
        for slide in presentation.slides
    )


def _docx_sections(path: Path) -> list[str]:
    from docx import Document

    document = Document(str(path))

    def parts():
        for paragraph in document.paragraphs:
            yield paragraph.text
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    yield cell.text

    return sections(_bounded(parts()))


def _pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    # Malformed but readable legacy PDFs can emit one warning per broken
    # object. The importer reports counts only; these internals are not useful
    # operator output and can flood the terminal.
    logger = logging.getLogger("pypdf")
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            return []
        return _limited(page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES])
    finally:
        logger.setLevel(previous)


def extract_pages(path: Path, filename: str) -> list[str]:
    """The searchable text of ``path`` (named ``filename``), one string per slide
    (pptx) or page (pdf), or per section of about SECTION_CHARS (docx, txt, md,
    csv). Slide and page numbers are list positions + 1, so a slide without
    text stays as ''. [] when the file has no readable text."""
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pptx":
            pages = _pptx_pages(path)
        elif suffix == ".docx":
            pages = _docx_sections(path)
        elif suffix == ".pdf":
            pages = _pdf_pages(path)
        elif suffix in {".txt", ".md", ".csv"}:
            pages = sections(path.read_bytes()[: MAX_CHARS * 2].decode("utf-8", "ignore"))
        else:
            return []
    except Exception:
        return []
    return pages if any(pages) else []


def extract_text(path: Path, filename: str) -> str:
    """The searchable text of ``path`` as one string, or ''."""
    return " ".join(page for page in extract_pages(path, filename) if page)[:MAX_CHARS]
