"""Website host vs work-mail host.

Most companies receive mail on the public website domain (first.last@garmin.com).
Some do not: Itaú Unibanco's site is itau.com.br, while officers are often on
itaubba.com.br. This module stores that map, seeds the known exceptions, and
learns new ones from published addresses, bounces, and replies.

MX/SMTP still decide whether a host can receive mail. This only answers
"which host should we mint against".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database import get_db
from app.services.contact_scraper import normalize_domain

# Website domain → preferred mailbox hosts. Keep this list small and evidenced.
# Learned bounce/reply rows override seed ranking when they disagree.
_ITAU_MAIL = "itaubba.com.br"
_ITAU_SITES = (
    "itau.com.br",
    "itau.com",
    "itauunibanco.com.br",
    "itau-unibanco.com.br",
    "bancoitau.com.br",
    "itauunibanco.com",
)
MAIL_DOMAIN_EXCEPTIONS: dict[str, list[tuple[str, str, str]]] = {
    site: [
        (
            _ITAU_MAIL,
            "subsidiary",
            "Itaú BBA / wholesale mail host — officers are often not on the retail website domain",
        )
    ]
    for site in _ITAU_SITES
}
MAIL_DOMAIN_EXCEPTIONS[_ITAU_MAIL] = [(_ITAU_MAIL, "canonical", "Itaú BBA mailbox host")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def preferred_mail_hosts_sync(website: str) -> list[str]:
    """Seed-only ranking. No I/O — used when a ReconcileContext is not loaded yet."""
    site = normalize_domain(website)
    if not site:
        return []
    hosts: list[str] = []
    for mail, _relation, _note in MAIL_DOMAIN_EXCEPTIONS.get(site, []):
        host = normalize_domain(mail)
        if host and host not in hosts:
            hosts.append(host)
    if site not in hosts:
        hosts.append(site)
    return hosts


def _score(row: dict[str, Any]) -> float:
    relation = str(row.get("relation") or "")
    score = float(row.get("confidence") or 0.5)
    if relation in {"subsidiary", "seed", "legal"}:
        score += 0.25
    elif relation == "observed":
        score += 0.15
    elif relation == "canonical":
        score += 0.05
    score += min(0.4, int(row.get("reply_count") or 0) * 0.1)
    score += min(0.2, int(row.get("sample_count") or 0) * 0.03)
    score -= min(0.5, int(row.get("bounce_count") or 0) * 0.08)
    if int(row.get("hard_to_reach") or 0):
        score -= 0.2
    if int(row.get("catch_all") or 0):
        score -= 0.15
    if row.get("mx_ok") == 0:
        score -= 1.0
    elif row.get("mx_ok") == 1:
        score += 0.12
    return score


async def ensure_mail_domain_schema(db) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS company_mail_domains (
            website_domain TEXT NOT NULL,
            mail_domain TEXT NOT NULL,
            relation TEXT NOT NULL DEFAULT 'observed',
            company_name TEXT,
            mx_ok INTEGER,
            catch_all INTEGER,
            hard_to_reach INTEGER NOT NULL DEFAULT 0,
            sample_count INTEGER NOT NULL DEFAULT 0,
            bounce_count INTEGER NOT NULL DEFAULT 0,
            reply_count INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0.5,
            sources_json TEXT NOT NULL DEFAULT '[]',
            note TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (website_domain, mail_domain)
        );
        CREATE INDEX IF NOT EXISTS idx_company_mail_domains_mail
            ON company_mail_domains(mail_domain);
        """
    )
    columns = {
        row["name"]
        for row in await (await db.execute("PRAGMA table_info(company_mail_domains)")).fetchall()
    }
    if columns and "catch_all" not in columns:
        await db.execute("ALTER TABLE company_mail_domains ADD COLUMN catch_all INTEGER")
    await _seed(db)


