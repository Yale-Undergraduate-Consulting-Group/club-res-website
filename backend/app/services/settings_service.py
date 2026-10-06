"""
Settings service - key-value storage for app config
"""
from app.database import get_db


async def get_setting(key: str) -> str | None:
    """Get a setting value by key."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row["value"] if row else None
    finally:
        await db.close()


async def set_setting(key: str, value: str | None) -> None:
    """Set a setting value."""
    db = await get_db()
    try:
        if value is None:
            await db.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        await db.commit()
    finally:
        await db.close()


# The structured sign-off the club signs with. Stored as fields rather than a
# pasted blob so the block is consistent between members and can never be a
# generated guess.
SIGN_OFF_KEYS = (
    "sign_off_name", "sign_off_pronouns", "sign_off_role",
    "sign_off_organization", "sign_off_linkedin", "sign_off_phone",
    "sign_off_logo_url",
)

MEMBER_SETTING_KEYS = frozenset({"daily_send_limit", "daily_send_warn_at", *SIGN_OFF_KEYS})


async def load_sign_off(user_id: int):
    """Load the complete sender block with one database connection."""
    from app.services.email_body import SignOff

    prefix = f"member:{user_id}:"
    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (prefix + "sign_off_%",),
        )).fetchall()
        values = {
            row["key"][len(prefix):]: (row["value"] or "").strip()
            for row in rows
        }
        user = await (await db.execute(
            "SELECT name FROM users WHERE id=?", (user_id,)
        )).fetchone()
    finally:
        await db.close()

    return SignOff(
        name=values.get("sign_off_name") or ((user["name"] or "").strip() if user else ""),
        pronouns=values.get("sign_off_pronouns", ""),
        role=values.get("sign_off_role", ""),
        organization=values.get("sign_off_organization") or "Yale Undergraduate Consulting Group",
        linkedin_url=values.get("sign_off_linkedin", ""),
        phone=values.get("sign_off_phone", ""),
        logo_url=values.get("sign_off_logo_url", ""),
    )


async def get_member_setting(user_id: int, key: str) -> str | None:
    if key not in MEMBER_SETTING_KEYS:
        raise ValueError("Unsupported member setting")
    return await get_setting(f"member:{user_id}:{key}")


async def set_member_setting(user_id: int, key: str, value: str | None) -> None:
    if key not in MEMBER_SETTING_KEYS:
        raise ValueError("Unsupported member setting")
    await set_setting(f"member:{user_id}:{key}", value)


async def member_daily_send_limit(user_id: int) -> int:
    """Per-member ceiling on initial sends, falling back to the club default."""
    import os

    default = int(os.getenv("CAMPAIGN_DAILY_SEND_LIMIT", "100") or 100)
    raw = await get_member_setting(user_id, "daily_send_limit")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return max(1, default)
    # A member may lower the ceiling but not raise it past the club default.
    return max(1, min(value, max(1, default)))


async def member_send_warn_threshold(user_id: int) -> int | None:
    """Warn the member once a run passes this many sends in a day."""
    raw = await get_member_setting(user_id, "daily_send_warn_at")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None
