"""Text handling for the Market sentiment app: transcript parsing, sentences,
tokens and word statistics. Standard library plus ``pypdf``; no NLP packages
and nothing downloaded at run time.
"""
from __future__ import annotations

import html
import io
import re
import zipfile
from collections import defaultdict
from typing import Any

POSITIVE_AT = 0.05
NEGATIVE_AT = -0.05

MAX_DOCX_XML_BYTES = 40 * 1024 * 1024
MAX_PDF_PAGES = 400

ALLOWED_EXTENSIONS = (".docx", ".txt", ".pdf")


def label_for(score: float) -> str:
    if score >= POSITIVE_AT:
        return "positive"
    if score <= NEGATIVE_AT:
        return "negative"
    return "neutral"


class TranscriptError(ValueError):
    """A file that cannot be read as a transcript; the message is for the member."""


# --- Reading files -----------------------------------------------------------

_PARAGRAPH_END = re.compile(r"</w:p\s*>")
_RUN_PIECE = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t\s*>|<w:(tab|br|cr)\s*/>", re.S)


def docx_lines(data: bytes) -> list[str]:
    """Paragraphs of a .docx. word/document.xml is read with patterns rather
    than an XML parser: an uploaded file is untrusted and the stdlib parsers
    expand entity bombs."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise TranscriptError("The document is too large to read")
            xml = archive.read(info).decode("utf-8", errors="ignore")
    except (zipfile.BadZipFile, KeyError):
        raise TranscriptError("Not a valid .docx file") from None
    lines = []
    for paragraph in _PARAGRAPH_END.split(xml):
        text = "".join(
            html.unescape(m.group(1)) if m.group(1) is not None else " "
            for m in _RUN_PIECE.finditer(paragraph)
        ).strip()
        if text:
            lines.append(text)
    return lines


def txt_lines(data: bytes) -> list[str]:
    text = data.decode("utf-8-sig", errors="ignore")
    return [line.strip() for line in text.splitlines() if line.strip()]


def pdf_lines(data: bytes) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise TranscriptError("The PDF is password protected")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise TranscriptError(f"The PDF has more than {MAX_PDF_PAGES} pages")
        lines: list[str] = []
        for page in reader.pages:
            lines.extend(l.strip() for l in (page.extract_text() or "").splitlines() if l.strip())
        return lines
    except (PyPdfError, ValueError, KeyError, OSError):
        raise TranscriptError("Not a readable PDF file") from None


def read_lines(filename: str, data: bytes) -> list[str]:
    name = filename.lower()
    if name.endswith(".docx"):
        return docx_lines(data)
    if name.endswith(".txt"):
        return txt_lines(data)
    if name.endswith(".pdf"):
        return pdf_lines(data)
    raise TranscriptError("Only .docx, .txt and .pdf files are accepted")


# --- Lines to speech ---------------------------------------------------------

_WEBVTT_TS = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d+\s*-->\s*\d{2}:\d{2}:\d{2}\.\d+$")
_RANGE_TS = re.compile(r"^\(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\)$")
_SPEAKER_COLON = re.compile(r"^([A-Za-z][A-Za-z .'-]{0,50}?):\s*(.+)$")
_SPEAKER_BRACKET = re.compile(r"^\[([^\]]+)\]\s*[:\-]?\s*(.*)$")


def parse_line(raw: str) -> tuple[str, str]:
    """(speaker, text) of one transcript line; speaker is '' when unlabelled.
    The colon is required so prose is never mistaken for a name."""
    line = raw.strip()
    if not line or line.upper() == "WEBVTT" or _WEBVTT_TS.match(line) or _RANGE_TS.match(line):
        return "", ""
    for pattern in (_SPEAKER_BRACKET, _SPEAKER_COLON):
        m = pattern.match(line)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return "", line


def tag_roles(name: str, speaker_lines: list[tuple[str, str]]) -> dict[str, str]:
    """speaker -> 'interviewee' | 'interviewer'. The speaker matching the file
    name is the interviewee, else the one with the most lines. No speaker
    labels at all gives {} (everything counts)."""
    counts: dict[str, int] = defaultdict(int)
    for speaker, text in speaker_lines:
        if speaker and text:
            counts[speaker] += 1
    if not counts:
        return {}
    wanted = re.sub(r"[_\s]+", " ", name).strip().lower()
    exact = [s for s in counts if s.lower() == wanted]
    top = exact[0] if exact else max(counts, key=lambda s: counts[s])
    return {s: ("interviewee" if s == top else "interviewer") for s in counts}


# --- Sentences and words -----------------------------------------------------

_ABBREVIATIONS = ("mr", "mrs", "ms", "dr", "prof", "vs", "etc", "e.g", "i.e", "inc", "co", "st", "no")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def sentences(text: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"\s*\n\s*", text.strip()):
        start = 0
        for m in _SENTENCE_END.finditer(part):
            last_word = part[start:m.start()].rsplit(" ", 1)[-1].rstrip(".").lower()
            if last_word in _ABBREVIATIONS:
                continue
            piece = part[start:m.end()].strip()
            if piece:
                out.append(piece)
            start = m.end()
        tail = part[start:].strip()
        if tail:
            out.append(tail)
    return out


STOPWORDS = frozenset("""
a about above after again against all also am an and any are aren't as at be because been before being below
between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few
for from further get got had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers
herself him himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself just let's me more
most mustn't my myself no nor not of off on once only or other ought our ours ourselves out over own same shan't
she she'd she'll she's should shouldn't so some such than that that's the their theirs them themselves then
there there's these they they'd they'll they're they've this those through to too under until up very was
wasn't we we'd we'll we're we've were weren't what what's when when's where where's which while who who's whom
why why's with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
like um uh yeah okay ok sort kind really know mean thing things lot much even still say said think
going go one way well right actually maybe probably basically
""".split())

_WORD = re.compile(r"[a-z][a-z']*")


def tokens(text: str, extra_stop: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Lower-case words of three letters or more, without stopwords or URLs."""
    text = re.sub(r"https?://\S+|www\.\S+", " ", text.lower())
    return [w for w in (t.strip("'") for t in _WORD.findall(text))
            if len(w) >= 3 and w not in STOPWORDS and w not in extra_stop]