async def _seed(db) -> None:
    now = _now()
    for website, entries in MAIL_DOMAIN_EXCEPTIONS.items():
        for mail_domain, relation, note in entries:
            await db.execute(
                """INSERT INTO company_mail_domains(
                       website_domain, mail_domain, relation, hard_to_reach,
                       confidence, sources_json, note, updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(website_domain, mail_domain) DO NOTHING""",
                (
                    website,
                    mail_domain,
                    relation,
                    0,
                    0.82 if website != mail_domain else 0.55,
                    json.dumps(["seed"]),
                    note,
                    now,
                ),
            )
        if website not in {mail for mail, _, _ in entries}:
            await db.execute(
                """INSERT INTO company_mail_domains(
                       website_domain, mail_domain, relation, hard_to_reach,
                       confidence, sources_json, note, updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(website_domain, mail_domain) DO NOTHING""",
                (
                    website,
                    website,
                    "canonical",
                    0,
                    0.28,
                    json.dumps(["seed"]),
                    "Public website host; try exception mailbox hosts first",
                    now,
                ),
            )


def _row_hosts(rows: list[dict[str, Any]], site: str) -> list[str]:
    ranked = sorted(rows, key=_score, reverse=True)
    hosts: list[str] = []
    for row in ranked:
        host = normalize_domain(row.get("mail_domain") or "")
        if host and host not in hosts and row.get("mx_ok") != 0:
            hosts.append(host)
    if site not in hosts:
        hosts.append(site)
    return hosts or [site]


