"""
Email deliverability checks — MX required; optional SMTP RCPT probe (best-effort).
Parallel-friendly: shared MX cache, configurable worker timeouts.
"""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import smtplib
import socket
import ipaddress
from datetime import datetime, timezone
from typing import Any

import dns.resolver

from app.services.company_email_cache import pattern_for_email

SMTP_PROBE_TIMEOUT = float(os.getenv("SMTP_PROBE_TIMEOUT", "1.5"))
MX_PREFETCH_WORKERS = int(os.getenv("MX_PREFETCH_WORKERS", "24"))
VERIFY_AGENTS = int(os.getenv("VERIFY_AGENTS", os.getenv("RECONCILE_WORKERS", "20")))
SMTP_MAX_CONCURRENT = int(os.getenv("SMTP_MAX_CONCURRENT", "6"))
# mx = MX lookup only (fast). auto = skip SMTP for trusted aligned scrapes. smtp = RCPT probe when requested.
INBOX_VERIFY_MODE = os.getenv("INBOX_VERIFY_MODE", "mx").strip().lower()
_smtp_sem = asyncio.Semaphore(max(1, SMTP_MAX_CONCURRENT))


def _effective_smtp_probe(requested: bool, *, force: bool = False) -> bool:
    if not requested:
        return False
    if force:
        return True
    if INBOX_VERIFY_MODE == "mx":
        return False
    if INBOX_VERIFY_MODE == "smtp":
        return True
    return requested if INBOX_VERIFY_MODE == 'auto' else False


async def detect_catch_all(
    domain: str,
    mx_host: str,
    timeout: float,
    cache: dict[str, str] | None = None,
) -> str:
    """Probe a random local-part. catch_all | selective | unknown."""
    cache = cache if cache is not None else {}
    if domain in cache:
        return cache[domain]
    fake = f"nombox.{secrets.token_hex(6)}@{domain}"
    probe = await _smtp_rcpt_probe(fake, mx_host, timeout)
    if probe == "accepted":
        verdict = "catch_all"
    elif probe == "invalid":
        verdict = "selective"
    else:
        verdict = "unknown"
    cache[domain] = verdict
    return verdict


async def verify_mx(domain: str) -> tuple[bool | None, list[str]]:
    try:
        answers = await asyncio.to_thread(dns.resolver.resolve, domain, "MX", lifetime=3)
        if any(str(r.exchange) == '.' for r in answers):
            return False, []  # RFC 7505 null MX: domain explicitly accepts no mail.
        hosts = [str(r.exchange).rstrip('.') for r in sorted(answers, key=lambda r: r.preference)]
        return bool(hosts), hosts
    except dns.resolver.NXDOMAIN:
        return False, []
    except dns.resolver.NoAnswer:
        # RFC 5321 implicit MX: use domain A/AAAA when there is no MX record.
        for kind in ('A', 'AAAA'):
            try:
                await asyncio.to_thread(dns.resolver.resolve, domain, kind, lifetime=3)
                return True, [domain]
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                continue
            except Exception:
                return None, []
        return False, []
    except Exception:
        return None, []  # DNS failure is inconclusive, not an invalid mailbox.


async def get_mx_cached(
    domain: str,
    cache: dict[str, tuple[bool, list[str]]] | None = None,
) -> tuple[bool, list[str]]:
    if cache is not None and domain in cache:
        return cache[domain]
    result = await verify_mx(domain)
    if cache is not None:
        cache[domain] = result
    return result


async def preload_mx_for_domains(
    domains: set[str],
    cache: dict[str, tuple[bool, list[str]]],
    *,
    workers: int | None = None,
) -> None:
    """Resolve MX for all domains in parallel (one lookup per domain)."""
    missing = {d for d in domains if d and d not in cache}
    if not missing:
        return
    sem = asyncio.Semaphore(workers or MX_PREFETCH_WORKERS)

    async def _one(dom: str) -> None:
        async with sem:
            cache[dom] = await verify_mx(dom)

    await asyncio.gather(*[_one(d) for d in missing])


