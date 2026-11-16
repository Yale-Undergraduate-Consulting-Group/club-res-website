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


def _bounded(parts) -> str:
    """Join non-empty text, stopping before an unusual file can fill SQLite."""
    out: list[str] = []
    size = 0
    for value in parts:
        text = str(value or "").strip()
        if not text:
            continue
        out.append(text)
        size += len(text)
        if size >= MAX_CHARS:
            break
    return "\n".join(out)[:MAX_CHARS]


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


def _pptx_text(path: Path) -> str:
    from pptx import Presentation

    presentation = Presentation(str(path))
    return _bounded(
        text
        for slide in presentation.slides
        for shape in slide.shapes
        for text in _shape_text(shape)
    )


def _docx_text(path: Path) -> str:
    from docx import Document

    document = Document(str(path))

    def parts():
        for paragraph in document.paragraphs:
            yield paragraph.text
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    yield cell.text

    return _bounded(parts())


def _pdf_text(path: Path) -> str:
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
            return ""
        return _bounded((page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES]))
    finally:
        logger.setLevel(previous)


def extract_text(path: Path, filename: str) -> str:
    """The searchable text of ``path`` (named ``filename``), or ''."""
    suffix = Path(filename).suffix.lower()
    try:
        if suffix == ".pptx":
            text = _pptx_text(path)
        elif suffix == ".docx":
            text = _docx_text(path)
        elif suffix == ".pdf":
            text = _pdf_text(path)
        elif suffix in {".txt", ".md", ".csv"}:
            text = path.read_bytes()[: MAX_CHARS * 2].decode("utf-8", "ignore")
        else:
            return ""
    except Exception:
        return ""
    return re.sub(r"\s+", " ", text).strip()[:MAX_CHARS]
