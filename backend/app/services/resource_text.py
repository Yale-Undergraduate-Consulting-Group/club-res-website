"""Plain text out of club files, for search.

Decks are read straight from their slide XML, never through python-pptx, which
loads every embedded image (a 685 MB deck needed 700 MB of memory). pypdf reads
a bounded number of pages. Extraction never raises: a file that cannot be read
is simply not searchable by its text.
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


_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
# One slide's XML is a few hundred KB at most; a larger part is not a real slide.
MAX_SLIDE_XML = 20 * 1024 * 1024


def _slide_parts(archive) -> list[str]:
    """Slide part names in presentation order (presentation.xml's slide list)."""
    from xml.etree import ElementTree

    rels = ElementTree.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    targets = {rel.get("Id"): rel.get("Target", "") for rel in rels.iter(f"{_REL}Relationship")}
    presentation = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
    parts = []
    for slide in presentation.iter(f"{_P}sldId"):
        target = targets.get(slide.get(f"{_R}id"), "")
        if target:
            parts.append(target.lstrip("/") if target.startswith("/") else f"ppt/{target}")
    return parts


def _slide_text(xml: bytes) -> str:
    """Every paragraph of a slide (text boxes, tables, groups), in document order;
    a line break inside a paragraph separates words as python-pptx's text did."""
    from xml.etree import ElementTree

    root = ElementTree.fromstring(xml)
    return " ".join(
        "".join((node.text or "") if node.tag == f"{_A}t" else " " for node in paragraph.iter() if node.tag in (f"{_A}t", f"{_A}br"))
        for paragraph in root.iter(f"{_A}p")
    )


def _pptx_pages(path: Path) -> list[str]:
    import zipfile

    with zipfile.ZipFile(path) as archive:
        def pages():
            for name in _slide_parts(archive):
                try:
                    info = archive.getinfo(name)
                except KeyError:
                    yield ""
                    continue
                yield "" if info.file_size > MAX_SLIDE_XML else _slide_text(archive.read(info))

        return _limited(pages())


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