async def _smtp_rcpt_probe(email: str, mx_host: str, timeout: float) -> str:
    """
    Best-effort RCPT TO. Returns: valid | invalid | unknown
    Many corporate servers greylist or accept-all — treat ambiguous as unknown.
    """

    def _probe() -> str:
        try:
            ips = [item[4][0] for item in socket.getaddrinfo(mx_host, 25, type=socket.SOCK_STREAM)]
            if not ips or not all(ipaddress.ip_address(ip).is_global for ip in ips):
                return 'unknown'
            with smtplib.SMTP(timeout=timeout) as smtp:
                smtp.connect(ips[0], 25)  # Pin the checked IP to prevent DNS rebinding.
                smtp.helo(socket.gethostname() or "clientreach.local")
                smtp.mail("verify@clientreach.local")
                code, response = smtp.rcpt(email)
                smtp.rset()  # Never issue DATA or send a message.
                if 200 <= code < 300:
                    return "accepted"  # Includes accept-all servers.
                if 500 <= code < 600 and re.search(rb'\b5\.1\.1\b', response):
                    return "invalid"
                return "unknown"
        except smtplib.SMTPServerDisconnected:
            return "unknown"
        except socket.timeout:
            return "unknown"
        except OSError:
            return "unknown"
        except Exception:
            return "unknown"

    try:
        async with _smtp_sem:
            return await asyncio.wait_for(asyncio.to_thread(_probe), timeout=timeout + 0.75)
    except Exception:
        return "unknown"


def verify_email_format(email: str) -> dict[str, Any]:
    """Syntax only. Used by GET /outreach/verify-email. Not inbox existence."""
    raw = (email or "").strip().lower()
    if not raw or raw.count('@') != 1 or len(raw) > 254:
        return {"valid": False, "reason": "Invalid format"}
    local, _, domain = raw.partition("@")
    if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local or not re.match(r"^[a-z0-9._+-]+$", local):
        return {"valid": False, "reason": "Invalid local part"}
    if "." not in domain or any(not label or label.startswith('-') or label.endswith('-') or len(label) > 63 for label in domain.split('.')) or not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        return {"valid": False, "reason": "Invalid domain"}
    return {"valid": True}


