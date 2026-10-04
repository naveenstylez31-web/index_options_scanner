"""Global cues + GIFT Nifty.

GIFT Nifty (NSE IX) has no free official API, so we try a public quote feed first and,
if that fails, estimate the implied opening gap from US futures + Asia (clearly labelled).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import settings

log = logging.getLogger(__name__)

# symbol -> (label, weight sign for Indian equities: +1 risk-on, -1 risk-off)
CUES = {
    "ES=F": ("S&P 500 fut", +1),
    "NQ=F": ("Nasdaq fut", +1),
    "^N225": ("Nikkei", +1),
    "^HSI": ("Hang Seng", +1),
    "^KS11": ("Kospi", +1),
    "BZ=F": ("Brent crude", -1),
    "DX-Y.NYB": ("Dollar index", -1),
    "INR=X": ("USD/INR", -1),
    "^TNX": ("US 10Y yield", -1),
    "GC=F": ("Gold", -1),
    "^INDIAVIX": ("India VIX", -1),
    "^NSEI": ("Nifty 50", +1),
    "^BSESN": ("Sensex", +1),
}

GIFT_SOURCES = [
    # Public quote feed used by Moneycontrol's GIFT Nifty page (best-effort; may change)
    "https://priceapi.moneycontrol.com/pricefeed/notapplicable/inidicesindia/in%3Bgsx",
]


def _quote(sym: str) -> tuple[str, dict | None]:
    import yfinance as yf
    try:
        fi = yf.Ticker(sym).fast_info
        last, prev = float(fi["last_price"]), float(fi["previous_close"])
        if not prev:
            return sym, None
        return sym, {"last": last, "prev": prev, "chg_pct": 100 * (last - prev) / prev}
    except Exception as e:
        log.debug("quote %s failed: %s", sym, e)
        return sym, None


def global_cues() -> dict:
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = dict(ex.map(_quote, CUES))
    return {s: {**q, "label": CUES[s][0], "sign": CUES[s][1]} for s, q in res.items() if q}


def _find_price(obj) -> float | None:
    keys = ("lastprice", "pricecurrent", "ltp", "last_price", "lastPrice", "price", "LTP")
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:
                try:
                    v = float(str(obj[k]).replace(",", ""))
                    if 5000 < v < 100000:
                        return v
                except ValueError:
                    pass
        for v in obj.values():
            p = _find_price(v)
            if p:
                return p
    elif isinstance(obj, list):
        for v in obj:
            p = _find_price(v)
            if p:
                return p
    return None


def gift_nifty(nifty_prev_close: float | None, cues: dict) -> dict:
    """Returns {'price', 'gap_pts', 'gap_pct', 'source'}; source 'live' or 'implied'."""
    urls = ([settings.gift_nifty_url] if settings.gift_nifty_url else []) + GIFT_SOURCES
    for url in urls:
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            price = _find_price(r.json())
            if price and nifty_prev_close:
                gap = price - nifty_prev_close
                # sanity: GIFT should be within 4% of Nifty close
                if abs(gap) / nifty_prev_close < 0.04:
                    return {"price": price, "gap_pts": gap, "gap_pct": 100 * gap / nifty_prev_close,
                            "source": "live"}
        except Exception as e:
            log.debug("GIFT source %s failed: %s", url, e)

    # Fallback: empirical overnight beta model (US futures dominate, Asia confirms)
    if not nifty_prev_close:
        return {}
    def c(s):
        return cues.get(s, {}).get("chg_pct", 0.0)
    implied_pct = 0.35 * c("ES=F") + 0.10 * c("NQ=F") + 0.15 * c("^N225") + 0.10 * c("^HSI") \
        + 0.05 * c("^KS11") - 0.05 * c("BZ=F") - 0.10 * c("INR=X") * 2
    return {"price": nifty_prev_close * (1 + implied_pct / 100),
            "gap_pts": nifty_prev_close * implied_pct / 100, "gap_pct": implied_pct,
            "source": "implied"}
