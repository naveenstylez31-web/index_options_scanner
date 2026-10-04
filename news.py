"""Market news from RSS feeds, scored with a finance-specific lexicon.

Optionally (ANTHROPIC_API_KEY set) Claude reads the headlines + all scanner numbers and writes
a short desk-style narrative. Without a key, the rule-based score is used on its own.
"""
from __future__ import annotations

import logging
import re
import time
from calendar import timegm

import feedparser
import requests

from .config import settings

log = logging.getLogger(__name__)

FEEDS = {
    "ET Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
    "Moneycontrol Biz": "https://www.moneycontrol.com/rss/business.xml",
    "Livemint": "https://www.livemint.com/rss/markets",
    "Business Standard": "https://www.business-standard.com/rss/markets-106.rss",
    "CNBC-TV18": "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
}

# phrase -> weight (positive = bullish for Indian indices)
LEXICON = {
    # flows / positioning
    r"fii[s]? (buy|bought|net buyers|inflow)": 3, r"fpi[s]? (buy|bought|inflow)": 3,
    r"fii[s]? (sell|sold|net sellers|outflow)": -3, r"fpi[s]? (sell|sold|outflow)": -3,
    r"short covering": 2, r"short build ?up": -2, r"long build ?up": 2, r"long unwinding": -2,
    # macro / policy
    r"rate cut": 3, r"rate hike": -3, r"dovish": 2, r"hawkish": -2, r"repo rate unchanged": 0,
    r"inflation (eases|cools|falls|softens)": 2, r"inflation (rises|jumps|surges|accelerates)": -2,
    r"gdp (beats|grows faster|accelerates)": 2, r"gdp (slows|misses|contracts)": -2,
    r"rupee (gains|strengthens|rises)": 1, r"rupee (falls|weakens|slumps|record low)": -2,
    r"crude (falls|slides|drops|eases)": 1, r"crude (jumps|surges|spikes|rallies)": -2,
    r"bond yields? (fall|ease|drop)": 1, r"bond yields? (rise|jump|surge)": -1,
    r"tariff": -1, r"sanction": -1, r"trade deal": 2, r"stimulus": 2,
    # risk events
    r"\bwar\b|missile|airstrike|escalat": -3, r"ceasefire|peace talks|de-escalat": 2,
    r"recession": -2, r"selloff|sell-off|rout|crash|plunge|tumble": -2,
    r"rally|surge|record high|all-time high|soar": 2, r"gap[- ]up": 1, r"gap[- ]down": -1,
    r"wall street (gains|rises|rallies|closes higher)": 2,
    r"wall street (falls|drops|slides|closes lower)": -2,
    r"(beats|tops) estimates|strong results|profit (jumps|rises)": 1,
    r"(misses|below) estimates|profit (falls|drops|declines)": -1,
    r"downgrade": -1, r"upgrade": 1,
}
HIGH_IMPACT = r"\b(rbi|mpc|fed|fomc|powell|cpi|inflation data|nonfarm|payrolls|budget|election|" \
              r"gdp data|expiry|war|tariff|opec|jackson hole|ecb|boj)\b"
INDEX_RELEVANCE = r"nifty|sensex|market|stocks|equit|fii|fpi|rbi|fed|rupee|crude|global|wall street|" \
                  r"bank nifty|index|derivative|f&o|gift"


def fetch_news(hours: int = 16, limit: int = 60) -> list[dict]:
    cutoff = time.time() - hours * 3600
    items, seen = [], set()
    for src, url in FEEDS.items():
        try:
            raw = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"}).content
            feed = feedparser.parse(raw)
        except Exception as e:
            log.debug("feed %s failed: %s", src, e)
            continue
        for e in feed.entries:
            ts = e.get("published_parsed") or e.get("updated_parsed")
            t = timegm(ts) if ts else time.time()
            title = re.sub(r"\s+", " ", e.get("title", "")).strip()
            key = title.lower()[:80]
            if t < cutoff or not title or key in seen:
                continue
            seen.add(key)
            items.append({"source": src, "title": title, "ts": t,
                          "summary": re.sub("<[^>]+>", "", e.get("summary", ""))[:300]})
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items[:limit]


def score_news(items: list[dict]) -> dict:
    total, scored = 0.0, []
    events = set()
    for it in items:
        text = f"{it['title']} {it['summary']}".lower()
        if not re.search(INDEX_RELEVANCE, text):
            continue
        s = sum(w for pat, w in LEXICON.items() if re.search(pat, text))
        # recency weighting: last 4h counts full, older decays
        age_h = (time.time() - it["ts"]) / 3600
        s *= 1.0 if age_h < 4 else 0.6
        for m in re.findall(HIGH_IMPACT, text):
            events.add(m.upper())
        if s:
            scored.append({**it, "score": s})
            total += s
    scored.sort(key=lambda x: abs(x["score"]), reverse=True)
    # squash to -1..+1
    norm = max(-1.0, min(1.0, total / 12))
    return {"raw": total, "norm": norm, "top": scored[:6], "events": sorted(events)}


def ai_narrative(context: str) -> str | None:
    """Optional: a 4-6 line desk note from Claude. Returns None if no key / failure."""
    if not settings.anthropic_api_key:
        return None
    prompt = (
        "You are the head of an index-derivatives desk in Mumbai writing the 08:30 pre-market note "
        "for Nifty and Sensex weekly options. Using ONLY the data below, write at most 6 crisp lines: "
        "1) opening expectation, 2) what flows/positioning say, 3) key OI levels and what would "
        "invalidate the bias, 4) event risk today. No generic advice, no disclaimers, plain text.\n\n"
        + context)
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
            "x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"}, json={
            "model": settings.anthropic_model, "max_tokens": 450,
            "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip() or None
    except Exception as e:
        log.warning("AI narrative failed: %s", e)
        return None
