# SAT Reader Bot

Drops 4 hard reading articles into Discord every day — each with extracted hard
vocabulary and 3 SAT-style questions — plus 2 bonus articles on advanced topics.
Targets a 1550 SAT (English is the bottleneck). Runs free, forever, with no server.

## How it works

```
GitHub Actions (cron)  →  Python  →  Discord webhook
                             ├─ RSS feeds                 (article links)
                             ├─ trafilatura               (full-text extraction)
                             ├─ Gemini Flash [Groq fallback]  (vocab + 3 questions JSON)
                             └─ commit state/ each run    (dedupe + keepalive)
```

There is **no always-on bot**. GitHub Actions runs the script on a cron schedule and
POSTs to a Discord webhook. Committing `state/` on every run counts as repo activity,
which defeats GitHub's 60-day auto-disable of scheduled workflows — so it self-renews.

## Daily schedule

Times are Jerusalem (cron is UTC). Exact times don't matter.

| Drop | Jerusalem | UTC cron | Slot |
|------|-----------|----------|------|
| 1 | 08:00 | `0 5 * * *`  | university science A (graduate vocab) |
| 2 | 12:00 | `0 9 * * *`  | university science B (engineering) |
| 3 | 16:00 | `0 13 * * *` | SAT science (~1550 calibrated) |
| 4 | 20:00 | `0 17 * * *` | history / humanities |
| 5 | 22:00 | `0 19 * * *` | bonus (2 articles, advanced topics) |

## Project layout

```
.github/workflows/daily.yml   cron + run steps + commit-back
src/main.py                   orchestrator (UTC hour -> slot -> pipeline)
src/fetch.py                  RSS parse, dedupe, trafilatura extraction
src/generate.py               Gemini call + prompt + JSON parse + Groq fallback
src/discord_post.py           build embeds, spoiler answers, POST webhook
src/slots.py                  hour->slot map + source rotation
sources.yaml                  all feeds, grouped by slot, with difficulty tier
state/seen.json               dedupe + last-used source (committed each run)
state/runs.log                appended each run
```

## SETUP STEPS (do these once)

1. **Discord webhook:** Server Settings → Integrations → Webhooks → New Webhook →
   pick the channel → **Copy Webhook URL**. (No bot token needed.)
2. **Gemini key:** aistudio.google.com → "Get API key" → create (free, no card).
3. **Groq key (optional fallback):** console.groq.com → API Keys → create.
4. **Create a PUBLIC GitHub repo**, push this code.
5. **Repo → Settings → Secrets and variables → Actions → New repository secret**, add:
   - `DISCORD_WEBHOOK_URL`
   - `GEMINI_API_KEY`
   - `GROQ_API_KEY` (optional)
6. **Actions tab → enable workflows →** run `sat-reader-bot` via **Run workflow**
   (manual) to test. Once a post lands in Discord, the cron schedule takes over.

Secrets live only in GitHub Secrets — never in code, never committed. Safe even
though the repo is public.

## Local testing

```bash
pip install -r requirements.txt

# pick a slot by faking the UTC hour, or just run (defaults to university_science_a)
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
export GEMINI_API_KEY="..."
export GROQ_API_KEY="..."          # optional
python src/main.py
```

To force a specific slot locally, the run uses the current UTC hour. Run near the
target UTC hour, or temporarily edit `HOUR_TO_SLOT` / call `current_slot()` in a REPL.

## Robustness

- Dead/empty feed → try `alt` URL → next source in pool → log skip, exit clean.
- Extracted text < 350 words → skip article, try next entry.
- Gemini error/quota → 1 retry → Groq fallback → degraded post (article still lands).
- Malformed LLM JSON → strip fences → salvage outer `{...}` → degraded post.
- Discord 429/5xx → exponential backoff, up to 3 tries.
- All top-level exceptions caught → logged → exit 0 (keepalive commit always runs).
