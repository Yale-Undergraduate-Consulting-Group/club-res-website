"""Validate PowerPoint decks against the YUCG Deck System.

The checker deliberately does not use :mod:`python-pptx`.  A presentation is
opened as a ZIP and only its small XML parts are parsed; media and embedded
objects are never read.  One slide tree is retained at a time, so memory use is
bounded by the largest permitted XML part rather than the size of the deck.
"""
from __future__ import annotations

import colorsys
import os
import posixpath
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Literal
from xml.etree import ElementTree as ET

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
EMU_PER_POINT = 12_700
MAX_XML_PART_BYTES = 25 * 1024 * 1024
EXPECTED_WIDTH_PT = 960.0
EXPECTED_HEIGHT_PT = 540.0

PALETTE = frozenset(
    {
        "00346B",  # navy-900
        "052457",  # navy-950
        "002060",  # navy-1000
        "1164A3",  # blue-600
        "0D4B7A",  # teal-800
        "44546A",  # slate-600
        "364D6E",  # slate-700
        "4C6C9C",  # steel-600
        "8497B0",  # steel-400 (hairlines and non-data accents only)
        "ADBACA",  # steel-300
        "C3D0E1",  # mist-300
        "D6DCE5",  # mist-200
        "DFE6EF",  # mist-100
        "ECECEC",  # canvas
        "D4D4D4",  # gray-300
        "CBCDD4",  # gray-350
        "E7E8EB",  # gray-100
        "AED6FF",  # sky-200 (portfolio matrix only; role requires review)
        "FFFFFF",  # white
        "1C3769",  # YUCG wordmark only
        "909AB1",  # YUCG wordmark only
        "4472C4",  # CIS screenshots only
    }
)
CANVAS = "ECECEC"
NAVY = "00346B"
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
DEFAULT_COLOR_MAP = {
    "bg1": "lt1",
    "tx1": "dk1",
    "bg2": "lt2",
    "tx2": "dk2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hlink": "hlink",
    "folHlink": "folHlink",
}
SYSTEM_COLORS = {"window": "FFFFFF", "windowText": "000000"}
VERB_HINT = re.compile(
    r"\b(?:is|are|was|were|has|have|had|will|can|could|should|must|drives?|"
    r"creates?|reduces?|increases?|shows?|requires?|enables?|limits?|supports?|"
    r"outperforms?|lags?|leads?|delivers?|faces?|needs?|offers?|remains?)\b",
    re.IGNORECASE,
)
SLIDE_REFERENCE = re.compile(r"(?:→\s*slide\s+\d+|\bslide\s+\d+\b)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DeckFinding:
    """One deterministic gate result.  Slide 0 denotes a deck-level finding."""

    slide_number: int
    rule: str
    severity: Literal["error", "warning"]
    message: str

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True, slots=True)
class _Theme:
    colors: dict[str, str]
    major_font: str | None
    minor_font: str | None
    color_map: dict[str, str]

    def color(self, name: str) -> str | None:
        return self.colors.get(self.color_map.get(name, name))

    def font(self, typeface: str | None, *, major: bool = False) -> str | None:
        if typeface:
            lowered = typeface.casefold()
            if lowered.startswith("+mj"):
                return self.major_font
            if lowered.startswith("+mn"):
                return self.minor_font
            return typeface
        return self.major_font if major else self.minor_font


@dataclass(slots=True)
class _TextBox:
    body: ET.Element
    box: _Box
    placeholder_type: str
    style_font_major: bool

    @property
    def text(self) -> str:
        return "\n".join(_paragraph_text(p) for p in self.body.findall(f"{A}p")).strip()


class _DeckFormatError(ValueError):
    pass


def deck_check_enabled() -> bool:
    """Whether a future app integration may invoke the checker; off by default."""

    return os.getenv("DECK_CHECK_ENABLED", "").strip().casefold() in TRUE_VALUES


def check_deck(path: str | os.PathLike[str]) -> list[DeckFinding]:
    """Return Deck System findings for *path* without loading the deck into memory.

    The function has no database, network, or application-state dependency.  It
    reports malformed/unsupported presentations as an ``invalid-pptx`` error so
    callers can use the result directly as a blocking gate.
    """

    deck_path = Path(path)
    try:
        with zipfile.ZipFile(deck_path) as archive:
            presentation = _parse_part(archive, "ppt/presentation.xml")
            relationships = _relationships(archive, "ppt/presentation.xml")
            slide_parts = _slide_parts(presentation, relationships)
            if not slide_parts:
                return [DeckFinding(0, "empty-deck", "error", "The presentation contains no slides.")]

            findings = _check_slide_size(presentation)
            fallback_theme = _first_theme(archive)
            presentation_default_font = _default_text_font(presentation, fallback_theme)
            for number, slide_part in enumerate(slide_parts, 1):
                try:
                    slide = _parse_part(archive, slide_part)
                    context = _slide_context(archive, slide_part, fallback_theme)
                    theme = context[0]
                    default_font = presentation_default_font or theme.minor_font
                    findings.extend(
                        _check_slide(
                            number,
                            slide,
                            theme,
                            default_font,
                            context[1],
                            context[2],
                            first_slide=number == 1,
                        )
                    )
                    findings.extend(
                        _check_related_drawing_xml(archive, slide_part, number, theme, default_font)
                    )
                except (ET.ParseError, KeyError, _DeckFormatError) as exc:
                    findings.append(
                        DeckFinding(number, "invalid-slide-xml", "error", f"Slide XML cannot be checked: {exc}.")
                    )
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError, _DeckFormatError) as exc:
        return [DeckFinding(0, "invalid-pptx", "error", f"The file is not a checkable PowerPoint deck: {exc}.")]

    return _ordered_unique(findings)


