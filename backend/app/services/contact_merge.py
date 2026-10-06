"""Merge domain-crawl, roster, and web-discovery contacts (shared by Scraper + YUCG)."""
from __future__ import annotations

from app.services.company_email_cache import build_email_for_person_sync
from app.services.contact_scraper import (
    _best_confidence,
    confidence_for_contact_dict,
    is_valid_person_contact,
    normalize_domain,
    person_name_key,
)
from app.services.web_contact_discovery import linkedin_profile_key


def merge_contacts(
    domain_contacts: list[dict],
    company: str,
    domain: str,
    custom_patterns: list[str] | None = None,
    web_contacts: list[dict] | None = None,
) -> list[dict]:
    """Merge domain-crawl, roster-cache, and web-discovery contacts.

    web_contacts rows may still carry linkedin /in/ profile URLs from search
    results — those participate in name/profile dedupe like any other row.
    """
    seen_emails: set[str] = set()
    merged: list[dict] = []
    by_name: dict[str, dict] = {}
    by_linkedin: dict[str, dict] = {}

    def _index(row: dict) -> None:
        if row.get("name"):
            by_name[person_name_key(row["name"])] = row
        li_key = linkedin_profile_key(row.get("linkedin_url"))
        if li_key:
            by_linkedin[li_key] = row

    def _find_match(name: str | None, linkedin_url: str | None) -> dict | None:
        if name:
            hit = by_name.get(person_name_key(name))
            if hit:
                return hit
        li_key = linkedin_profile_key(linkedin_url)
        if li_key:
            return by_linkedin.get(li_key)
        return None

    def _with_mail(raw: dict) -> dict:
        row = dict(raw)
        if (row.get("email") or "").strip():
            return row
        host = normalize_domain(row.get("company_domain") or domain or "")
        person = (row.get("name") or "").strip()
        if not host or not person:
            return row
        guessed = build_email_for_person_sync(person, host, custom_patterns=custom_patterns)
        if guessed:
            row["email"] = guessed
        return row

    for c in domain_contacts:
        dc = _with_mail(c)
        if not is_valid_person_contact(dc, company_name=company, domain=domain):
            continue
        email = dc.get("email")
        if not email or email in seen_emails:
            continue
        seen_emails.add(email)
        dc.setdefault("contact_source", "domain_scrape")
        dc["confidence"] = _best_confidence(
            dc.get("confidence"),
            confidence_for_contact_dict(dc, company_name=company, domain=domain),
        )
        merged.append(dc)
        _index(merged[-1])

    for c in web_contacts or []:
        wc = _with_mail(c)
        if not is_valid_person_contact(wc, company_name=company, domain=domain):
            continue
        email = wc.get("email")
        if not email or email in seen_emails:
            continue
        matched = _find_match(wc.get("name"), wc.get("linkedin_url"))
        if matched:
            matched["linkedin_url"] = c.get("linkedin_url") or matched.get("linkedin_url")
            matched["title"] = matched.get("title") or c.get("title")
            matched["source_url"] = matched.get("source_url") or c.get("source_url")
            matched["discovery_context"] = matched.get("discovery_context") or c.get("discovery_context")
            matched["contact_source"] = matched.get("contact_source") or "web_discovery"
            matched["confidence"] = _best_confidence(
                matched.get("confidence"),
                confidence_for_contact_dict(matched, company_name=company, domain=domain),
            )
            continue
        seen_emails.add(email)
        wc.setdefault("contact_source", "web_discovery")
        wc["confidence"] = confidence_for_contact_dict(wc, company_name=company, domain=domain)
        merged.append(wc)
        _index(merged[-1])

    return merged
