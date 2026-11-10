"""When a recipient is likely to be at their desk.

Mail that lands at 3am local time sits under the night's newsletters and is
read as a bulk send. The drain therefore claims a row only while it is
business hours where the recipient works: Monday to Friday, 08:00 up to
17:00, in that company's time zone. Held rows stay pending and go out on the
first tick after their window opens.

The zone comes from the company register, matched by entity key the same way
the register groups companies: a US record's state, or a UK record's country.
That is the filing address, not necessarily where the person sits, and a state
split across zones uses the zone most of its people live in. A company the
register does not place uses the contract's deterministic US Eastern default;
that fallback is an approximation, not evidence of where the person works.

SEND_WINDOW_HOURS="8-17" changes the hours; "off" removes the window (local
development, or a club that mails across the clock).
"""

from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

WINDOW_START_HOUR = 8
WINDOW_END_HOUR = 17
DEFAULT_ZONE = "America/New_York"

_EASTERN = "America/New_York"
_CENTRAL = "America/Chicago"
_MOUNTAIN = "America/Denver"
_PACIFIC = "America/Los_Angeles"

_US_STATE_ZONES = {
    **dict.fromkeys(
        "CT DE DC FL GA IN KY ME MD MA MI NH NJ NY NC OH PA RI SC VT VA WV".split(), _EASTERN),
    **dict.fromkeys(
        "AL AR IL IA KS LA MN MS MO NE ND OK SD TN TX WI".split(), _CENTRAL),
    **dict.fromkeys("CO ID MT NM UT WY".split(), _MOUNTAIN),
    **dict.fromkeys("CA NV OR WA".split(), _PACIFIC),
    "AZ": "America/Phoenix",  # no daylight saving
    "AK": "America/Anchorage",
    "HI": "Pacific/Honolulu",
}


def _hours() -> tuple[int, int] | None:
    """The configured window, or None when it is switched off."""
    raw = (os.getenv("SEND_WINDOW_HOURS") or "").strip().lower()
    if raw == "off":
        return None
    try:
        start, end = (int(part) for part in raw.split("-"))
    except ValueError:
        return WINDOW_START_HOUR, WINDOW_END_HOUR
    return (start, end) if 0 <= start < end <= 24 else (WINDOW_START_HOUR, WINDOW_END_HOUR)


def _utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def open_now(zone: ZoneInfo, now: datetime | None = None) -> bool:
    hours = _hours()
    if hours is None:
        return True
    local = _utc(now).astimezone(zone)
    return local.weekday() < 5 and hours[0] <= local.hour < hours[1]


def next_open(zone: ZoneInfo, now: datetime | None = None) -> datetime:
    """The first moment a send may go (UTC); ``now`` itself when already open."""
    moment = _utc(now)
    if open_now(zone, moment):
        return moment
    start = _hours()[0]  # open_now is False only when a window is configured
    local = moment.astimezone(zone)
    day = local.date()
    if local.weekday() >= 5 or local.hour >= start:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(start), tzinfo=zone).astimezone(timezone.utc)


def _zone_for_record(country: str | None, region: str | None) -> ZoneInfo | None:
    if (country or "").strip().upper() == "GB":
        return ZoneInfo("Europe/London")
    if (country or "").strip().upper() == "US":
        from app.services.company_register import _US_STATE_CODES

        place = (region or "").strip()
        code = place.upper() if len(place) == 2 else _US_STATE_CODES.get(place.lower())
        if code in _US_STATE_ZONES:
            return ZoneInfo(_US_STATE_ZONES[code])
    return None


async def company_zone(db, company: str | None) -> ZoneInfo:
    """The company's time zone, US Eastern when the register cannot place it."""
    if company and company.strip():
        from app.services.register_names import derive

        keys = sorted({derive(company, country).entity_key for country in ("US", "GB")})
        rows = await (await db.execute(
            f"""SELECT country, region FROM company_register
                WHERE entity_key IN ({','.join('?' * len(keys))}) ORDER BY id""",
            keys,
        )).fetchall()
        for row in rows:
            zone = _zone_for_record(row["country"], row["region"])
            if zone:
                return zone
    return ZoneInfo(DEFAULT_ZONE)


async def recipient_zone(db, contact_id: int) -> ZoneInfo:
    row = await (await db.execute("SELECT company FROM contacts WHERE id=?", (contact_id,))).fetchone()
    return await company_zone(db, row["company"] if row else None)
