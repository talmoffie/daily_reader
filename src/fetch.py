"""Article fetching: RSS parse, dedupe against state, full-text extraction."""
from __future__ import annotations

import json
import logging
import os

import feedparser
import trafilatura

from slots import rotate_pool, sort_bonus_pool

log = logging.getLogger("fetch")

MIN_WORDS = 350          # quality gate: shorter = excerpt-only / extraction failure
SEEN_KEEP = 50           # keep last N urls per source for dedupe
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def load_seen(path: str) -> dict:
    if not os.path.exists(path):
        return {"sources": {}, "last_used": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("sources", {})
        data.setdefault("last_used", {})
        return data
    except Exception as e:  # corrupt state should never crash the run
        log.warning("seen.json unreadable (%s); starting fresh", e)
        return {"sources": {}, "last_used": {}}


def save_seen(path: str, seen: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


def _mark_seen(seen: dict, source_id: str, url: str) -> None:
    urls = seen["sources"].setdefault(source_id, [])
    if url not in urls:
        urls.append(url)
    seen["sources"][source_id] = urls[-SEEN_KEEP:]


def _extract_full_text(url: str) -> str | None:
    """Fetch a URL and extract clean main-body text via trafilatura."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
        return text
    except Exception as e:
        log.warning("extraction failed for %s: %s", url, e)
        return None


def _try_source(source: dict, seen: dict) -> dict | None:
    """Try one source: parse feed, find first unread entry with enough text."""
    source_id = source["id"]
    feed_urls = [source.get("url")] + ([source["alt"]] if source.get("alt") else [])
    seen_urls = set(seen["sources"].get(source_id, []))

    for feed_url in feed_urls:
        if not feed_url:
            continue
        try:
            parsed = feedparser.parse(feed_url, agent=USER_AGENT)
        except Exception as e:
            log.warning("feed parse error %s: %s", feed_url, e)
            continue
        if not parsed.entries:
            log.warning("feed empty: %s", feed_url)
            continue

        for entry in parsed.entries:  # feedparser yields newest-first
            url = entry.get("link")
            if not url or url in seen_urls:
                continue
            text = _extract_full_text(url)
            if not text or len(text.split()) < MIN_WORDS:
                log.info("skip (short/failed extraction): %s", url)
                continue
            return {
                "title": entry.get("title", "Untitled"),
                "url": url,
                "source_id": source_id,
                "topic_hint": source.get("topic_hint"),
                "full_text": text,
            }
    return None


def get_article(slot_id: str, slot_cfg: dict, seen: dict) -> dict | None:
    """Pick the first unread, full-text article from a slot's source pool.

    Mutates `seen` (marks the chosen url + records last_used). Returns the
    article dict or None if the whole pool is exhausted.
    """
    pool = slot_cfg.get("pool", [])
    if slot_id == "bonus":
        pool = sort_bonus_pool(pool)
    else:
        pool = rotate_pool(pool, seen["last_used"].get(slot_id))

    for source in pool:
        article = _try_source(source, seen)
        if article:
            _mark_seen(seen, article["source_id"], article["url"])
            seen["last_used"][slot_id] = article["source_id"]
            return article

    log.warning("slot '%s' exhausted: no unread full-text article found", slot_id)
    return None


def get_articles(slot_id: str, slot_cfg: dict, seen: dict, count: int = 1) -> list[dict]:
    """Fetch up to `count` distinct articles from a slot (for the bonus slot)."""
    out: list[dict] = []
    for _ in range(count):
        article = get_article(slot_id, slot_cfg, seen)
        if not article:
            break
        out.append(article)
    return out
