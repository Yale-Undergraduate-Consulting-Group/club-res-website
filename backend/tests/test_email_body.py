"""What the recipient receives, and what a repeated Generate costs."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.email_body import (  # noqa: E402
    SignOff, html_to_text, render_email, sanitize_html,
)
from app.services.gmail_api import _build_message  # noqa: E402


def editor_html_never_reaches_the_recipient_as_markup() -> None:
    """The Studio editor is a contentEditable producing HTML. The sender used
    to attach that same string as the text/plain alternative, so anyone whose
    client prefers plain text was shown "<p>Dear Ashley,</p>"."""
    body = "<p>Dear Ashley,</p><p>I am <b>Andre</b>.</p><ul><li>One</li><li>Two</li></ul>"
    text, doc = render_email(body)

    assert "<" not in text, text
    assert text.startswith("Dear Ashley,")
    # A list reads as a list, single spaced, not as four paragraphs.
    assert "- One\n- Two" in text, text
    # And the HTML copy keeps real paragraphs instead of pre-wrap plus <br>.
    assert "<p>Dear Ashley,</p>" in doc
    assert "pre-wrap" not in doc


def typed_text_is_escaped_and_given_paragraphs() -> None:
    text, doc = render_email("Hi there\n\nSecond para with <angle> brackets")
    assert "&lt;angle&gt;" in doc
    assert doc.count("<p ") == 2
    assert "<angle>" in text


def hostile_markup_is_stripped_from_outgoing_mail() -> None:
    dirty = '<p onclick="steal()">Hi</p><script>bad()</script><iframe src="x"></iframe><a href="javascript:x">click</a>'
    clean = sanitize_html(dirty)
    assert "script" not in clean.lower()
    assert "iframe" not in clean.lower()
    assert "onclick" not in clean.lower()
    # The anchor survives as an anchor but loses the javascript: target.
    assert "javascript:" not in clean.lower()


def sign_off_is_assembled_from_stored_facts_not_written_by_a_model() -> None:
    """The club signs off with logo, name and pronouns, role, organization,
    then LinkedIn and phone. Those are facts about the sender, so they are
    rendered from stored fields; the model is told never to write them."""
    sign_off = SignOff(
        name="Andre Costa", pronouns="He/Him", role="Client recruitment director",
        linkedin_url="https://linkedin.com/in/andre", phone="+1 (212) 814-1360",
        logo_url="https://example.org/yucg.png",
    )
    text, doc = render_email("<p>Body.</p>", sign_off=sign_off)

    assert "Andre Costa (He/Him)" in html_to_text(doc)
    assert "Client recruitment director" in doc
    assert "Yale Undergraduate Consulting Group" in doc
    assert 'href="https://linkedin.com/in/andre"' in doc
    assert "+1 (212) 814-1360" in doc
    assert "yucg.png" in doc
    # Table layout, because Outlook does not lay out flex or grid.
    assert "<table" in doc

    assert "Andre Costa (He/Him)" in text
    assert "Client recruitment director" in text


def an_empty_sign_off_adds_no_dangling_separator() -> None:
    text, doc = render_email("<p>Body.</p>", sign_off=SignOff())
    assert "--" not in text
    assert "border-top" not in doc


def composer_formatting_survives_but_style_cannot_fetch_or_position() -> None:
    """The toolbar writes colour, font, size and alignment as inline CSS, which
    is what mail clients render. The attribute used to be copied through whole,
    so a pasted page could carry a url() fetch into the member's outgoing mail."""
    _, doc = render_email(
        '<p style="text-align: center"><span style="font-family: Georgia, serif;'
        ' font-size: 18px; color: #00356b">Hello</span></p>'
        '<p style="position: fixed; background-image: url(https://tracker.example/p.gif)">Body</p>'
    )
    assert 'text-align: center' in doc
    assert 'font-family: Georgia, serif' in doc and 'font-size: 18px' in doc
    assert 'color: #00356b' in doc
    assert 'position' not in doc and 'tracker.example' not in doc

def attachments_are_named_in_the_email_not_just_in_the_envelope() -> None:
    """A file riding silently in the MIME envelope is easy to miss, so the
    foot of the message lists what was actually attached."""
    text, doc = render_email(
        "<p>Body.</p>",
        sign_off=SignOff(name="Andre Costa"),
        attachments=["YUCG capabilities.pdf", "Case study.pdf"],
    )
    assert "Attached" in doc and "YUCG capabilities.pdf" in doc
    assert "Attached:\n- YUCG capabilities.pdf\n- Case study.pdf" in text
    # The list sits above the sign-off, the way the club's emails read.
    assert doc.index("YUCG capabilities.pdf") < doc.index("Andre Costa")


def html_to_text_handles_an_empty_or_plain_body() -> None:
    assert html_to_text("") == ""
    assert html_to_text("just words") == "just words"

def attachments_wrap_the_alternative_body_in_multipart_mixed() -> None:
    msg = _build_message(
        "Subject", "Sender <sender@example.com>", "recipient@example.com",
        "Plain body", "<p>HTML body</p>",
        [(b"pdf", "proposal.pdf", "application/pdf")],
    )
    assert msg.get_content_subtype() == "mixed"
    alternatives = msg.get_payload()[0]
    assert alternatives.get_content_subtype() == "alternative"
    assert [part.get_content_subtype() for part in alternatives.get_payload()] == ["plain", "html"]
    assert msg.get_payload()[1].get_filename() == "proposal.pdf"


if __name__ == "__main__":
    editor_html_never_reaches_the_recipient_as_markup()
    typed_text_is_escaped_and_given_paragraphs()
    hostile_markup_is_stripped_from_outgoing_mail()
    sign_off_is_assembled_from_stored_facts_not_written_by_a_model()
    an_empty_sign_off_adds_no_dangling_separator()
    attachments_are_named_in_the_email_not_just_in_the_envelope()
    composer_formatting_survives_but_style_cannot_fetch_or_position()
    html_to_text_handles_an_empty_or_plain_body()
    attachments_wrap_the_alternative_body_in_multipart_mixed()
    print("email body: ok")
