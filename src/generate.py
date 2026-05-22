"""LLM generation: Gemini Flash primary, Groq fallback, strict JSON contract."""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

log = logging.getLogger("generate")

GEMINI_MODEL = "gemini-1.5-flash"
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Cap article text sent to the LLM (keeps us well inside token limits).
MAX_TEXT_CHARS = 12000

PROMPT_TEMPLATE = """You are an elite SAT Reading & Writing coach preparing a student targeting a 1550.
The student is strong in STEM and needs to grow ACADEMIC ENGLISH VOCABULARY and
mastery of the exact question types on the Digital SAT.

ARTICLE SOURCE: {source_id}
DIFFICULTY TARGET: {difficulty}
ARTICLE TITLE: {title}

ARTICLE TEXT:
\"\"\"
{full_text}
\"\"\"

TASK: Output ONLY valid JSON (no markdown, no commentary) matching this schema exactly:
{{
  "one_line_summary": string,
  "why_it_matters": string,
  "vocabulary": [
    {{
      "word": string,
      "part_of_speech": string,
      "definition": string,
      "sentence_from_article": string
    }}
  ],
  "questions": [
    {{
      "type": string,
      "stem": string,
      "choices": {{ "A": string, "B": string, "C": string, "D": string }},
      "answer": string,
      "explanation": string
    }}
  ]
}}

HARD RULES:
- one_line_summary <= 25 words. why_it_matters is 1 sentence.
- vocabulary: 6-8 items. Choose the RAREST, most test-relevant words ACTUALLY PRESENT in
  the text (tier-3 academic vocabulary). Do NOT invent words. word MUST appear verbatim in
  the text and sentence_from_article must be the real sentence containing it.
- questions: EXACTLY 3. At least ONE must be type "vocab_in_context" and at least ONE must
  be "inference" OR "function". type is one of: vocab_in_context, inference, function,
  main_idea, detail. Calibrate difficulty to: {difficulty}
- Distractors must be PLAUSIBLE - partially true, or the right answer to a slightly
  different question. No obvious throwaways.
- Ground everything in the text. Never hallucinate facts not in the article.
- answer is "A" | "B" | "C" | "D".
- Output JSON only. No prose before or after. No code fences.
"""


def build_prompt(article: dict, difficulty: str) -> str:
    return PROMPT_TEMPLATE.format(
        source_id=article["source_id"],
        difficulty=difficulty,
        title=article["title"],
        full_text=article["full_text"][:MAX_TEXT_CHARS],
    )


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_json(raw: str) -> dict | None:
    cleaned = _strip_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # salvage: grab the outermost {...} block
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    log.warning("could not parse LLM JSON")
    return None


def _validate(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    if not payload.get("vocabulary") or not payload.get("questions"):
        return False
    return len(payload["questions"]) >= 1


def _call_gemini(prompt: str) -> dict | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        log.info("no GEMINI_API_KEY set; skipping Gemini")
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            GEMINI_MODEL,
            generation_config={"response_mime_type": "application/json"},
        )
        resp = model.generate_content(prompt)
        payload = _parse_json(resp.text)
        if payload and _validate(payload):
            return payload
        log.warning("Gemini returned invalid payload")
    except Exception as e:
        log.warning("Gemini call failed: %s", e)
    return None


def _call_groq(prompt: str) -> dict | None:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        log.info("no GROQ_API_KEY set; skipping Groq")
        return None
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.4,
                "response_format": {"type": "json_object"},
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        payload = _parse_json(content)
        if payload and _validate(payload):
            return payload
        log.warning("Groq returned invalid payload")
    except Exception as e:
        log.warning("Groq call failed: %s", e)
    return None


def _degraded(article: dict) -> dict:
    """Minimal payload so the article still posts when both LLMs fail."""
    sentences = re.split(r"(?<=[.!?])\s+", article["full_text"].strip())
    summary = " ".join(sentences[:2])[:300]
    return {
        "one_line_summary": summary,
        "why_it_matters": "",
        "vocabulary": [],
        "questions": [],
        "degraded": True,
    }


def enrich(article: dict, difficulty: str) -> dict:
    """Generate vocab + questions for an article. Always returns a payload."""
    prompt = build_prompt(article, difficulty)

    payload = _call_gemini(prompt)
    if payload is None:
        # one retry on Gemini before falling back
        time.sleep(2)
        payload = _call_gemini(prompt)
    if payload is None:
        payload = _call_groq(prompt)
    if payload is None:
        log.warning("both LLMs failed for '%s'; degraded post", article["title"])
        return _degraded(article)

    payload.setdefault("degraded", False)
    payload.setdefault("why_it_matters", "")
    payload.setdefault("vocabulary", [])
    payload.setdefault("questions", [])
    return payload
