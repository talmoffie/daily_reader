"""Slot definitions: UTC-hour -> slot mapping and source rotation helpers."""
from __future__ import annotations

from datetime import datetime, timezone

# UTC hour -> slot id (matches the cron table in the spec, section 3)
HOUR_TO_SLOT = {
    5: "university_science_a",   # 08:00 Jerusalem
    9: "university_science_b",   # 12:00
    13: "sat_science",           # 16:00
    17: "history_other",         # 20:00
    19: "bonus",                 # 22:00
}


def current_slot(now: datetime | None = None) -> str:
    """Map the current UTC hour to a slot id.

    Exact-hour match wins. But GitHub cron is frequently delayed by hours, so a
    run that fires at an unmapped hour must NOT blindly default — it should post
    the slot for the most recent scheduled hour at-or-before now (i.e. the slot
    that was *due*). Before the day's first slot, wrap to the last slot.
    """
    now = now or datetime.now(timezone.utc)
    hour = now.hour
    scheduled_hours = sorted(HOUR_TO_SLOT)
    due = [h for h in scheduled_hours if h <= hour]
    chosen_hour = due[-1] if due else scheduled_hours[-1]
    return HOUR_TO_SLOT[chosen_hour]


def rotate_pool(pool: list[dict], last_used_id: str | None) -> list[dict]:
    """Return the pool reordered so the last-used source is tried last.

    Keeps source rotation simple: if we used `quanta_physics` yesterday and
    `quanta_cs` has unread articles, prefer `quanta_cs` today.
    """
    if not last_used_id:
        return list(pool)
    head = [s for s in pool if s.get("id") != last_used_id]
    tail = [s for s in pool if s.get("id") == last_used_id]
    return head + tail


def sort_bonus_pool(pool: list[dict]) -> list[dict]:
    """Order the bonus pool by topic priority (lower number = higher priority)."""
    return sorted(pool, key=lambda s: s.get("priority", 99))
