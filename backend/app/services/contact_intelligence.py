"""Canonical evidence, independent confidence dimensions, and access-scoped snapshots.

Ingestion is local and uses the caller's transaction. Resolve network assessments
before beginning that transaction; neither ingestion nor observation commits it.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from app.services.contact_scraper import looks_like_person_name, normalize_domain


class Identity(StrEnum):
    UNREVIEWED = 'unreviewed'
    PLAUSIBLE = 'plausible'
    CORROBORATED = 'corroborated'
    CONFLICTED = 'conflicted'
    REJECTED = 'rejected'


class Employment(StrEnum):
    CURRENT_SOURCE_OBSERVED = 'current_source_observed'
    CURRENT_INFERRED = 'current_inferred'
    STALE = 'stale'
    FORMER = 'former'
    UNKNOWN = 'unknown'


class AddressOrigin(StrEnum):
    PUBLISHED_BY_COMPANY = 'published_by_company'
    PUBLISHED_BY_INDEPENDENT_SOURCE = 'published_by_independent_source'
    INFERRED_FROM_PUBLISHED_PATTERN = 'inferred_from_published_pattern'
    USER_SUPPLIED = 'user_supplied'
    IMPORTED_WITHOUT_EVIDENCE = 'imported_without_evidence'


class Mailbox(StrEnum):
    NOT_CHECKED = 'not_checked'
    BAD_SYNTAX = 'bad_syntax'
    DOMAIN_HAS_NO_MAIL_ROUTE = 'domain_has_no_mail_route'
    MAIL_ROUTE_AVAILABLE = 'mail_route_available'
    PROVIDER_HIGH_CONFIDENCE = 'provider_high_confidence'
    PROVIDER_MEDIUM_CONFIDENCE = 'provider_medium_confidence'
    ACCEPT_ALL_OR_RISKY = 'accept_all_or_risky'
    RECIPIENT_REJECTED = 'recipient_rejected'
    INCONCLUSIVE = 'inconclusive'
    PREVIOUSLY_DELIVERED = 'previously_delivered'
    HUMAN_REPLY_OBSERVED = 'human_reply_observed'
    PERMANENT_FAILURE_OBSERVED = 'permanent_failure_observed'


class ProjectFit(StrEnum):
    STRONG = 'strong'
    POSSIBLE = 'possible'
    WEAK = 'weak'
    EXCLUDED = 'excluded'


class OutreachState(StrEnum):
    NEVER_CONTACTED = 'never_contacted'
    DRAFTED = 'drafted'
    SCHEDULED = 'scheduled'
    SENT = 'sent'
    DELIVERY_DELAYED = 'delivery_delayed'
    DELIVERY_FAILED = 'delivery_failed'
    OPEN_DETECTED = 'open_detected'
    AUTOMATIC_REPLY = 'automatic_reply'
    HUMAN_REPLY = 'human_reply'
    UNSUBSCRIBED = 'unsubscribed'
    SUPPRESSED = 'suppressed'


ROLE_LOCALS = frozenset({'info', 'support', 'sales', 'admin', 'contact', 'hello', 'office', 'careers', 'jobs', 'hr', 'press', 'marketing', 'noreply', 'no-reply', 'recruiting'})
CONSUMER_DOMAINS = frozenset({'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com', 'aol.com'})
DISPOSABLE_DOMAINS = frozenset({'mailinator.com', 'guerrillamail.com', 'tempmail.com', '10minutemail.com', 'yopmail.com'})


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalized_name(value):
    return ' '.join(re.findall(r'[^\W\d_]+', unicodedata.normalize('NFKC', value or '').casefold()))


def parsed_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def safe_source_url(value):
    try:
        url = urlsplit(str(value or ''))
        host = url.hostname or ''
        if url.scheme not in ('https', 'http') or not host or url.username or url.password:
            return None
        if host in ('localhost', 'metadata.google.internal') or host.endswith(('.local', '.internal')):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if '.' not in host:
                return None
        return urlunsplit((url.scheme, url.netloc.lower(), url.path, url.query, ''))[:2048]
    except ValueError:
        return None


def mailbox_from_legacy(result):
    """Compatibility only: a historic valid flag never proves an individual mailbox."""
    reason = str(result.get('reason') or '')
    if reason in ('bad_format', 'bad_local'):
        return Mailbox.BAD_SYNTAX.value
    if reason == 'no_mx':
        return Mailbox.DOMAIN_HAS_NO_MAIL_ROUTE.value
    if reason == 'recipient_rejected_5.1.1' or result.get('smtp_probe') == 'invalid':
        return Mailbox.RECIPIENT_REJECTED.value
    if result.get('catch_all') is True or result.get('smtp_probe') == 'catch_all':
        return Mailbox.ACCEPT_ALL_OR_RISKY.value
    if result.get('smtp_probe') == 'accepted' and result.get('catch_all') is False:
        return Mailbox.PROVIDER_MEDIUM_CONFIDENCE.value
    if result.get('smtp_probe') == 'accepted':
        return Mailbox.ACCEPT_ALL_OR_RISKY.value
    if result.get('mx_valid') is True:
        return Mailbox.MAIL_ROUTE_AVAILABLE.value
    return Mailbox.INCONCLUSIVE.value


def recommendation_state(identity, employment, mailbox, fit, conflicts=(), suppressed=False):
    if suppressed or identity == 'rejected' or employment == 'former' or fit == 'excluded' or mailbox in ('bad_syntax', 'domain_has_no_mail_route', 'recipient_rejected', 'permanent_failure_observed'):
        return 'excluded'
    if identity == 'corroborated' and employment == 'current_source_observed' and fit in ('strong', 'possible') and not conflicts and mailbox in ('mail_route_available', 'provider_high_confidence', 'provider_medium_confidence', 'previously_delivered', 'human_reply_observed'):
        return 'ready_to_review'
    return 'needs_evidence'


def _source_facts(source, contact):
    excerpt = normalized_name(source['excerpt'])
    facts = []
    for fact, field in (('identity', 'name'), ('employment', 'company'), ('role', 'title')):
        value = normalized_name(contact.get(field))
        if value and value in excerpt:
            facts.append(fact)
    # Claims supplied by a model do not substitute for the observed excerpt.
    if re.search(r'\b(former|previously|ex employee|left the company)\b', excerpt):
        facts.append('former')
    return facts


async def ingest_contact(db, *, contact: dict, actor_id: int, project_id: int | None = None,
                         origin: str = 'imported_without_evidence', sources: list | None = None) -> dict:
    email = str(contact.get('email') or '').strip().lower()
    name = str(contact.get('name') or '').strip()[:250]
    company = str(contact.get('company') or '').strip()[:250]
    domain = normalize_domain(contact.get('domain') or contact.get('company_domain') or (email.rpartition('@')[2] if '@' in email else ''))
    current = now_iso()
    org_key = domain or normalized_name(company) or 'unknown'
    await db.execute('INSERT INTO organizations(canonical_key,name,domain,created_at) VALUES(?,?,?,?) ON CONFLICT(canonical_key) DO NOTHING', (org_key, company or domain or 'Unknown', domain, current))
    org = await (await db.execute('SELECT id FROM organizations WHERE canonical_key=?', (org_key,))).fetchone()
    profile = safe_source_url(contact.get('profile_url') or contact.get('linkedin_url'))
    # Exact address wins; otherwise normalized person+organization joins alternatives.
    existing = await (await db.execute('SELECT person_id,id FROM email_candidates WHERE email=?', (email,))).fetchone() if email else None
    person_key = f'{org["id"]}:{normalized_name(name)}' if normalized_name(name) else f'address:{email}'
    if existing:
        person_id = existing['person_id']
    else:
        await db.execute('''INSERT INTO people(canonical_key,organization_id,name,normalized_name,title,profile_url,created_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(canonical_key) DO NOTHING''', (person_key, org['id'], name, normalized_name(name), contact.get('title'), profile, current))
        person_id = (await (await db.execute('SELECT id FROM people WHERE canonical_key=?', (person_key,))).fetchone())['id']
    origin = origin if origin in AddressOrigin._value2member_map_ else 'imported_without_evidence'
    source_ids = []
    published_company = published_independent = False
    incoming = list(sources or contact.get('sources') or [])
    if not incoming:
        excerpt = str(contact.get('discovery_context') or contact.get('source_excerpt') or '').strip()
        url = contact.get('source_url') or contact.get('profile_url') or contact.get('linkedin_url')
        if excerpt and url:
            incoming = [{'url': url, 'excerpt': excerpt, 'observed_at': contact.get('observed_at') or now_iso()}]
    for item in incoming[:30]:
        if not isinstance(item, dict):
            continue
        url = safe_source_url(item.get('url') or item.get('source_url'))
        excerpt = str(item.get('excerpt') or '').strip()[:2000]
        observed = parsed_time(item.get('observed_at')) or datetime.now(timezone.utc)
        if not url or not excerpt or observed > datetime.now(timezone.utc) + timedelta(minutes=5):
            continue
        source = {**item, 'excerpt': excerpt}
        facts = _source_facts(source, contact)
        if email and email in excerpt.lower():
            facts.append('address')
            host = urlsplit(url).hostname or ''
            published_company |= host == domain or host.endswith('.' + domain) if domain else False
            published_independent |= not (host == domain or host.endswith('.' + domain)) if domain else True
        # Public sharing must be explicit at trusted server callsites, never inherited from imported cells.
        scope = 'public' if item.get('scope') == 'public' else 'private'
        digest = hashlib.sha256(excerpt.encode()).hexdigest()
        expires = (observed + timedelta(days=180)).isoformat()
        await db.execute('''INSERT INTO person_evidence(person_id,owner_id,project_id,access_scope,source_url,source_type,excerpt,observed_at,content_hash,facts_json,method,expires_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(person_id,owner_id,source_url,content_hash) DO UPDATE SET observed_at=excluded.observed_at,expires_at=excluded.expires_at''',
            (person_id, actor_id, project_id, scope, url, str(item.get('source_type') or 'public_page')[:80], excerpt, observed.isoformat(), digest, json.dumps(facts), str(item.get('method') or 'source_excerpt')[:80], expires))
        source_ids.append((await (await db.execute('SELECT id FROM person_evidence WHERE person_id=? AND owner_id=? AND source_url=? AND content_hash=?', (person_id, actor_id, url, digest))).fetchone())['id'])
    if published_company:
        origin = 'published_by_company'
    elif published_independent:
        origin = 'published_by_independent_source'
    elif origin.startswith('published_'):
        origin = 'imported_without_evidence'
    if origin == 'inferred_from_published_pattern':
        learned = await (await db.execute('SELECT 1 FROM email_pattern_samples WHERE company_domain=? LIMIT 1', (domain,))).fetchone()
        if not learned:
            origin = 'imported_without_evidence'
    candidate_id = None
    if email:
        await db.execute('''INSERT INTO email_candidates(person_id,email,company_domain,origin,pattern_key,selected,method,created_at)
            VALUES(?,?,?,?,?,0,?,?) ON CONFLICT(email) DO NOTHING''', (person_id, email, domain, origin, contact.get('email_pattern'), 'ingestion', current))
        candidate_id = (await (await db.execute('SELECT id FROM email_candidates WHERE email=?', (email,))).fetchone())['id']
        await db.execute('UPDATE email_candidates SET selected=1 WHERE id=? AND NOT EXISTS(SELECT 1 FROM email_candidates WHERE person_id=? AND selected=1)', (candidate_id, person_id))
        assessment = contact.get('mailbox_assessment')
        if isinstance(assessment, dict) and assessment.get('mailbox') in Mailbox._value2member_map_:
            await _store_check(db, candidate_id, actor_id, assessment)
        if contact.get('id'):
            await db.execute('''INSERT INTO catalog_evidence(contact_id,person_id,candidate_id) VALUES(?,?,?)
                ON CONFLICT(contact_id) DO UPDATE SET person_id=excluded.person_id,candidate_id=excluded.candidate_id''', (contact['id'], person_id, candidate_id))
        if published_company or published_independent:
            sample_url = next((safe_source_url(i.get('url') or i.get('source_url')) for i in incoming if isinstance(i, dict)), None) or ''
            await db.execute("""INSERT INTO email_pattern_samples(company_domain,email,pattern_key,source_url,observed_at,provenance)
                VALUES(?,?,?,?,?,?) ON CONFLICT(company_domain,email) DO NOTHING""",
                (domain or '', email, str(contact.get('email_pattern') or '')[:80], sample_url[:2048], current, origin))

    visible = await _sources(db, person_id, actor_id)
    identity, employment = _person_dimensions(name, company, visible)
    conflicts = [str(c)[:300] for c in (contact.get('conflicts') or [])[:20]]
    if existing:
        old = await (await db.execute('SELECT normalized_name FROM people WHERE id=?', (person_id,))).fetchone()
        if old['normalized_name'] and old['normalized_name'] != normalized_name(name):
            conflicts.append('Address is already associated with a different person name')
    if conflicts:
        identity = 'conflicted'
    if email.partition('@')[0] in ROLE_LOCALS or domain in CONSUMER_DOMAINS | DISPOSABLE_DOMAINS:
        identity = 'rejected'
        conflicts.append('Generic or non-professional address')
    fit = contact.get('project_fit', 'weak')
    if fit not in ProjectFit._value2member_map_:
        fit = 'weak'
    disposition = contact.get('disposition', 'pending')
    if disposition not in ('pending', 'accepted', 'rejected'):
        disposition = 'pending'
    await db.execute('''INSERT INTO person_assessments(person_id,actor_id,project_key,identity,employment,project_fit,reason,conflicts_json,source_ids_json,checked_at,expires_at,disposition)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(person_id,actor_id,project_key) DO UPDATE SET
        identity=excluded.identity,employment=excluded.employment,project_fit=CASE WHEN excluded.project_fit='weak' THEN person_assessments.project_fit ELSE excluded.project_fit END,
        reason=excluded.reason,conflicts_json=excluded.conflicts_json,source_ids_json=excluded.source_ids_json,checked_at=excluded.checked_at,expires_at=excluded.expires_at,
        disposition=CASE WHEN excluded.disposition='pending' THEN person_assessments.disposition ELSE excluded.disposition END''',
        (person_id, actor_id, project_id or 0, identity, employment, fit, str(contact.get('reason') or 'Review dated person, employer and address evidence')[:1000], json.dumps(conflicts), json.dumps([s['id'] for s in visible]), current, (datetime.now(timezone.utc)+timedelta(days=180)).isoformat(), disposition))
    return await _snapshot(db, person_id, candidate_id, actor_id, project_id)


async def _sources(db, person_id, actor_id):
    rows = await (await db.execute('''SELECT * FROM person_evidence WHERE person_id=? AND superseded=0
        AND (owner_id=? OR access_scope='public') ORDER BY observed_at DESC,id DESC''', (person_id, actor_id))).fetchall()
    return [dict(id=r['id'], url=r['source_url'], excerpt=r['excerpt'], observed_at=r['observed_at'],
                 expires_at=r['expires_at'], method=r['method'], facts=json.loads(r['facts_json'])) for r in rows]


def _person_dimensions(name, company, sources):
    if not looks_like_person_name(name, company):
        return 'rejected', 'unknown'
    current = datetime.now(timezone.utc)
    relevant = [s for s in sources if 'identity' in s['facts']]
    complete = [s for s in relevant if {'identity', 'employment', 'role'} <= set(s['facts'])]
    fresh = [s for s in complete if parsed_time(s['expires_at']) and parsed_time(s['expires_at']) > current]
    if fresh and any('former' in s['facts'] for s in fresh):
        return ('conflicted' if any('former' not in s['facts'] for s in fresh) else 'corroborated'), 'former'
    if fresh:
        return 'corroborated', 'current_source_observed'
    if complete:
        return 'plausible', 'stale'
    return 'plausible', 'current_inferred' if company else 'unknown'


async def _store_check(db, candidate_id, actor_id, result, event_key=None):
    state = result['mailbox']
    if state not in Mailbox._value2member_map_:
        raise ValueError('Unknown mailbox state')
    await db.execute('''INSERT INTO email_checks(candidate_id,actor_id,verifier,check_type,result,reason,source_ids_json,checked_at,expires_at,cost_units,provider_request_id,event_key)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING''',
        (candidate_id, actor_id, result.get('method', 'local'), result.get('method', 'local'), state,
         str(result.get('reason') or 'Assessment recorded')[:500], json.dumps(result.get('source_ids') or []), result.get('checked_at') or now_iso(), result.get('expires_at'), int(result.get('cost_units') or 0), result.get('provider_request_id'), event_key))


async def _snapshot(db, person_id, candidate_id, actor_id, project_id=None):
    person = await (await db.execute('SELECT p.*,o.name AS company FROM people p LEFT JOIN organizations o ON o.id=p.organization_id WHERE p.id=?', (person_id,))).fetchone()
    sources = await _sources(db, person_id, actor_id)
    identity, employment = _person_dimensions(person['name'], person['company'], sources)
    query = 'SELECT * FROM person_assessments WHERE person_id=? AND actor_id=?'
    args = [person_id, actor_id]
    if project_id is not None:
        query += ' AND project_key=?'
        args.append(project_id)
    assessment = await (await db.execute(query + ' ORDER BY checked_at DESC LIMIT 1', args)).fetchone()
    fit = assessment['project_fit'] if assessment else 'weak'
    conflicts = json.loads(assessment['conflicts_json']) if assessment else []
    if assessment and assessment['identity'] in ('rejected', 'conflicted'):
        identity = assessment['identity']
    if identity == 'conflicted' and not conflicts:
        conflicts = ['Conflicting current and former employment sources']
    origin = 'imported_without_evidence'
    mailbox = Mailbox.NOT_CHECKED.value
    check = None
    suppressed = False
    if candidate_id:
        candidate = await (await db.execute('SELECT * FROM email_candidates WHERE id=?', (candidate_id,))).fetchone()
        if candidate:
            origin = candidate['origin']
            suppressed = bool(await (await db.execute(
                'SELECT 1 FROM candidate_suppressions WHERE email=? COLLATE NOCASE', (candidate['email'],))).fetchone())
        check = await (await db.execute(
            'SELECT * FROM email_checks WHERE candidate_id=? ORDER BY checked_at DESC, id DESC LIMIT 1',
            (candidate_id,))).fetchone()
        if check:
            mailbox = check['result']
    catalog = None
    if candidate_id:
        catalog = await (await db.execute(
            'SELECT contact_id FROM catalog_evidence WHERE person_id=? AND candidate_id=?',
            (person_id, candidate_id))).fetchone()
    disposition = assessment['disposition'] if assessment else 'pending'
    state = recommendation_state(identity, employment, mailbox, fit, conflicts, suppressed)
    return dict(
        person_id=person_id, candidate_id=candidate_id,
        contact_id=catalog['contact_id'] if catalog else None,
        identity=identity, employment=employment,
        address_origin=origin, mailbox=mailbox, project_fit=fit,
        checked_at=check['checked_at'] if check else (assessment['checked_at'] if assessment else now_iso()),
        method=check['verifier'] if check else 'source_review',
        source_ids=[s['id'] for s in sources],
        reason=check['reason'] if check else (assessment['reason'] if assessment else 'No mailbox check has been recorded'),
        expires_at=check['expires_at'] if check else (assessment['expires_at'] if assessment else None),
        sources=sources, conflicts=conflicts, recommendation_state=state, disposition=disposition,
        accepted_source_ids=[s['id'] for s in sources] if disposition == 'accepted' else [],
        provider_state='cached' if check and check['verifier'] == 'verifalia' else 'not_requested',
    )


async def contact_evidence(db, contact_id: int, actor_id: int):
    """Access-scoped snapshot for a catalog contact the caller can already read."""
    row = await (await db.execute(
        'SELECT person_id, candidate_id FROM catalog_evidence WHERE contact_id=?', (contact_id,))).fetchone()
    if not row:
        return None
    return await _snapshot(db, row['person_id'], row['candidate_id'], actor_id)


async def assess_address(email: str, *, actor_id: int, external: bool = False, manual: bool = False, reason: str | None = None):
    """Lazy import keeps verification out of the ingestion import cycle."""
    from app.services.email_verification import assess_address as _assess
    return await _assess(email, actor_id=actor_id, external=external, manual=manual, reason=reason)


async def address_is_suppressed(db, email: str) -> bool:
    row = await (await db.execute(
        'SELECT 1 FROM candidate_suppressions WHERE email=? COLLATE NOCASE',
        ((email or '').strip().lower(),))).fetchone()
    return bool(row)


async def record_mailbox_event(db, candidate_id: int, actor_id: int, state: str, reason: str, event_key=None) -> None:
    """Opens and delays never prove a mailbox; only replies and permanent failures do."""
    if state not in (Mailbox.HUMAN_REPLY_OBSERVED.value, Mailbox.PERMANENT_FAILURE_OBSERVED.value, Mailbox.PREVIOUSLY_DELIVERED.value):
        return
    key = f'mailbox:{event_key}:{state}' if event_key else None
    from app.services.roster_email import apply_mailbox_proof

    await apply_mailbox_proof(db, candidate_id, state)
    await _store_check(db, candidate_id, actor_id, {
        'mailbox': state, 'method': 'message_tracking', 'reason': reason,
        'checked_at': now_iso(), 'expires_at': None, 'cost_units': 0,
    }, event_key=key)
    if state == Mailbox.PERMANENT_FAILURE_OBSERVED.value:
        cand = await (await db.execute('SELECT email FROM email_candidates WHERE id=?', (candidate_id,))).fetchone()
        if cand:
            await db.execute("""INSERT INTO candidate_suppressions(email,state,observed_at) VALUES(?,?,?)
                ON CONFLICT(email) DO UPDATE SET state=excluded.state, observed_at=excluded.observed_at""",
                (cand['email'], 'permanent_failure', now_iso()))