def _ordered_unique(findings: Iterable[DeckFinding]) -> list[DeckFinding]:
    severity_order = {"error": 0, "warning": 1}
    unique = set(findings)
    return sorted(
        unique,
        key=lambda item: (item.slide_number, severity_order[item.severity], item.rule, item.message),
    )


def _parse_part(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise _DeckFormatError(f"missing required part {name}") from exc
    if info.file_size > MAX_XML_PART_BYTES:
        raise _DeckFormatError(
            f"XML part {name} is {info.file_size:,} bytes; limit is {MAX_XML_PART_BYTES:,}"
        )
    with archive.open(info) as handle:
        return ET.parse(handle).getroot()


def _relationships(archive: zipfile.ZipFile, source: str) -> dict[str, tuple[str, str]]:
    directory, filename = posixpath.split(source)
    rels_part = posixpath.join(directory, "_rels", filename + ".rels")
    root = _parse_part(archive, rels_part)
    result: dict[str, tuple[str, str]] = {}
    for relationship in root.iter(f"{REL}Relationship"):
        if relationship.get("TargetMode") == "External":
            continue
        rel_id = relationship.get("Id")
        target = relationship.get("Target")
        if rel_id and target:
            base = "" if target.startswith("/") else directory
            resolved = posixpath.normpath(posixpath.join(base, target.lstrip("/")))
            result[rel_id] = (resolved, relationship.get("Type", ""))
    return result


def _related_part(relationships: dict[str, tuple[str, str]], suffix: str) -> str | None:
    for target, relationship_type in relationships.values():
        if relationship_type.endswith(suffix):
            return target
    return None


def _slide_parts(
    presentation: ET.Element, relationships: dict[str, tuple[str, str]]
) -> list[str]:
    parts: list[str] = []
    for slide_id in presentation.iter(f"{P}sldId"):
        relationship = relationships.get(slide_id.get(f"{R}id", ""))
        if relationship and relationship[1].endswith("/slide"):
            parts.append(relationship[0])
    return parts


def _check_slide_size(presentation: ET.Element) -> list[DeckFinding]:
    size = presentation.find(f"{P}sldSz")
    if size is None:
        return [DeckFinding(0, "slide-size", "error", "The presentation does not declare a slide size.")]
    try:
        width = int(size.get("cx", "0")) / EMU_PER_POINT
        height = int(size.get("cy", "0")) / EMU_PER_POINT
    except ValueError:
        return [DeckFinding(0, "slide-size", "error", "The presentation has an invalid slide size.")]
    if abs(width - EXPECTED_WIDTH_PT) <= 2 and abs(height - EXPECTED_HEIGHT_PT) <= 2:
        return []
    return [
        DeckFinding(
            0,
            "slide-size",
            "error",
            f"Slide size is {width:.1f} × {height:.1f} pt; YUCG decks must be 960 × 540 pt (13.333 × 7.5 in).",
        )
    ]


def _first_theme(archive: zipfile.ZipFile) -> _Theme:
    names = sorted(
        info.filename
        for info in archive.infolist()
        if info.filename.startswith("ppt/theme/theme") and info.filename.endswith(".xml")
    )
    if not names:
        return _Theme({}, None, None, dict(DEFAULT_COLOR_MAP))
    return _read_theme(_parse_part(archive, names[0]), DEFAULT_COLOR_MAP)


def _slide_context(
    archive: zipfile.ZipFile, slide_part: str, fallback: _Theme
) -> tuple[_Theme, ET.Element | None, ET.Element | None]:
    """Return theme, layout, and master roots for one slide without caching XML."""

    try:
        slide_relationships = _relationships(archive, slide_part)
    except _DeckFormatError:
        return fallback, None, None
    layout_part = _related_part(slide_relationships, "/slideLayout")
    if not layout_part:
        return fallback, None, None
    layout = _parse_part(archive, layout_part)
    try:
        layout_relationships = _relationships(archive, layout_part)
    except _DeckFormatError:
        return fallback, layout, None
    master_part = _related_part(layout_relationships, "/slideMaster")
    if not master_part:
        return fallback, layout, None
    master = _parse_part(archive, master_part)
    try:
        master_relationships = _relationships(archive, master_part)
    except _DeckFormatError:
        return fallback, layout, master
    theme_part = _related_part(master_relationships, "/theme")
    color_map = _color_map(master)
    if theme_part:
        return _read_theme(_parse_part(archive, theme_part), color_map), layout, master
    return _Theme(fallback.colors, fallback.major_font, fallback.minor_font, color_map), layout, master


def _read_theme(root: ET.Element, color_map: dict[str, str]) -> _Theme:
    colors: dict[str, str] = {}
    scheme = root.find(f".//{A}clrScheme")
    if scheme is not None:
        for slot in list(scheme):
            if not list(slot):
                continue
            color = _base_color(list(slot)[0], None)
            if color:
                colors[_local_name(slot.tag)] = color
    major = root.find(f".//{A}fontScheme/{A}majorFont/{A}latin")
    minor = root.find(f".//{A}fontScheme/{A}minorFont/{A}latin")
    return _Theme(
        colors,
        major.get("typeface") if major is not None else None,
        minor.get("typeface") if minor is not None else None,
        color_map,
    )


def _color_map(master: ET.Element) -> dict[str, str]:
    mapping = dict(DEFAULT_COLOR_MAP)
    node = master.find(f"{P}clrMap")
    if node is not None:
        mapping.update({key: value for key, value in node.attrib.items() if key in mapping})
    return mapping


def _default_text_font(presentation: ET.Element, theme: _Theme) -> str | None:
    latin = presentation.find(f"{P}defaultTextStyle/{A}lvl1pPr/{A}defRPr/{A}latin")
    return theme.font(latin.get("typeface") if latin is not None else None)


def _check_related_drawing_xml(
    archive: zipfile.ZipFile,
    slide_part: str,
    number: int,
    theme: _Theme,
    default_font: str | None,
) -> list[DeckFinding]:
    """Check chart and SmartArt text/style parts related directly to a slide."""

    findings: list[DeckFinding] = []
    relationships = _relationships(archive, slide_part)
    drawing_suffixes = ("/chart", "/diagramData")
    for target, relationship_type in relationships.values():
        if not relationship_type.endswith(drawing_suffixes):
            continue
        root = _parse_part(archive, target)
        findings.extend(_check_colors(number, root, theme))
        findings.extend(_check_forbidden_effects(number, root))
        fonts = {
            theme.font(node.get("typeface"))
            for node in root.iter(f"{A}latin")
            if node.get("typeface")
        }
        if not fonts and any(
            _local_name(node.tag) in {"t", "v"} and (node.text or "").strip()
            for node in root.iter()
        ):
            fonts.add(default_font)
        for font in sorted((font or "unresolved/theme default" for font in fonts)):
            if not font.casefold().startswith("lato"):
                findings.append(
                    DeckFinding(
                        number,
                        "font-family",
                        "error",
                        f"Chart or SmartArt text resolves to {font!r}; all YUCG deck text must use Lato.",
                    )
                )
    return findings


def _check_slide(
    number: int,
    slide: ET.Element,
    theme: _Theme,
    default_font: str | None,
    layout: ET.Element | None,
    master: ET.Element | None,
    *,
    first_slide: bool,
) -> list[DeckFinding]:
    findings: list[DeckFinding] = []
    inherited_geometry = _placeholder_geometry(master)
    inherited_geometry.update(_placeholder_geometry(layout, inherited_geometry))
    text_boxes = _collect_text_boxes(slide, inherited_geometry)
    layout_text = _all_text(layout) if layout is not None else ""
    master_text = _all_text(master) if master is not None else ""
    inherited_text = f"{layout_text}\n{master_text}"

    findings.extend(_check_fonts_and_sizes(number, text_boxes, theme, default_font))
    findings.extend(_check_colors(number, slide, theme))
    findings.extend(_check_forbidden_effects(number, slide))
    findings.extend(_check_text_overflow(number, text_boxes, theme, default_font))
    findings.extend(_check_text_content(number, text_boxes))
    findings.extend(_check_text_bounds(number, text_boxes))

    title = _find_title(text_boxes, theme, default_font)
    rule_present = _has_title_rule(slide, theme)
    full_bleed_picture = _has_full_bleed_picture(slide)
    body_text = [
        box
        for box in text_boxes
        if box.text and box.box.bottom > 88 and box.box.y < 505 and box.placeholder_type not in {"ftr", "sldNum", "dt"}
    ]
    divider_like = full_bleed_picture and not rule_present and any(
        _maximum_font_size(box.body, theme, default_font) >= 38 for box in body_text
    )
    cover_like = first_slide and not rule_present and (
        not text_boxes or any(_maximum_font_size(box.body, theme, default_font) >= 40 for box in text_boxes)
    )
    content_slide = bool(title or rule_present or body_text) and not cover_like and not divider_like

    if content_slide:
        if title is None or not title.text.strip():
            findings.append(
                DeckFinding(number, "missing-slide-title", "error", "Content slides require an action title.")
            )
        else:
            findings.extend(_check_title(number, title, theme, default_font))
        if not rule_present:
            findings.append(
                DeckFinding(
                    number,
                    "title-rule",
                    "error",
                    "The fixed 1.5 pt navy title rule at x=22–938, y=84 is missing or moved.",
                )
            )
        background = _background_color(slide, layout, master, theme)
        if background != CANVAS:
            shown = f"#{background}" if background else "the default/unknown background"
            findings.append(
                DeckFinding(
                    number,
                    "content-canvas",
                    "error",
                    f"Content slide background is {shown}; it must be canvas #ECECEC.",
                )
            )
        if "YUCG INC." not in ("\n".join(box.text for box in text_boxes) + "\n" + inherited_text).upper():
            findings.append(
                DeckFinding(number, "missing-footer", "error", "Content slides require the YUCG INC. footer.")
            )
        findings.extend(_check_content_margins(number, text_boxes, title))
        word_count = len(re.findall(r"\b[\w’'-]+\b", " ".join(box.text for box in body_text)))
        if word_count > 250:
            findings.append(
                DeckFinding(
                    number,
                    "word-budget",
                    "warning",
                    f"Slide has about {word_count} body words; evidence/profile slides should not exceed about 250.",
                )
            )

    return findings


def _placeholder_geometry(
    root: ET.Element | None, fallback: dict[tuple[str, str], _Box] | None = None
) -> dict[tuple[str, str], _Box]:
    result = dict(fallback or {})
    if root is None:
        return result
    for shape in root.iter(f"{P}sp"):
        placeholder = shape.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
        if placeholder is None:
            continue
        key = (placeholder.get("idx", "0"), placeholder.get("type", "body"))
        box = _shape_box(shape)
        if box is not None:
            result[key] = box
    return result


def _collect_text_boxes(
    slide: ET.Element, inherited_geometry: dict[tuple[str, str], _Box]
) -> list[_TextBox]:
    boxes: list[_TextBox] = []
    for element, visual_box in _visual_elements(slide):
        if element.tag == f"{P}sp":
            body = element.find(f"{P}txBody")
            if body is None:
                continue
            placeholder = element.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
            placeholder_type = placeholder.get("type", "body") if placeholder is not None else ""
            box = visual_box
            if box is None and placeholder is not None:
                key = (placeholder.get("idx", "0"), placeholder.get("type", "body"))
                box = inherited_geometry.get(key)
                if box is None:
                    box = next(
                        (value for (_, kind), value in inherited_geometry.items() if kind == placeholder_type),
                        None,
                    )
            if box is None:
                continue
            font_ref = element.find(f"{P}style/{A}fontRef")
            boxes.append(
                _TextBox(body, box, placeholder_type, font_ref is not None and font_ref.get("idx") == "major")
            )
            continue

        if element.tag != f"{P}graphicFrame" or visual_box is None:
            continue
        table = element.find(f".//{A}tbl")
        if table is None:
            continue
        raw_widths = [int(node.get("w", "0")) / EMU_PER_POINT for node in table.findall(f"{A}tblGrid/{A}gridCol")]
        rows = table.findall(f"{A}tr")
        raw_heights = [int(row.get("h", "0")) / EMU_PER_POINT for row in rows]
        width_scale = visual_box.width / sum(raw_widths) if sum(raw_widths) else 1
        height_scale = visual_box.height / sum(raw_heights) if sum(raw_heights) else 1
        row_y = visual_box.y
        for row, raw_height in zip(rows, raw_heights):
            row_height = raw_height * height_scale
            cell_x = visual_box.x
            for index, cell in enumerate(row.findall(f"{A}tc")):
                width = (raw_widths[index] if index < len(raw_widths) else 0) * width_scale
                body = cell.find(f"{A}txBody")
                if body is not None:
                    boxes.append(_TextBox(body, _Box(cell_x, row_y, width, row_height), "table", False))
                cell_x += width
            row_y += row_height
    return boxes


def _shape_box(shape: ET.Element) -> _Box | None:
    transform = shape.find(f"{P}spPr/{A}xfrm")
    return _box_from_transform(transform)


def _frame_box(frame: ET.Element) -> _Box | None:
    return _box_from_transform(frame.find(f"{P}xfrm"))


def _visual_elements(slide: ET.Element) -> Iterable[tuple[ET.Element, _Box | None]]:
    """Yield visual children with group-local coordinates mapped to slide points."""

    tree = slide.find(f"{P}cSld/{P}spTree")
    if tree is None:
        return

    def walk(parent: ET.Element, mapper):
        for element in list(parent):
            if element.tag not in {f"{P}sp", f"{P}pic", f"{P}graphicFrame", f"{P}cxnSp", f"{P}grpSp"}:
                continue
            raw_box = _raw_visual_box(element)
            absolute_box = mapper(raw_box) if raw_box is not None else None
            yield element, absolute_box
            if element.tag != f"{P}grpSp" or raw_box is None or absolute_box is None:
                continue
            transform = element.find(f"{P}grpSpPr/{A}xfrm")
            child_offset = transform.find(f"{A}chOff") if transform is not None else None
            child_extent = transform.find(f"{A}chExt") if transform is not None else None
            if child_offset is None or child_extent is None:
                continue
            try:
                child_x = int(child_offset.get("x", "0")) / EMU_PER_POINT
                child_y = int(child_offset.get("y", "0")) / EMU_PER_POINT
                child_width = int(child_extent.get("cx", "0")) / EMU_PER_POINT
                child_height = int(child_extent.get("cy", "0")) / EMU_PER_POINT
            except ValueError:
                continue
            if not child_width or not child_height:
                continue

            def group_mapper(box: _Box) -> _Box:
                return _Box(
                    absolute_box.x + (box.x - child_x) * absolute_box.width / child_width,
                    absolute_box.y + (box.y - child_y) * absolute_box.height / child_height,
                    box.width * absolute_box.width / child_width,
                    box.height * absolute_box.height / child_height,
                )

            yield from walk(element, group_mapper)

    yield from walk(tree, lambda box: box)


def _raw_visual_box(element: ET.Element) -> _Box | None:
    if element.tag == f"{P}graphicFrame":
        return _frame_box(element)
    if element.tag == f"{P}grpSp":
        return _box_from_transform(element.find(f"{P}grpSpPr/{A}xfrm"))
    return _box_from_transform(element.find(f"{P}spPr/{A}xfrm"))


def _box_from_transform(transform: ET.Element | None) -> _Box | None:
    if transform is None:
        return None
    offset = transform.find(f"{A}off")
    extent = transform.find(f"{A}ext")
    if offset is None or extent is None:
        return None
    try:
        return _Box(
            int(offset.get("x", "0")) / EMU_PER_POINT,
            int(offset.get("y", "0")) / EMU_PER_POINT,
            int(extent.get("cx", "0")) / EMU_PER_POINT,
            int(extent.get("cy", "0")) / EMU_PER_POINT,
        )
    except ValueError:
        return None


def _check_fonts_and_sizes(
    number: int, boxes: list[_TextBox], theme: _Theme, default_font: str | None
) -> list[DeckFinding]:
    bad_fonts: set[str] = set()
    findings: list[DeckFinding] = []
    undersized: set[float] = set()
    for text_box in boxes:
        for paragraph in text_box.body.findall(f"{A}p"):
            for text, props in _text_runs(paragraph):
                if not text.strip():
                    continue
                font = _run_font(props, paragraph, text_box, theme, default_font)
                if not font or not font.casefold().startswith("lato"):
                    bad_fonts.add(font or "unresolved/theme default")
                size = _run_size(props, paragraph)
                if 88 <= text_box.box.y < 505 and text_box.placeholder_type not in {"ftr", "sldNum", "dt"} and size < 12:
                    undersized.add(size)
    for font in sorted(bad_fonts):
        findings.append(
            DeckFinding(number, "font-family", "error", f"Text resolves to {font!r}; all YUCG deck text must use Lato.")
        )
    for size in sorted(undersized):
        findings.append(
            DeckFinding(
                number,
                "minimum-type-size",
                "error",
                f"Body text uses {size:g} pt type; body-zone text must be at least 12 pt.",
            )
        )
    return findings


def _check_colors(number: int, root: ET.Element, theme: _Theme) -> list[DeckFinding]:
    off_palette: set[str] = set()
    unresolved: set[str] = set()
    transparency = False
    for fill in root.iter(f"{A}solidFill"):
        if not list(fill):
            continue
        color_node = list(fill)[0]
        color = _resolved_color(color_node, theme)
        if color and color not in PALETTE:
            off_palette.add(color)
        elif color is None:
            unresolved.add(_local_name(color_node.tag))
        if any(_local_name(node.tag) == "alpha" and node.get("val") != "100000" for node in color_node):
            transparency = True
    findings = [
        DeckFinding(number, "colour-palette", "error", f"#{color} is outside the approved YUCG palette.")
        for color in sorted(off_palette)
    ]
    findings.extend(
        DeckFinding(
            number,
            "colour-palette",
            "error",
            f"A {kind} paint cannot be resolved to an approved YUCG palette colour.",
        )
        for kind in sorted(unresolved)
    )
    if transparency:
        findings.append(
            DeckFinding(number, "translucent-fill", "error", "Translucent fills or text are not allowed.")
        )
    return findings


def _check_forbidden_effects(number: int, root: ET.Element) -> list[DeckFinding]:
    findings: list[DeckFinding] = []
    if root.find(f".//{A}gradFill") is not None:
        findings.append(DeckFinding(number, "gradient", "error", "Gradients are not allowed."))
    if root.find(f".//{A}outerShdw") is not None or root.find(f".//{A}innerShdw") is not None:
        findings.append(DeckFinding(number, "shadow", "error", "Drop and inner shadows are not allowed."))
    if any(node.get("val", "solid") != "solid" for node in root.iter(f"{A}prstDash")):
        findings.append(DeckFinding(number, "dashed-line", "error", "Dashed lines and outlines are not allowed."))
    outlined = False
    for shape in root.iter(f"{P}sp"):
        geometry = shape.find(f"{P}spPr/{A}prstGeom")
        if geometry is not None and geometry.get("prst") == "line":
            continue
        line = shape.find(f"{P}spPr/{A}ln")
        line_reference = shape.find(f"{P}style/{A}lnRef")
        explicit_outline = line is not None and line.find(f"{A}noFill") is None
        inherited_outline = line is None and line_reference is not None and line_reference.get("idx", "0") != "0"
        if explicit_outline or inherited_outline:
            outlined = True
            break
    if outlined:
        findings.append(DeckFinding(number, "shape-outline", "error", "Shape outlines are not allowed."))
    return findings


def _check_text_overflow(
    number: int, boxes: list[_TextBox], theme: _Theme, default_font: str | None
) -> list[DeckFinding]:
    findings: list[DeckFinding] = []
    for index, text_box in enumerate(boxes, 1):
        if not text_box.text:
            continue
        body_properties = text_box.body.find(f"{A}bodyPr")
        body_properties = body_properties if body_properties is not None else ET.Element("bodyPr")
        left = int(body_properties.get("lIns", "91440")) / EMU_PER_POINT
        right = int(body_properties.get("rIns", "91440")) / EMU_PER_POINT
        top = int(body_properties.get("tIns", "45720")) / EMU_PER_POINT
        bottom = int(body_properties.get("bIns", "45720")) / EMU_PER_POINT
        width = max(1.0, text_box.box.width - left - right)
        height = max(1.0, text_box.box.height - top - bottom)
        if body_properties.get("vert") in {"vert", "vert270", "eaVert", "mongolianVert"}:
            width, height = height, width
        no_wrap = body_properties.get("wrap") == "none"
        autofit = body_properties.find(f"{A}spAutoFit") is not None
        normal_autofit = body_properties.find(f"{A}normAutofit")
        font_scale = 1.0
        if normal_autofit is not None:
            try:
                font_scale = int(normal_autofit.get("fontScale", "100000")) / 100_000
            except ValueError:
                font_scale = 1.0
        if autofit:
            continue
        needed_height = 0.0
        too_wide = False
        for paragraph in text_box.body.findall(f"{A}p"):
            text = _paragraph_text(paragraph)
            if not text:
                continue
            size = max(
                (_run_size(props, paragraph) for run_text, props in _text_runs(paragraph) if run_text),
                default=14.0,
            ) * font_scale
            lines, widest = _estimated_lines(text, width, size, no_wrap)
            needed_height += lines * size * 1.18
            too_wide = too_wide or (no_wrap and widest > width + 1)
        if needed_height > height + 1 or too_wide:
            findings.append(
                DeckFinding(
                    number,
                    "text-overflow",
                    "error",
                    f"Text box {index} is likely clipped ({needed_height:.0f} pt estimated text in {height:.0f} pt usable height).",
                )
            )
        if normal_autofit is not None and font_scale < 1:
            minimum = _minimum_font_size(text_box.body) * font_scale
            severity: Literal["error", "warning"] = "error" if minimum < 12 and 88 <= text_box.box.y < 505 else "warning"
            findings.append(
                DeckFinding(
                    number,
                    "text-autofit",
                    severity,
                    f"PowerPoint shrinks this text box to {font_scale:.0%}; fix the layout instead of shrinking type"
                    + (f" (effective minimum {minimum:.1f} pt)." if minimum else "."),
                )
            )
    return findings


def _check_text_content(number: int, boxes: list[_TextBox]) -> list[DeckFinding]:
    text = "\n".join(box.text for box in boxes)
    findings: list[DeckFinding] = []
    if "[" in text and "]" in text:
        findings.append(
            DeckFinding(number, "unfilled-placeholder", "warning", "Bracketed template copy remains on the slide.")
        )
    if "†" in text or SLIDE_REFERENCE.search(text):
        findings.append(
            DeckFinding(number, "slide-cross-reference", "warning", "Remove dagger marks and references to slide numbers.")
        )
    return findings


def _find_title(
    boxes: list[_TextBox], theme: _Theme, default_font: str | None
) -> _TextBox | None:
    placeholders = [box for box in boxes if box.placeholder_type in {"title", "ctrTitle"} and box.text]
    if placeholders:
        return min(placeholders, key=lambda item: item.box.y)
    candidates = [
        box
        for box in boxes
        if box.text and box.box.y < 84 and _maximum_font_size(box.body, theme, default_font) >= 20
    ]
    return min(candidates, key=lambda item: item.box.y) if candidates else None


def _check_title(
    number: int, title: _TextBox, theme: _Theme, default_font: str | None
) -> list[DeckFinding]:
    findings: list[DeckFinding] = []
    text = re.sub(r"\s+", " ", title.text).strip()
    if abs(title.box.x - 29) > 2 or title.box.right > 940:
        findings.append(
            DeckFinding(
                number,
                "off-grid-margin",
                "error",
                f"Title box is x={title.box.x:.1f}–{title.box.right:.1f}; it must start at x=29 and remain inside x=940.",
            )
        )
    size = _maximum_font_size(title.body, theme, default_font)
    if abs(size - 32) > 0.6:
        findings.append(
            DeckFinding(number, "title-type-size", "error", f"Action title is {size:g} pt; it must be 32 pt.")
        )
    title_colours = {
        color
        for fill in title.body.iter(f"{A}solidFill")
        if list(fill) and (color := _resolved_color(list(fill)[0], theme))
    }
    if title_colours and title_colours != {NAVY}:
        shown = ", ".join(f"#{color}" for color in sorted(title_colours))
        findings.append(
            DeckFinding(number, "title-colour", "error", f"Action title uses {shown}; it must use navy #00346B.")
        )
    navigation_title = text.casefold() in {"project roadmap", "table of contents"}
    if not navigation_title and not text.endswith("."):
        findings.append(
            DeckFinding(number, "action-title-copy", "warning", "Action titles should be full sentences ending in a period.")
        )
    if "!" in text:
        findings.append(DeckFinding(number, "exclamation-mark", "warning", "YUCG slide copy does not use exclamation marks."))
    if not navigation_title and text.endswith(".") and not VERB_HINT.search(text):
        findings.append(
            DeckFinding(number, "action-title-claim", "warning", "Confirm that the action title makes a claim with a verb.")
        )
    estimated_lines, _ = _estimated_lines(text, max(1, title.box.width), max(size, 1), False)
    if estimated_lines > 2:
        findings.append(
            DeckFinding(number, "title-length", "warning", "Action titles may use at most two lines.")
        )
    return findings


def _check_content_margins(
    number: int, boxes: list[_TextBox], title: _TextBox | None
) -> list[DeckFinding]:
    findings: list[DeckFinding] = []
    for index, text_box in enumerate(boxes, 1):
        if text_box is title or not text_box.text or not (88 <= text_box.box.y < 505):
            continue
        if text_box.box.x < 20 or text_box.box.right > 941:
            findings.append(
                DeckFinding(
                    number,
                    "off-grid-margin",
                    "error",
                    f"Body text box {index} reaches x={text_box.box.x:.1f}–{text_box.box.right:.1f}; content must stay inside x=21–940.",
                )
            )
    return findings


def _check_text_bounds(number: int, boxes: list[_TextBox]) -> list[DeckFinding]:
    findings: list[DeckFinding] = []
    for index, text_box in enumerate(boxes, 1):
        if not text_box.text:
            continue
        box = text_box.box
        if box.x < -1 or box.y < -1 or box.right > EXPECTED_WIDTH_PT + 1 or box.bottom > EXPECTED_HEIGHT_PT + 1:
            findings.append(
                DeckFinding(
                    number,
                    "clipped-text-box",
                    "error",
                    f"Text box {index} extends beyond the slide ({box.x:.1f}, {box.y:.1f}, {box.right:.1f}, {box.bottom:.1f} pt).",
                )
            )
    return findings


def _has_title_rule(slide: ET.Element, theme: _Theme) -> bool:
    for element, box in _visual_elements(slide):
        if box is None or element.tag not in {f"{P}sp", f"{P}cxnSp"}:
            continue
        fill = element.find(f"{P}spPr/{A}solidFill")
        color = _resolved_color(list(fill)[0], theme) if fill is not None and list(fill) else None
        geometry = element.find(f"{P}spPr/{A}prstGeom")
        is_line = element.tag == f"{P}cxnSp" or (geometry is not None and geometry.get("prst") == "line")
        line_fill = element.find(f"{P}spPr/{A}ln/{A}solidFill")
        if is_line and line_fill is not None and list(line_fill):
            color = _resolved_color(list(line_fill)[0], theme)
        if (
            color == NAVY
            and abs(box.x - 22) <= 2
            and abs(box.y - 84) <= 2
            and box.width >= 912
            and (is_line or box.height <= 3)
        ):
            return True
    return False


def _has_full_bleed_picture(slide: ET.Element) -> bool:
    for element, box in _visual_elements(slide):
        if element.tag == f"{P}pic" and box and box.x <= 2 and box.y <= 2 and box.right >= 958 and box.bottom >= 538:
            return True
    return False


def _background_color(
    slide: ET.Element,
    layout: ET.Element | None,
    master: ET.Element | None,
    theme: _Theme,
) -> str | None:
    for root in (slide, layout, master):
        if root is None:
            continue
        background = root.find(f"{P}cSld/{P}bg")
        if background is None:
            background = root.find(f"{P}bg")
        if background is None:
            continue
        solid = background.find(f"{P}bgPr/{A}solidFill")
        if solid is not None and list(solid):
            return _resolved_color(list(solid)[0], theme)
        reference = background.find(f"{P}bgRef")
        if reference is not None and list(reference):
            return _resolved_color(list(reference)[0], theme)
    return "FFFFFF"


def _resolved_color(node: ET.Element, theme: _Theme) -> str | None:
    base = _base_color(node, theme)
    if base is None:
        return None
    red, green, blue = (int(base[index : index + 2], 16) / 255 for index in (0, 2, 4))
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    for transform in node:
        name = _local_name(transform.tag)
        try:
            value = int(transform.get("val", "0")) / 100_000
        except ValueError:
            continue
        if name == "lumMod":
            lightness *= value
        elif name == "lumOff":
            lightness += value
        elif name == "tint":
            red, green, blue = (channel + (1 - channel) * value for channel in (red, green, blue))
            hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
        elif name == "shade":
            red, green, blue = (channel * value for channel in (red, green, blue))
            hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    red, green, blue = colorsys.hls_to_rgb(hue, max(0, min(1, lightness)), saturation)
    return "".join(f"{round(max(0, min(1, channel)) * 255):02X}" for channel in (red, green, blue))


def _base_color(node: ET.Element, theme: _Theme | None) -> str | None:
    name = _local_name(node.tag)
    value = node.get("val", "")
    if name == "srgbClr" and re.fullmatch(r"[0-9A-Fa-f]{6}", value):
        return value.upper()
    if name == "schemeClr" and theme is not None:
        return theme.color(value)
    if name == "sysClr":
        last = node.get("lastClr", "")
        if re.fullmatch(r"[0-9A-Fa-f]{6}", last):
            return last.upper()
        return SYSTEM_COLORS.get(value)
    if name == "prstClr":
        return {"white": "FFFFFF", "black": "000000"}.get(value)
    return None


def _paragraph_text(paragraph: ET.Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{A}t":
            pieces.append(node.text or "")
        elif node.tag == f"{A}br":
            pieces.append("\n")
    return "".join(pieces)


def _text_runs(paragraph: ET.Element) -> Iterable[tuple[str, ET.Element | None]]:
    for child in paragraph:
        if child.tag in {f"{A}r", f"{A}fld"}:
            text = child.find(f"{A}t")
            yield (text.text or "" if text is not None else "", child.find(f"{A}rPr"))
        elif child.tag == f"{A}br":
            yield "\n", child.find(f"{A}rPr")


def _run_font(
    run_properties: ET.Element | None,
    paragraph: ET.Element,
    text_box: _TextBox,
    theme: _Theme,
    default_font: str | None,
) -> str | None:
    typeface = _latin_typeface(run_properties)
    if not typeface:
        paragraph_properties = paragraph.find(f"{A}pPr/{A}defRPr")
        typeface = _latin_typeface(paragraph_properties)
    if not typeface:
        level = 1
        paragraph_properties = paragraph.find(f"{A}pPr")
        if paragraph_properties is not None:
            try:
                level = int(paragraph_properties.get("lvl", "0")) + 1
            except ValueError:
                level = 1
        list_default = text_box.body.find(f"{A}lstStyle/{A}lvl{level}pPr/{A}defRPr")
        typeface = _latin_typeface(list_default)
    return theme.font(typeface, major=text_box.style_font_major) or default_font


def _latin_typeface(properties: ET.Element | None) -> str | None:
    if properties is None:
        return None
    latin = properties.find(f"{A}latin")
    return latin.get("typeface") if latin is not None else None


def _run_size(run_properties: ET.Element | None, paragraph: ET.Element) -> float:
    for properties in (run_properties, paragraph.find(f"{A}pPr/{A}defRPr"), paragraph.find(f"{A}endParaRPr")):
        if properties is not None and properties.get("sz"):
            try:
                return int(properties.get("sz", "1400")) / 100
            except ValueError:
                pass
    return 14.0


def _maximum_font_size(body: ET.Element, theme: _Theme, default_font: str | None) -> float:
    del theme, default_font  # kept in the signature beside other typography helpers
    return max(
        (
            _run_size(properties, paragraph)
            for paragraph in body.findall(f"{A}p")
            for text, properties in _text_runs(paragraph)
            if text.strip()
        ),
        default=0.0,
    )


def _minimum_font_size(body: ET.Element) -> float:
    return min(
        (
            _run_size(properties, paragraph)
            for paragraph in body.findall(f"{A}p")
            for text, properties in _text_runs(paragraph)
            if text.strip()
        ),
        default=0.0,
    )


def _estimated_lines(text: str, width: float, size: float, no_wrap: bool) -> tuple[int, float]:
    explicit_lines = text.splitlines() or [""]
    total = 0
    widest = 0.0
    for line in explicit_lines:
        line_width = _text_width(line, size)
        widest = max(widest, line_width)
        if no_wrap or line_width <= width:
            total += 1
            continue
        current = 0.0
        wrapped = 1
        for word in re.findall(r"\S+\s*", line):
            word_width = _text_width(word, size)
            if current and current + word_width > width:
                wrapped += 1
                current = word_width
            else:
                current += word_width
            if word_width > width:
                wrapped += max(0, int(word_width // width) - 1)
                current = word_width % width
        total += wrapped
    return max(1, total), widest


def _text_width(text: str, size: float) -> float:
    units = 0.0
    for character in text:
        if character.isspace():
            units += 0.28
        elif character in "ilI.,:;'|!":
            units += 0.27
        elif character in "MW@%&":
            units += 0.9
        elif character.isupper():
            units += 0.62
        else:
            units += 0.51
    return units * size


def _all_text(root: ET.Element | None) -> str:
    if root is None:
        return ""
    return " ".join((node.text or "") for node in root.iter(f"{A}t"))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
