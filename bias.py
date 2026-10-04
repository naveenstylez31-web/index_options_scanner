"""Composite day-bias engine. Each factor gives -1..+1; weights sum to 100."""
from __future__ import annotations

from dataclasses import dataclass, field

from .analytics import ChainMetrics, options_score

WEIGHTS = {
    "gift_gap": 22,       # GIFT Nifty vs Nifty close
    "global": 12,         # US futures + Asia
    "macro": 8,           # crude, USDINR, DXY, US10Y
    "fii_cash": 10,       # FII/DII cash flows
    "fii_deriv": 16,      # FII index futures long% + change
    "options": 22,        # option-chain positioning (avg of Nifty & Sensex)
    "news": 10,           # news sentiment
}


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


@dataclass
class Factor:
    name: str
    score: float
    note: str
    available: bool = True


@dataclass
class BiasResult:
    score: float                      # -100..+100
    label: str
    confidence: str
    regime: str                       # range / trend expectation
    factors: list[Factor] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def compute_bias(cues: dict, gift: dict, flows: dict | None, part: dict | None,
                 metrics: list[ChainMetrics], news: dict | None) -> BiasResult:
    f: list[Factor] = []

    # 1. GIFT Nifty gap
    if gift:
        g = gift["gap_pct"]
        tag = "" if gift.get("source") == "live" else " (implied est.)"
        f.append(Factor("gift_gap", _clip(g / 0.6), f"GIFT {g:+.2f}% ({gift['gap_pts']:+.0f} pts){tag}"))
    else:
        f.append(Factor("gift_gap", 0, "GIFT n/a", False))

    # 2. Global equities
    def chg(s):
        return cues.get(s, {}).get("chg_pct")
    us = [x for x in (chg("ES=F"), chg("NQ=F")) if x is not None]
    asia = [x for x in (chg("^N225"), chg("^HSI"), chg("^KS11")) if x is not None]
    if us or asia:
        us_avg = sum(us) / len(us) if us else 0
        asia_avg = sum(asia) / len(asia) if asia else 0
        f.append(Factor("global", _clip((0.65 * us_avg + 0.35 * asia_avg) / 0.8),
                        f"US fut {us_avg:+.2f}%, Asia {asia_avg:+.2f}%"))
    else:
        f.append(Factor("global", 0, "global n/a", False))

    # 3. Macro (risk-off when crude/USD/yields rise)
    parts, notes = [], []
    for sym, scale, lbl in (("BZ=F", 1.5, "Brent"), ("INR=X", 0.3, "USDINR"),
                            ("DX-Y.NYB", 0.5, "DXY"), ("^TNX", 2.0, "US10Y")):
        c = chg(sym)
        if c is not None:
            parts.append(-_clip(c / scale))
            notes.append(f"{lbl} {c:+.2f}%")
    f.append(Factor("macro", sum(parts) / len(parts) if parts else 0, ", ".join(notes) or "n/a", bool(parts)))

    # 4. FII / DII cash
    if flows and "fii" in flows:
        fii, dii = flows["fii"]["net"], flows.get("dii", {}).get("net", 0)
        s = _clip(fii / 4000) * 0.75 + _clip((fii + dii) / 5000) * 0.25
        f.append(Factor("fii_cash", s, f"FII {fii:+,.0f} cr, DII {dii:+,.0f} cr ({flows.get('date', '')})"))
    else:
        f.append(Factor("fii_cash", 0, "FII/DII n/a", False))

    # 5. FII derivatives positioning (index futures long %, change, options)
    fii_p = (part or {}).get("FII")
    if fii_p:
        lp = fii_p["fut_long_pct"]
        lvl = _clip((lp - 45) / 20)                  # 25% -> -1, 65% -> +1
        chg_ct = fii_p.get("fut_net_chg", 0)
        mom = _clip(chg_ct / 20000)
        opt = _clip((fii_p.get("call_net_chg", 0) - fii_p.get("put_net_chg", 0)) / 150000)
        s = 0.5 * lvl + 0.35 * mom + 0.15 * opt
        f.append(Factor("fii_deriv", _clip(s),
                        f"FII idx-fut long {lp:.1f}%, net chg {chg_ct:+,.0f} contracts"))
    else:
        f.append(Factor("fii_deriv", 0, "participant OI n/a", False))

    # 6. Option chain
    reasons: list[str] = []
    if metrics:
        sc = []
        for m in metrics:
            s, why = options_score(m)
            sc.append(s)
            reasons += [f"{m.underlying}: {w}" for w in why[:3]]
        f.append(Factor("options", sum(sc) / len(sc),
                        " | ".join(f"{m.underlying} PCR {m.pcr_window}" for m in metrics)))
    else:
        f.append(Factor("options", 0, "option chain n/a", False))

    # 7. News
    if news and news.get("top") is not None:
        f.append(Factor("news", news["norm"], f"news score {news['raw']:+.1f}"))
    else:
        f.append(Factor("news", 0, "news n/a", False))

    avail = [x for x in f if x.available]
    wsum = sum(WEIGHTS[x.name] for x in avail) or 1
    score = 100 * sum(WEIGHTS[x.name] * x.score for x in avail) / wsum

    if score >= 40:
        label = "STRONG BULLISH"
    elif score >= 15:
        label = "BULLISH"
    elif score > -15:
        label = "NEUTRAL / RANGE"
    elif score > -40:
        label = "BEARISH"
    else:
        label = "STRONG BEARISH"

    # confidence = weighted agreement of factors with the final sign x data coverage
    sign = 1 if score >= 0 else -1
    agree = sum(WEIGHTS[x.name] for x in avail if x.score * sign > 0.1)
    coverage = wsum / 100
    conf_v = (agree / wsum) * coverage
    confidence = "HIGH" if conf_v >= 0.6 and abs(score) >= 25 else "MEDIUM" if conf_v >= 0.4 else "LOW"

    # regime from gamma + VIX
    vix = cues.get("^INDIAVIX", {})
    gex_pos = metrics and sum(m.net_gex_cr for m in metrics) > 0
    if vix.get("chg_pct", 0) > 5 or (metrics and not gex_pos):
        regime = "Expansion risk - trend/large-range day more likely (negative gamma or VIX spike)"
    elif gex_pos and abs(score) < 25:
        regime = "Mean-reverting - range between walls likely (positive gamma)"
    else:
        regime = "Directional drift with dips/rallies being bought/sold at OI walls"

    return BiasResult(round(score, 1), label, confidence, regime, f, reasons)
