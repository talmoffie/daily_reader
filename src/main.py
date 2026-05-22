"""Orchestrator: pick slot from current UTC hour, run the full pipeline.

Top-level exceptions are caught and logged so the workflow's commit/keepalive
step always runs (exit 0).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone

import yaml

import discord_post
import fetch
import generate
from slots import current_slot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("main")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "sources.yaml")
SEEN_PATH = os.path.join(ROOT, "state", "seen.json")
RUNS_LOG = os.path.join(ROOT, "state", "runs.log")


def _log_run(line: str) -> None:
    os.makedirs(os.path.dirname(RUNS_LOG), exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(RUNS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{ts} {line}\n")


def run() -> None:
    slot_id = current_slot()
    log.info("slot: %s", slot_id)

    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        sources = yaml.safe_load(f)
    slot_cfg = sources["slots"][slot_id]
    difficulty = slot_cfg.get("difficulty", "")

    seen = fetch.load_seen(SEEN_PATH)
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")

    count = slot_cfg.get("count", 1) if slot_id == "bonus" else 1
    articles = fetch.get_articles(slot_id, slot_cfg, seen, count=count)

    if not articles:
        _log_run(f"slot={slot_id} result=no_article")
        fetch.save_seen(SEEN_PATH, seen)  # persist last_used/dedupe regardless
        log.warning("no article posted for slot %s", slot_id)
        return

    posted = 0
    for article in articles:
        payload = generate.enrich(article, difficulty)
        ok = discord_post.post_article(
            webhook, article, payload, slot_id, bonus=(slot_id == "bonus")
        )
        if ok:
            posted += 1
        _log_run(
            f"slot={slot_id} source={article['source_id']} "
            f"degraded={payload.get('degraded')} posted={ok} "
            f"url={article['url']}"
        )

    fetch.save_seen(SEEN_PATH, seen)
    log.info("done: %d/%d articles posted", posted, len(articles))


def main() -> int:
    try:
        run()
    except Exception as e:  # never hard-crash: keepalive commit must run
        log.exception("top-level failure: %s", e)
        try:
            _log_run(f"FATAL {type(e).__name__}: {e}")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
