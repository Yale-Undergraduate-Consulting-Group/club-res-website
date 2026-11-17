# Deck System checker proof of concept

## What was built

- `backend/app/services/deck_check.py` exposes the pure `check_deck(path) -> list[DeckFinding]` API. Every finding has `slide_number`, `rule`, `severity` (`error` or `warning`), and `message`; slide 0 denotes a deck-level finding.
- `backend/scripts/check_deck.py DECK.pptx [--json]` is the local gate. It exits 1 when any error exists and 0 for a clean deck or warnings only.
- `backend/tests/test_deck_check.py` builds all fixtures at run time with `python-pptx`; the production checker never imports or calls `Presentation`.
- `DECK_CHECK_ENABLED` is declared as an optional Resources setting and `deck_check_enabled()` defaults false. No upload, Slack, outreach, API, or UI path calls the checker.

## Rules implemented

Errors block the deck:

- malformed or empty PowerPoint packages, malformed slide XML, and XML parts over the 25 MiB safety limit;
- slide size other than 960 × 540 pt (13.333 × 7.5 in);
- text not resolving to Lato, including chart and SmartArt text, and body-zone type below 12 pt;
- explicit fills, lines, backgrounds, and text outside the Deck System palette; unresolved paint types; translucency, gradients, shadows, dashes, and shape outlines;
- estimated text overflow, effective autofit below 12 pt, or a text box extending outside the slide;
- missing content-slide title, title rule, canvas background, or `YUCG INC.` footer;
- a title moved off its fixed margin, not 32 pt, or not navy; and body text outside the x=21–940 content margins.

Warnings do not block:

- action titles without a full-stop, without a likely verb, longer than two estimated lines, or containing an exclamation mark (`Project Roadmap` and `Table of Contents` remain the documented exceptions);
- bracketed placeholders, dagger/slide-number cross-references, normal autofit shrinkage that remains at least 12 pt, and more than about 250 body words.

The overflow check is intentionally conservative: it estimates Lato glyph widths and line heights from the XML box, margins, wrap, and autofit values. It catches clear clipping without claiming to reproduce PowerPoint's renderer pixel-for-pixel. Warnings that need source documents or human judgment—whether a Central Question matches the SOW, a claim is sourced, a logo is recoloured, or a layout has the right semantic archetype—remain outside this gate.

## Bounded-memory evidence

The checker opens the package with `zipfile`, follows presentation relationships, and parses only presentation, relationship, theme, layout/master, slide, chart, and SmartArt XML. It never opens media or embedded workbooks. XML parts are individually capped at 25 MiB, and slide/context trees are discarded after each slide; peak use is independent of aggregate media size.

Observed local check on a synthetic 700 MiB `.pptx` whose media member was ZIP-stored (the valid two-slide deck was otherwise identical to the clean fixture):

- result: `PASS: no Deck System findings.`
- elapsed: 0.06 seconds;
- maximum resident set size: 21,905,408 bytes (20.9 MiB);
- swaps: 0.

This is direct evidence for a media-heavy 700 MiB deck, not a claim that every 700 MiB deck has the same XML complexity. A malicious or abnormal XML-heavy file stops at the per-part limit instead of expanding without bound.

## Synthetic evaluation

`/tmp/club-venv/bin/python tests/test_deck_check.py` generated and checked three tiny decks:

| Synthetic deck | Expected use | Result |
|---|---|---|
| clean | Correct size, canvas, Lato, navy title/rule, in-grid body, footer | 0 findings |
| warning | Valid structure with an unfinished bracketed source and non-sentence title | 2 warnings, 0 errors |
| blocked | Wrong size; Arial 10 pt red overflowing text off-grid; white canvas; outline; no title, rule, or footer | 11 errors, 0 warnings |

The same test proves the checker never opens a sentinel `ppt/media/*` member, malformed input becomes an `invalid-pptx` error, the setting defaults off, and CLI JSON counts and exit status match the pure API.

## Where it should live

**Recommendation: eventually run it when a new version is uploaded to Resources.** Block only `error` findings, return warnings for review, and leave the prior published version available when a replacement fails. This is the natural durable-file boundary, gives the uploader a slide number and fix, and avoids coupling design validation to mail sending. Before enabling it, run the CLI against a representative, authorized set of legacy decks to tune any false positives. A future implementation should stream/download the object to root-volume temporary space, never `/data`, then delete it after the check.

| Option | Benefit | Trade-off | Monthly AWS impact at current club scale |
|---|---|---|---|
| Resources new-version upload | One durable quality boundary; uploader gets immediate findings; failed replacement cannot displace the good version | Requires an asynchronous post-upload state for very large files and careful root-volume cleanup; legacy decks need calibration | ≈$0 new fixed cost; existing EC2 and documents S3 are reused, with negligible S3 request/EBS I/O at 37 members |
| Slack `/yucg check-deck` | Convenient where teams coordinate | Slack file-download authorization, size limits, private-file handling, and result threading add a second upload workflow; not a durable gate | ≈$0 new fixed AWS cost, but additional existing-box compute and Slack API traffic |
| Outreach attachment pre-send | Closest to an external-send safety boundary | Delays/block sends, covers only mail sent through Outreach, and puts a design heuristic in the most sensitive delivery path | ≈$0 new fixed AWS cost; burst CPU/temporary disk on the existing box |
| Standalone admin tool | Lowest rollout risk and useful for calibrating rules | Admin bottleneck; members can bypass it; no automatic quality boundary | ≈$0/month; existing box only |

No new paid service is justified. The current AWS pace remains approximately $29/month; this proof of concept changes it by $0/month because it is off and has no deployed caller.

## Decisions for the president

1. Confirm Resources new-version upload as the eventual integration boundary rather than Slack or outreach send.
2. Approve a calibration set of authorized, non-client or properly controlled legacy decks before setting `DECK_CHECK_ENABLED=true`.
3. Decide whether a future upload should expose all warning text to standard members or reserve source/governance warnings for admins.

## Verification and state

- All 101 backend test scripts: passed in six isolated parallel batches with `/tmp/club-venv/bin/python`.
- Focused synthetic test, CLI exercise, and Python compilation: passed.
- 700 MiB media-heavy synthetic deck memory exercise: passed at 20.9 MiB maximum RSS.
- No AWS, GitHub, production data, client data, deployment, Terraform, or remote state was accessed or changed.
