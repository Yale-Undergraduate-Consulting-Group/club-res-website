"""Deck System gate tests.  Every presentation and name is synthetic."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pptx import Presentation  # noqa: E402
from pptx.dml.color import RGBColor  # noqa: E402
from pptx.enum.shapes import MSO_SHAPE  # noqa: E402
from pptx.enum.text import MSO_AUTO_SIZE  # noqa: E402
from pptx.util import Pt  # noqa: E402

from app.services import deck_check  # noqa: E402

NAVY = RGBColor(0x00, 0x34, 0x6B)
CANVAS = RGBColor(0xEC, 0xEC, 0xEC)
RED = RGBColor(0xFF, 0x00, 0x00)
ROOT = Path(__file__).resolve().parents[1]


def _text_box(slide, x, y, width, height, text, *, size=14, font="Lato", color=NAVY):
    shape = slide.shapes.add_textbox(Pt(x), Pt(y), Pt(width), Pt(height))
    shape.fill.background()
    shape.line.fill.background()
    frame = shape.text_frame
    frame.clear()
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = frame.margin_right = Pt(0)
    frame.margin_top = frame.margin_bottom = Pt(0)
    run = frame.paragraphs[0].add_run()
    run.text = text
    run.font.name = font
    run.font.size = Pt(size)
    run.font.color.rgb = color
    return shape


def _base_deck(path: Path, *, width=960) -> tuple[Presentation, object]:
    presentation = Presentation()
    presentation.slide_width = Pt(width)
    presentation.slide_height = Pt(540)
    presentation.slides.add_slide(presentation.slide_layouts[6])  # synthetic cover
    content = presentation.slides.add_slide(presentation.slide_layouts[6])
    return presentation, content


def _content_chrome(slide, title: str) -> None:
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = CANVAS
    _text_box(slide, 29, 10, 902, 72, title, size=32)
    rule = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Pt(22), Pt(84), Pt(916), Pt(1.5))
    rule.fill.solid()
    rule.fill.fore_color.rgb = NAVY
    rule.line.fill.background()
    _text_box(slide, 800, 515, 130, 15, "YUCG INC.  1", size=11)


def build_clean(path: Path) -> None:
    presentation, slide = _base_deck(path)
    _content_chrome(slide, "The synthetic market is growing rapidly.")
    _text_box(slide, 22, 100, 917, 80, "Synthetic evidence supports the claim.")
    presentation.save(path)


def build_warning(path: Path) -> None:
    presentation, slide = _base_deck(path)
    _content_chrome(slide, "Synthetic market overview")
    _text_box(slide, 22, 100, 917, 80, "[Metric, unit and synthetic source]")
    presentation.save(path)


def build_blocked(path: Path) -> None:
    presentation, slide = _base_deck(path, width=720)
    box = _text_box(
        slide,
        5,
        100,
        120,
        20,
        "This deliberately long synthetic sentence cannot fit inside its tiny text box.",
        size=10,
        font="Arial",
        color=RED,
    )
    box.line.fill.solid()
    box.line.fill.fore_color.rgb = RED
    presentation.save(path)


def _rules(findings, severity=None):
    return {item.rule for item in findings if severity is None or item.severity == severity}


def run() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        clean = root / "clean.pptx"
        warning = root / "warning.pptx"
        blocked = root / "blocked.pptx"
        build_clean(clean)
        build_warning(warning)
        build_blocked(blocked)

        clean_findings = deck_check.check_deck(clean)
        assert clean_findings == [], clean_findings

        warning_findings = deck_check.check_deck(warning)
        assert not _rules(warning_findings, "error"), warning_findings
        assert {"action-title-copy", "unfilled-placeholder"} <= _rules(warning_findings, "warning")

        blocked_findings = deck_check.check_deck(blocked)
        expected = {
            "slide-size",
            "missing-slide-title",
            "title-rule",
            "content-canvas",
            "missing-footer",
            "off-grid-margin",
            "font-family",
            "minimum-type-size",
            "colour-palette",
            "shape-outline",
            "text-overflow",
        }
        assert expected <= _rules(blocked_findings, "error"), (
            expected - _rules(blocked_findings, "error"),
            blocked_findings,
        )

        original_open = zipfile.ZipFile.open

        def reject_media(self, name, *args, **kwargs):
            filename = name.filename if isinstance(name, zipfile.ZipInfo) else str(name)
            assert not filename.startswith("ppt/media/"), filename
            return original_open(self, name, *args, **kwargs)

        with zipfile.ZipFile(clean, "a", zipfile.ZIP_STORED) as archive:
            archive.writestr("ppt/media/synthetic-large-object.bin", b"not opened by the checker")
        with patch.object(zipfile.ZipFile, "open", reject_media):
            assert deck_check.check_deck(clean) == []

        invalid = root / "invalid.pptx"
        invalid.write_bytes(b"not a zip")
        invalid_findings = deck_check.check_deck(invalid)
        assert len(invalid_findings) == 1 and invalid_findings[0].rule == "invalid-pptx"

        os.environ.pop("DECK_CHECK_ENABLED", None)
        assert deck_check.deck_check_enabled() is False
        os.environ["DECK_CHECK_ENABLED"] = "true"
        assert deck_check.deck_check_enabled() is True
        os.environ.pop("DECK_CHECK_ENABLED")

        command = [sys.executable, str(ROOT / "scripts" / "check_deck.py"), str(blocked), "--json"]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert completed.returncode == 1, completed
        report = json.loads(completed.stdout)
        assert report["errors"] == sum(item.severity == "error" for item in blocked_findings)
        assert report["warnings"] == sum(item.severity == "warning" for item in blocked_findings)

        print(
            "Deck checker evaluation: "
            f"clean={len(clean_findings)} findings; "
            f"warning={len(warning_findings)} findings; "
            f"blocked={len(blocked_findings)} findings "
            f"({sum(item.severity == 'error' for item in blocked_findings)} errors)."
        )


if __name__ == "__main__":
    run()