async def verify_email_deliverability(
    email: str,
    *,
    full_name: str | None = None,
    smtp_probe: bool = True,
    smtp_timeout: float | None = None,
    mx_cache: dict[str, tuple[bool, list[str]]] | None = None,
    catch_all_cache: dict[str, str] | None = None,
    force_smtp: bool = False,
) -> dict[str, Any]:
    """
    Returns status: valid | likely_valid | invalid | unknown
    """
    email = (email or "").strip().lower()
    if not verify_email_format(email)['valid']:
        return {"status": "invalid", "mx_valid": False, "reason": "bad_format", "catch_all": None}

    local, domain = email.rsplit("@", 1)
    if not re.match(r"^[a-z0-9._+-]+$", local):
        return {"status": "invalid", "mx_valid": False, "reason": "bad_local", "catch_all": None}

    mx_valid, mx_hosts = await get_mx_cached(domain, mx_cache)
    if mx_valid is None:
        return {"status": "unknown", "mx_valid": None, "reason": "dns_unavailable", "mailbox_exists": None, "catch_all": None}
    if not mx_valid:
        return {"status": "invalid", "mx_valid": False, "reason": "no_mx", "catch_all": None}

    result: dict[str, Any] = {
        "status": "likely_valid",
        "mx_valid": True,
        "mx_hosts": mx_hosts[:3],
        "matched_pattern": pattern_for_email(email, full_name) if full_name else None,
        "smtp_probe": None,
        "mailbox_exists": None,
        "catch_all": None,
        "reason": "mail_route_found_mailbox_unconfirmed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    if not _effective_smtp_probe(smtp_probe, force=force_smtp) or not mx_hosts:
        return result

    timeout = smtp_timeout if smtp_timeout is not None else SMTP_PROBE_TIMEOUT
    catch = await detect_catch_all(domain, mx_hosts[0], timeout, catch_all_cache)
    if catch == "catch_all":
        result["smtp_probe"] = "catch_all"
        result["catch_all"] = True
        result["reason"] = "domain_accepts_all_recipients"
        return result
    if catch == "selective":
        result["catch_all"] = False
    probe = await _smtp_rcpt_probe(email, mx_hosts[0], timeout)
    result["smtp_probe"] = probe
    if probe == "invalid":
        result["status"] = "invalid"
        result["reason"] = "recipient_rejected_5.1.1"
    elif probe == "accepted" and catch == "selective":
        result["reason"] = "recipient_accepted_not_catch_all"
    return result


def _verify_mx_only(
    email: str,
    full_name: str | None,
    cache: dict[str, tuple[bool, list[str]]],
) -> dict[str, Any]:
    email = (email or "").strip().lower()
    if not verify_email_format(email)['valid']:
        return {"status": "invalid", "mx_valid": False, "reason": "bad_format"}

    local, domain = email.rsplit("@", 1)
    if not re.match(r"^[a-z0-9._+-]+$", local):
        return {"status": "invalid", "mx_valid": False, "reason": "bad_local"}

    mx_valid, mx_hosts = cache.get(domain, (None, []))
    if mx_valid is None:
        return {"status": "unknown", "mx_valid": None, "reason": "dns_unavailable", "mailbox_exists": None}
    if not mx_valid:
        return {"status": "invalid", "mx_valid": False, "reason": "no_mx"}

    return {
        "status": "likely_valid",
        "mx_valid": True,
        "mx_hosts": mx_hosts[:3],
        "matched_pattern": pattern_for_email(email, full_name) if full_name else None,
        "smtp_probe": None,
        "mailbox_exists": None,
        "catch_all": None,
        "reason": "mail_route_found_mailbox_unconfirmed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def verify_emails_parallel(
    items: list[dict[str, Any]] | list[tuple[str, str | None]],
    *,
    smtp_probe: bool = True,
    smtp_timeout: float | None = None,
    workers: int | None = None,
    mx_cache: dict[str, tuple[bool, list[str]]] | None = None,
    on_progress: Any = None,
) -> list[dict[str, Any]]:
    """
    Verify many emails concurrently. Items are either:
    - dict with keys email, full_name (optional), smtp_probe (optional bool)
    - tuple (email, full_name)
    """
    if not items:
        return []

    normalized: list[tuple[str, str | None, bool]] = []
    for item in items:
        if isinstance(item, dict):
            email = (item.get("email") or "").strip().lower()
            name = item.get("full_name")
            probe = _effective_smtp_probe(bool(item.get("smtp_probe", smtp_probe)))
            normalized.append((email, name, probe))
        else:
            email, name = item
            normalized.append(((email or "").strip().lower(), name, _effective_smtp_probe(smtp_probe)))

    cache = mx_cache if mx_cache is not None else {}
    domains = {e.rsplit("@", 1)[1].lower() for e, _, _ in normalized if e and "@" in e}
    await preload_mx_for_domains(domains, cache)

    if not any(p for _, _, p in normalized):
        total = len(normalized)
        results = [_verify_mx_only(e, n, cache) for e, n, _ in normalized]
        if on_progress:
            maybe = on_progress(total, total)
            if maybe is not None:
                await maybe
        return results

    pool = workers or VERIFY_AGENTS
    sem = asyncio.Semaphore(pool)
    done = 0
    lock = asyncio.Lock()
    total = len(normalized)
    catch_cache: dict[str, str] = {}

    async def _one(email: str, full_name: str | None, probe: bool) -> dict[str, Any]:
        nonlocal done
        async with sem:
            result = await verify_email_deliverability(
                email,
                full_name=full_name,
                smtp_probe=probe,
                smtp_timeout=smtp_timeout,
                mx_cache=cache,
                catch_all_cache=catch_cache,
            )
            if on_progress:
                async with lock:
                    done += 1
                    current = done
                maybe = on_progress(current, total)
                if maybe is not None:
                    await maybe
            return result

    return await asyncio.gather(*[_one(e, n, p) for e, n, p in normalized])