async def list_mail_hosts(website: str, *, db=None) -> list[str]:
    """Preferred mailbox hosts for a website domain, best first. Always includes the website host."""
    site = normalize_domain(website)
    if not site:
        return []
    owns_db = db is None
    if owns_db:
        db = await get_db()
    try:
        await ensure_mail_domain_schema(db)
        cur = await db.execute(
            """SELECT website_domain, mail_domain, relation, mx_ok, catch_all, hard_to_reach,
                      sample_count, bounce_count, reply_count, confidence
               FROM company_mail_domains
               WHERE website_domain = ? OR mail_domain = ?""",
            (site, site),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        if owns_db:
            await db.close()
    if not rows:
        return preferred_mail_hosts_sync(site)
    return _row_hosts(rows, site)


async def list_mail_domain_rows(website: str) -> list[dict[str, Any]]:
    site = normalize_domain(website)
    if not site:
        return []
    db = await get_db()
    try:
        await ensure_mail_domain_schema(db)
        cur = await db.execute(
            """SELECT website_domain, mail_domain, relation, mx_ok, catch_all, hard_to_reach,
                      sample_count, bounce_count, reply_count, confidence, note, updated_at
               FROM company_mail_domains WHERE website_domain = ? OR mail_domain = ?""",
            (site, site),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    return sorted(rows, key=_score, reverse=True)


async def record_observed_alias(
    website: str,
    mail_domain: str,
    *,
    source: str = "scrape",
    company_name: str | None = None,
    db=None,
) -> None:
    site = normalize_domain(website)
    mail = normalize_domain(mail_domain)
    if not site or not mail or site == mail:
        return
    owns_db = db is None
    if owns_db:
        db = await get_db()
    try:
        await ensure_mail_domain_schema(db)
        cur = await db.execute(
            "SELECT sample_count, sources_json, reply_count, bounce_count FROM company_mail_domains WHERE website_domain=? AND mail_domain=?",
            (site, mail),
        )
        row = await cur.fetchone()
        sources: list[str] = []
        if row:
            try:
                sources = json.loads(row["sources_json"] or "[]")
            except Exception:
                sources = []
            if source not in sources:
                sources.append(source)
            samples = int(row["sample_count"] or 0) + 1
            confidence = min(
                0.98,
                0.4
                + samples * 0.04
                + int(row["reply_count"] or 0) * 0.08
                - int(row["bounce_count"] or 0) * 0.06,
            )
            await db.execute(
                """UPDATE company_mail_domains SET
                       sample_count=?, confidence=?, sources_json=?,
                       company_name=COALESCE(?, company_name),
                       relation=CASE WHEN relation IN ('seed','subsidiary','legal','canonical') THEN relation ELSE 'observed' END,
                       updated_at=?
                   WHERE website_domain=? AND mail_domain=?""",
                (samples, confidence, json.dumps(sources[-20:]), company_name, _now(), site, mail),
            )
        else:
            await db.execute(
                """INSERT INTO company_mail_domains(
                       website_domain, mail_domain, relation, company_name, hard_to_reach,
                       sample_count, confidence, sources_json, note, updated_at
                   ) VALUES(?,?,?,?,0,1,?,?,?,?)""",
                (
                    site,
                    mail,
                    "observed",
                    company_name,
                    0.62,
                    json.dumps([source]),
                    "Observed mailbox host differs from the public website domain",
                    _now(),
                ),
            )
        if owns_db:
            await db.commit()
    finally:
        if owns_db:
            await db.close()


async def record_mailbox_outcome(
    email: str,
    *,
    kind: str,
    website_domain: str | None = None,
    db=None,
) -> None:
    """kind: bounce | reply | delivered | catch_all"""
    mail = normalize_domain((email or "").rsplit("@", 1)[-1] if "@" in (email or "") else "")
    site = normalize_domain(website_domain or "") or mail
    if not mail or not site:
        return
    owns_db = db is None
    if owns_db:
        db = await get_db()
    try:
        await ensure_mail_domain_schema(db)
        await db.execute(
            """INSERT INTO company_mail_domains(
                   website_domain, mail_domain, relation, sample_count, bounce_count, reply_count,
                   hard_to_reach, catch_all, confidence, sources_json, note, updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(website_domain, mail_domain) DO NOTHING""",
            (
                site,
                mail,
                "observed" if site != mail else "canonical",
                0,
                0,
                0,
                1 if kind in {"catch_all", "bounce"} else 0,
                1 if kind == "catch_all" else None,
                0.4,
                json.dumps([kind]),
                None,
                _now(),
            ),
        )
        bounce_delta = 1 if kind == "bounce" else 0
        reply_delta = 1 if kind == "reply" else 0
        hard = 1 if kind in {"catch_all", "bounce"} else 0
        catch = 1 if kind == "catch_all" else 0
        conf_delta = {"bounce": -0.1, "reply": 0.12, "delivered": 0.04, "catch_all": -0.2}.get(kind, 0)
        await db.execute(
            """UPDATE company_mail_domains SET
                   bounce_count = bounce_count + ?,
                   reply_count = reply_count + ?,
                   sample_count = sample_count + 1,
                   hard_to_reach = CASE
                       WHEN ? = 1 THEN 1
                       WHEN ? > 0 THEN 0
                       ELSE hard_to_reach
                   END,
                   catch_all = CASE WHEN ? = 1 THEN 1 ELSE catch_all END,
                   confidence = MIN(0.98, MAX(0.05, confidence + ?)),
                   updated_at = ?
               WHERE website_domain=? AND mail_domain=?""",
            (bounce_delta, reply_delta, hard, reply_delta, catch, conf_delta, _now(), site, mail),
        )
        if owns_db:
            await db.commit()
    finally:
        if owns_db:
            await db.close()


async def mark_catch_all(domain: str, *, db=None) -> None:
    host = normalize_domain(domain)
    if not host:
        return
    await record_mailbox_outcome(f"probe@{host}", kind="catch_all", website_domain=host, db=db)


async def store_mx_result(domain: str, mx_ok: bool | None, *, db=None) -> None:
    host = normalize_domain(domain)
    if not host or mx_ok is None:
        return
    owns_db = db is None
    if owns_db:
        db = await get_db()
    try:
        await db.execute(
            "UPDATE company_mail_domains SET mx_ok=?, updated_at=? WHERE mail_domain=?",
            (1 if mx_ok else 0, _now(), host),
        )
        if owns_db:
            await db.commit()
    finally:
        if owns_db:
            await db.close()
