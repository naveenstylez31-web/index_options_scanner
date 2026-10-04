"""Offline tests with a synthetic Dhan-format option chain. Run: python -m pytest -q  (or python tests/test_offline.py)"""
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DRY_RUN"] = "true"

from scanner import intraday, report  # noqa: E402
from scanner.analytics import analyze, options_score  # noqa: E402
from scanner.bias import compute_bias  # noqa: E402


def make_chain(spot=25000.0, call_wall=25300, put_wall=24700, bump=None, name="NIFTY"):
    oc = {}
    for k in range(24000, 26050, 50):
        dist = (k - spot) / 300
        ce_iv, pe_iv = 12 - 0.5 * dist, 12 + 0.8 * max(-dist, 0) + 0.2
        ce_px = max(spot - k, 0) + 120 * math.exp(-abs(dist) / 1.5)
        pe_px = max(k - spot, 0) + 120 * math.exp(-abs(dist) / 1.5)
        ce_oi = 2e6 + (6e6 if k == call_wall else 0) + (3e6 if k == call_wall + 100 else 0)
        pe_oi = 2e6 + (7e6 if k == put_wall else 0) + (3e6 if k == put_wall - 100 else 0)
        if bump and k == bump[0]:
            if bump[1] == "ce":
                ce_oi += bump[2]
            else:
                pe_oi += bump[2]
        d = 1 / (1 + math.exp(-(spot - k) / 120))
        oc[f"{k}.000000"] = {
            "ce": {"oi": ce_oi, "previous_oi": ce_oi * 0.8, "last_price": ce_px, "previous_close_price": ce_px * 1.05,
                   "implied_volatility": ce_iv, "greeks": {"delta": d, "gamma": 0.001}, "volume": 1e6},
            "pe": {"oi": pe_oi, "previous_oi": pe_oi * 0.7, "last_price": pe_px, "previous_close_price": pe_px * 1.1,
                   "implied_volatility": pe_iv, "greeks": {"delta": d - 1, "gamma": 0.001}, "volume": 1e6},
        }
    return {"last_price": spot, "oc": oc, "expiry": "2099-01-01", "underlying": name}


def test_analytics():
    m = analyze(make_chain(), 50, 10)
    assert m.atm == 25000
    assert m.call_wall == 25300 and m.put_wall == 24700
    assert m.pcr_window > 1.0           # puts heavier in our synthetic chain
    assert 24700 <= m.max_pain <= 25300
    assert m.iv_skew > 0                # put skew
    s, why = options_score(m)
    assert -1 <= s <= 1 and why
    print("analytics:", m.pcr_window, m.max_pain, m.iv_skew, m.buildup, s)


def test_bias_and_message():
    m = analyze(make_chain(), 50, 10)
    cues = {"ES=F": {"last": 6000, "prev": 5980, "chg_pct": 0.33, "label": "S&P 500 fut", "sign": 1},
            "^N225": {"last": 40000, "prev": 39800, "chg_pct": 0.5, "label": "Nikkei", "sign": 1},
            "BZ=F": {"last": 70, "prev": 71, "chg_pct": -1.4, "label": "Brent crude", "sign": -1},
            "^INDIAVIX": {"last": 12, "prev": 12.5, "chg_pct": -4, "label": "India VIX", "sign": -1}}
    gift = {"price": 25080, "gap_pts": 80, "gap_pct": 0.32, "source": "live"}
    flows = {"fii": {"net": 1850}, "dii": {"net": 900}, "date": "03-Oct-2026"}
    part = {"FII": {"fut_long_pct": 38.0, "fut_net": -60000, "fut_net_chg": 9000,
                    "call_net": 10000, "put_net": -5000, "call_net_chg": 20000, "put_net_chg": -10000},
            "Client": {"fut_long_pct": 66.0}}
    news = {"raw": 4, "norm": 0.33, "top": [{"title": "Wall Street closes higher on Fed hopes", "score": 2,
                                             "source": "ET"}], "events": ["FED"]}
    b = compute_bias(cues, gift, flows, part, [m], news)
    assert b.score > 0, b
    msg = report.premarket_message(b, cues, gift, flows, part, [m], news, None)
    assert "DAY BIAS" in msg and "NIFTY" in msg
    print(msg)


def test_intraday_alerts(tmp_path=None):
    from scanner.config import Underlying
    u = Underlying("TESTIDX", 0, "IDX_I", 50, "")
    p = intraday._state_path(u)
    if p.exists():
        p.unlink()
    first = intraday.detect(u, analyze(make_chain(name="TESTIDX"), 50, 10))
    assert first and "monitor started" in first[0]
    # force lookback window to compare against the open snapshot
    intraday.LOOKBACK_MIN = 0
    # big fresh call writing at 25100 + spot slips + wall shifts
    m2 = analyze(make_chain(spot=24960, call_wall=25100, bump=(25100, "ce", 9e6), name="TESTIDX"), 50, 10)
    alerts = intraday.detect(u, m2)
    joined = "\n".join(alerts)
    print(joined)
    assert "Resistance (call wall) shifted" in joined
    assert "OI change" in joined
    p.unlink()


if __name__ == "__main__":
    test_analytics()
    test_bias_and_message()
    test_intraday_alerts()
    print("\nALL TESTS PASSED")
