"""Discord webhook formatting: rich embeds + spoiler-tagged answers."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("discord")

# Discord hard limits
DESC_MAX = 4096
FIELD_VALUE_MAX = 1024
MSG_MAX = 2000
EMBED_COLOR = 0x00D4FF  # MOFF accent cyan

SLOT_BADGE = {
    "university_science_a": "UNIVERSITY SCIENCE",
    "university_science_b": "UNIVERSITY ENGINEERING",
    "sat_science": "SAT SCIENCE",
    "history_other": "SAT HUMANITIES",
    "bonus": "BONUS",
}


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _post(webhook: str, payload: dict) -> bool:
    """POST to the webhook with exponential backoff on 429/5xx."""
    for attempt in range(3):
        try:
            r = requests.post(webhook, json=payload, timeout=30)
            if r.status_code in (200, 204):
                return True
            if r.status_code == 429:
                retry_after = float(r.json().get("retry_after", 2)) if r.content else 2
                log.warning("429 rate limited; sleeping %.1fs", retry_after)
                time.sleep(retry_after + 0.5)
                continue
            if 500 <= r.status_code < 600:
                log.warning("Discord %s; backing off", r.status_code)
                time.sleep(2 ** attempt)
                continue
            log.error("Discord rejected post: %s %s", r.status_code, r.text[:300])
            return False
        except Exception as e:
            log.warning("Discord post error (attempt %d): %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    return False


def _vocab_fields(vocabulary: list[dict]) -> list[dict]:
    """Render vocabulary into one or more embed fields (respecting 1024 cap)."""
    fields: list[dict] = []
    buf = ""
    for v in vocabulary:
        word = v.get("word", "?")
        pos = v.get("part_of_speech", "")
        definition = v.get("definition", "")
        sentence = v.get("sentence_from_article", "")
        block = f"**{word}** ({pos}) — {definition}\n*{sentence}*\n\n"
        if len(buf) + len(block) > FIELD_VALUE_MAX:
            fields.append({"name": "​", "value": buf.strip() or "​"})
            buf = block
        else:
            buf += block
    if buf.strip():
        fields.append({"name": "​", "value": buf.strip()})
    if fields:
        fields[0]["name"] = "\U0001F4D6 Hard Vocabulary"
    return fields[:25]


def _questions_messages(questions: list[dict]) -> list[str]:
    """Render the 3 questions into one or more plain messages under 2000 chars."""
    blocks: list[str] = []
    for i, q in enumerate(questions, 1):
        qtype = q.get("type", "question").replace("_", " ")
        stem = q.get("stem", "")
        choices = q.get("choices", {})
        answer = q.get("answer", "?")
        explanation = q.get("explanation", "")
        lines = [f"**Q{i} ({qtype}):** {stem}"]
        for letter in ("A", "B", "C", "D"):
            if letter in choices:
                lines.append(f"{letter}) {choices[letter]}")
        lines.append(f"||Answer: {answer} — {explanation}||")
        blocks.append("\n".join(lines))

    messages: list[str] = []
    buf = ""
    for block in blocks:
        candidate = (buf + "\n\n" + block).strip() if buf else block
        if len(candidate) > MSG_MAX:
            if buf:
                messages.append(buf)
            buf = block[:MSG_MAX]
        else:
            buf = candidate
    if buf:
        messages.append(buf)
    return messages


def post_article(webhook: str, article: dict, payload: dict, slot_id: str,
                 bonus: bool = False) -> bool:
    """Post one article (embed + questions) to Discord. Returns success bool."""
    if not webhook:
        log.error("DISCORD_WEBHOOK_URL not set")
        return False

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    badge = SLOT_BADGE.get(slot_id, slot_id.upper())
    bonus_prefix = "\U0001F381 BONUS · " if bonus else ""
    footer = f"{bonus_prefix}{badge} · {article['source_id']} · {date_str}"

    summary = payload.get("one_line_summary", "")
    why = payload.get("why_it_matters", "")
    description = summary + (f"\n\n*{why}*" if why else "")
    if payload.get("degraded"):
        description += "\n\n_(vocabulary/questions unavailable this run)_"

    embed = {
        "title": _truncate(article["title"], 256),
        "url": article["url"],
        "description": _truncate(description, DESC_MAX),
        "color": EMBED_COLOR,
        "footer": {"text": _truncate(footer, 2048)},
        "fields": _vocab_fields(payload.get("vocabulary", [])),
    }

    ok = _post(webhook, {"embeds": [embed]})
    if not ok:
        return False

    for msg in _questions_messages(payload.get("questions", [])):
        time.sleep(0.5)  # gentle pacing to avoid webhook rate limits
        _post(webhook, {"content": msg})
    return True
