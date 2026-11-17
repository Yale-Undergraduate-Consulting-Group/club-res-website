#!/usr/bin/env python3
"""Check a .pptx against the YUCG Deck System without loading its media."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.deck_check import check_deck  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("deck", help="path to the .pptx deck")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    findings = check_deck(args.deck)
    errors = sum(item.severity == "error" for item in findings)
    warnings = sum(item.severity == "warning" for item in findings)
    if args.json:
        print(
            json.dumps(
                {
                    "deck": str(Path(args.deck)),
                    "errors": errors,
                    "warnings": warnings,
                    "findings": [item.to_dict() for item in findings],
                },
                indent=2,
            )
        )
    else:
        for item in findings:
            location = "deck" if item.slide_number == 0 else f"slide {item.slide_number}"
            print(f"{location}: {item.severity.upper()} [{item.rule}] {item.message}")
        if not findings:
            print("PASS: no Deck System findings.")
        else:
            status = "BLOCK" if errors else "PASS WITH WARNINGS"
            print(f"{status}: {errors} error(s), {warnings} warning(s).")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
