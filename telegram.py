"""Telegram delivery (bot must be an admin of the channel)."""
from __future__ import annotations

import logging
import time

import requests

from .config import settings

log = logging.getLogger(__name__)
LIMIT = 4000


def _chunks(text: str):
    while text:
        if len(text) <= LIMIT:
            yield text
            return
        cut = text.rfind("\n", 0, LIMIT)
        cut = cut if cut > 0 else LIMIT
        yield text[:cut]
        text = text[cut:].lstrip("\n")


def send(text: str, silent: bool = False) -> bool:
    if settings.dry_run or not (settings.telegram_bot_token and settings.telegram_chat_id):
        print("\n" + "=" * 60 + "\n" + text + "\n" + "=" * 60)
        return True
    ok = True
    for part in _chunks(text):
        for attempt in range(3):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    timeout=15, json={"chat_id": settings.telegram_chat_id, "text": part,
                                      "parse_mode": "HTML", "disable_web_page_preview": True,
                                      "disable_notification": silent})
                if r.status_code == 200:
                    break
                if r.status_code == 429:
                    time.sleep(r.json().get("parameters", {}).get("retry_after", 3))
                    continue
                log.error("Telegram %s: %s", r.status_code, r.text[:200])
                # retry once without HTML in case of a formatting error
                if r.status_code == 400 and "parse" in r.text.lower():
                    import re
                    requests.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                                  timeout=15, json={"chat_id": settings.telegram_chat_id,
                                                    "text": re.sub("<[^>]+>", "", part)})
                    break
            except requests.RequestException as e:
                log.warning("Telegram error: %s", e)
                time.sleep(2)
        else:
            ok = False
    return ok