def name_words(*names: str) -> set[str]:
    return {w for n in names for w in _WORD.findall(n.lower())}


def mentions(text: str, name: str) -> bool:
    return bool(name.strip()) and re.search(r"\b" + re.escape(name.strip()) + r"\b", text, re.I) is not None


def word_stats(rows: list[tuple[str, float]], stop: set[str], *, min_count: int = 2, top: int = 40) -> list[dict[str, Any]]:
    """Words by how many of the given (text, score) sentences use them, with
    the average score of those sentences. A plural is counted with its singular
    when both appear."""
    seen: dict[str, list[float]] = defaultdict(list)
    for text, score in rows:
        for word in set(tokens(text, stop)):
            seen[word].append(score)
    for word in [w for w in seen if w.endswith("s") and w[:-1] in seen]:
        seen[word[:-1]].extend(seen.pop(word))
    stats = [{"word": w, "count": len(v), "avg_sentiment": round(sum(v) / len(v), 3)}
             for w, v in seen.items() if len(v) >= min_count]
    stats.sort(key=lambda s: (-s["count"], s["word"]))
    return stats[:top]


def summarise(scores: list[float]) -> dict[str, Any]:
    n = len(scores)
    dist = {"positive": 0, "neutral": 0, "negative": 0}
    for s in scores:
        dist[label_for(s)] += 1
    return {
        "count": n,
        "avg_sentiment": round(sum(scores) / n, 4) if n else None,
        "distribution": dist,
    }
