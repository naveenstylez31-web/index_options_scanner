"""NSE public data: FII/DII cash flows, participant-wise F&O OI, trading holidays.

NSE blocks bare API calls, so a session first visits the homepage to collect cookies.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import date, datetime, timedelta

import pandas as pd
import requests

from .config import DATA_DIR, IST

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HOLIDAY_CACHE = DATA_DIR / "nse_holidays.json"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"})
    try:
        s.get("https://www.nseindia.com", timeout=15)
        s.get("https://www.nseindia.com/reports/fii-dii", timeout=15)
    except requests.RequestException:
        pass
    return s


def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- FII / DII cash
def fii_dii_cash() -> dict | None:
    """Latest provisional FII/FPI and DII cash-market figures (Rs crore)."""
    try:
        r = _session().get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=20)
        rows = r.json()
    except Exception as e:
        log.warning("FII/DII fetch failed: %s", e)
        return None
    out = {}
    for row in rows:
        cat = str(row.get("category", "")).upper()
        key = "fii" if "FII" in cat or "FPI" in cat else "dii" if "DII" in cat else None
        if key:
            out[key] = {"buy": _num(row.get("buyValue")), "sell": _num(row.get("sellValue")),
                        "net": _num(row.get("netValue"))}
            out["date"] = row.get("date")
    return out or None


# ---------------------------------------------------------------- Participant OI
PARTICIPANT_URLS = [
    "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d}.csv",
    "https://archives.nseindia.com/content/nsccl/fao_participant_oi_{d}.csv",
]


def _participant_csv(day: date, s: requests.Session) -> pd.DataFrame | None:
    for url in PARTICIPANT_URLS:
        try:
            r = s.get(url.format(d=day.strftime("%d%m%Y")), timeout=20)
            if r.status_code == 200 and b"Client Type" in r.content:
                df = pd.read_csv(io.BytesIO(r.content), skiprows=1)
                df.columns = [c.strip() for c in df.columns]
                df["Client Type"] = df["Client Type"].astype(str).str.strip()
                return df.set_index("Client Type")
        except Exception:
            continue
    return None


def participant_oi() -> dict | None:
    """FII/Pro/Client index futures & options positioning for the last two available days.

    Returns FII index-futures long %, net contracts, day-on-day change and
    FII net index-options exposure (calls long-short, puts long-short).
    """
    s = _session()
    frames: list[tuple[date, pd.DataFrame]] = []
    now = datetime.now(IST)
    # NSE publishes the day's file in the evening; before 19:00 start from yesterday
    d = now.date() if now.hour >= 19 else now.date() - timedelta(days=1)
    for _ in range(12):
        if d.weekday() < 5:
            df = _participant_csv(d, s)
            if df is not None:
                frames.append((d, df))
                if len(frames) == 2:
                    break
        d -= timedelta(days=1)
    if not frames:
        log.warning("Participant OI not available")
        return None

    def extract(df: pd.DataFrame) -> dict:
        res = {}
        for who in ("FII", "Pro", "Client", "DII"):
            if who not in df.index:
                continue
            r = df.loc[who]
            fl, fs = _num(r.get("Future Index Long")), _num(r.get("Future Index Short"))
            res[who] = {
                "fut_long": fl, "fut_short": fs, "fut_net": fl - fs,
                "fut_long_pct": 100 * fl / (fl + fs) if fl + fs else 50.0,
                "call_net": _num(r.get("Option Index Call Long")) - _num(r.get("Option Index Call Short")),
                "put_net": _num(r.get("Option Index Put Long")) - _num(r.get("Option Index Put Short")),
            }
        return res

    today_d, today_df = frames[0]
    out = {"date": today_d.isoformat(), **extract(today_df)}
    if len(frames) > 1:
        prev = extract(frames[1][1])
        for who in out:
            if who in prev and isinstance(out[who], dict):
                out[who]["fut_net_chg"] = out[who]["fut_net"] - prev[who]["fut_net"]
                out[who]["call_net_chg"] = out[who]["call_net"] - prev[who]["call_net"]
                out[who]["put_net_chg"] = out[who]["put_net"] - prev[who]["put_net"]
    return out


# ---------------------------------------------------------------- Holidays
def trading_holidays(refresh: bool = False) -> set[str]:
    """Set of 'YYYY-MM-DD' F&O trading holidays (cached for 7 days)."""
    if HOLIDAY_CACHE.exists() and not refresh:
        try:
            d = json.loads(HOLIDAY_CACHE.read_text())
            if datetime.fromisoformat(d["fetched"]) > datetime.now() - timedelta(days=7):
                return set(d["dates"])
        except Exception:
            pass
    dates: set[str] = set()
    try:
        r = _session().get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=20)
        for row in r.json().get("FO", []) + r.json().get("CM", []):
            try:
                dates.add(datetime.strptime(row["tradingDate"], "%d-%b-%Y").strftime("%Y-%m-%d"))
            except (KeyError, ValueError):
                continue
        HOLIDAY_CACHE.write_text(json.dumps({"fetched": datetime.now().isoformat(), "dates": sorted(dates)}))
    except Exception as e:
        log.warning("Holiday list fetch failed (%s) - using cache if any", e)
        if HOLIDAY_CACHE.exists():
            return set(json.loads(HOLIDAY_CACHE.read_text()).get("dates", []))
    return dates


def is_trading_day(d: date | None = None) -> bool:
    import os
    d = d or datetime.now(IST).date()
    # Manual list as a backstop (NSE often blocks cloud/US IPs): EXTRA_HOLIDAYS=2026-10-20,2026-11-10
    extra = {x.strip() for x in os.getenv("EXTRA_HOLIDAYS", "").split(",") if x.strip()}
    return d.weekday() < 5 and d.isoformat() not in trading_holidays() | extra
