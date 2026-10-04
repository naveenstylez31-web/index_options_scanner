"""Option-chain analytics used by both the morning brief and the intraday monitor."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

from .config import IST


def chain_to_df(chain: dict) -> pd.DataFrame:
    rows = []
    for k, v in (chain.get("oc") or {}).items():
        try:
            strike = float(k)
        except ValueError:
            continue
        ce, pe = v.get("ce") or {}, v.get("pe") or {}
        row = {"strike": strike}
        for side, d in (("ce", ce), ("pe", pe)):
            g = d.get("greeks") or {}
            row.update({
                f"{side}_oi": float(d.get("oi") or 0), f"{side}_prev_oi": float(d.get("previous_oi") or 0),
                f"{side}_ltp": float(d.get("last_price") or 0),
                f"{side}_prev_close": float(d.get("previous_close_price") or 0),
                f"{side}_iv": float(d.get("implied_volatility") or 0),
                f"{side}_delta": float(g.get("delta") or 0), f"{side}_gamma": float(g.get("gamma") or 0),
                f"{side}_vol": float(d.get("volume") or 0),
            })
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
    if not df.empty:
        df["ce_doi"] = df["ce_oi"] - df["ce_prev_oi"]
        df["pe_doi"] = df["pe_oi"] - df["pe_prev_oi"]
    return df


def _max_pain(df: pd.DataFrame) -> float:
    strikes = df["strike"].values
    best, best_loss = strikes[0], math.inf
    for s in strikes:
        loss = ((s - df["strike"]).clip(lower=0) * df["ce_oi"]).sum() + \
               ((df["strike"] - s).clip(lower=0) * df["pe_oi"]).sum()
        if loss < best_loss:
            best, best_loss = s, loss
    return float(best)


def _iv_at_delta(df: pd.DataFrame, side: str, target: float) -> float:
    d = df[(df[f"{side}_iv"] > 0) & (df[f"{side}_delta"] != 0)]
    if d.empty:
        return 0.0
    i = (d[f"{side}_delta"] - target).abs().idxmin()
    return float(d.loc[i, f"{side}_iv"])


@dataclass
class ChainMetrics:
    underlying: str
    expiry: str
    spot: float
    atm: float
    dte: float
    pcr: float
    pcr_window: float
    pcr_doi: float               # put ΔOI / call ΔOI (day)
    max_pain: float
    call_wall: float
    call_wall_2: float
    put_wall: float
    put_wall_2: float
    call_wall_oi: float
    put_wall_oi: float
    top_ce_add: float            # strike with largest CE OI addition today
    top_pe_add: float
    atm_iv: float
    iv_skew: float               # 25d put IV - 25d call IV
    straddle: float
    exp_move_day: float          # 1-sd daily move from ATM IV
    net_gex_cr: float            # approx net gamma exposure (Rs cr per 1% move), + = mean-reverting
    ce_doi_window: float
    pe_doi_window: float
    buildup: str                 # dominant writer behaviour near ATM
    strikes: dict = field(default_factory=dict)   # strike -> (ce_oi, pe_oi, ce_ltp, pe_ltp) in window
    ts: str = ""

    def to_dict(self):
        return asdict(self)


def analyze(chain: dict, step: int, window: int = 10) -> ChainMetrics:
    df = chain_to_df(chain)
    spot = float(chain.get("last_price") or 0)
    if df.empty or not spot:
        raise ValueError("empty option chain")
    atm = round(spot / step) * step
    win = df[(df["strike"] >= atm - window * step) & (df["strike"] <= atm + window * step)]

    tot_ce, tot_pe = df["ce_oi"].sum(), df["pe_oi"].sum()
    w_ce, w_pe = win["ce_oi"].sum(), win["pe_oi"].sum()
    ce_doi, pe_doi = win["ce_doi"].sum(), win["pe_doi"].sum()

    above = win[win["strike"] >= atm]
    below = win[win["strike"] <= atm]
    cw = above.nlargest(2, "ce_oi") if not above.empty else win.nlargest(2, "ce_oi")
    pw = below.nlargest(2, "pe_oi") if not below.empty else win.nlargest(2, "pe_oi")

    atm_row = df.iloc[(df["strike"] - atm).abs().idxmin()]
    atm_iv = (atm_row["ce_iv"] + atm_row["pe_iv"]) / 2 if atm_row["ce_iv"] and atm_row["pe_iv"] \
        else max(atm_row["ce_iv"], atm_row["pe_iv"])
    straddle = atm_row["ce_ltp"] + atm_row["pe_ltp"]

    try:
        exp_dt = datetime.strptime(chain["expiry"], "%Y-%m-%d").replace(hour=15, minute=30, tzinfo=IST)
        dte = max((exp_dt - datetime.now(IST)).total_seconds() / 86400, 0.05)
    except (KeyError, ValueError):
        dte = 1.0

    # net GEX, standard street convention (call gamma +, put gamma -). Positive => moves tend to
    # get dampened (range day); negative => moves get amplified (trend/expansion day).
    gex = ((win["ce_gamma"] * win["ce_oi"]) - (win["pe_gamma"] * win["pe_oi"])).sum() * spot * spot * 0.01 / 1e7

    # writer behaviour near ATM (±4 strikes)
    near = df[(df["strike"] >= atm - 4 * step) & (df["strike"] <= atm + 4 * step)]
    ce_chg_px = (near["ce_ltp"] - near["ce_prev_close"]).mean()
    pe_chg_px = (near["pe_ltp"] - near["pe_prev_close"]).mean()
    n_ce, n_pe = near["ce_doi"].sum(), near["pe_doi"].sum()
    tags = []
    if n_ce > 0 and ce_chg_px < 0:
        tags.append("Call writing")
    elif n_ce < 0 and ce_chg_px > 0:
        tags.append("Call short-covering")
    if n_pe > 0 and pe_chg_px < 0:
        tags.append("Put writing")
    elif n_pe < 0 and pe_chg_px > 0:
        tags.append("Put short-covering")
    buildup = " + ".join(tags) or "Mixed"

    return ChainMetrics(
        underlying=chain.get("underlying", ""), expiry=chain.get("expiry", ""), spot=spot, atm=atm,
        dte=round(dte, 2),
        pcr=round(tot_pe / tot_ce, 3) if tot_ce else 0,
        pcr_window=round(w_pe / w_ce, 3) if w_ce else 0,
        pcr_doi=round(pe_doi / ce_doi, 2) if ce_doi > 0 else (9.99 if pe_doi > 0 else 0),
        max_pain=_max_pain(df),
        call_wall=float(cw.iloc[0]["strike"]), call_wall_2=float(cw.iloc[-1]["strike"]),
        put_wall=float(pw.iloc[0]["strike"]), put_wall_2=float(pw.iloc[-1]["strike"]),
        call_wall_oi=float(cw.iloc[0]["ce_oi"]), put_wall_oi=float(pw.iloc[0]["pe_oi"]),
        top_ce_add=float(win.loc[win["ce_doi"].idxmax(), "strike"]),
        top_pe_add=float(win.loc[win["pe_doi"].idxmax(), "strike"]),
        atm_iv=round(float(atm_iv), 2),
        iv_skew=round(_iv_at_delta(df, "pe", -0.25) - _iv_at_delta(df, "ce", 0.25), 2),
        straddle=round(float(straddle), 2),
        exp_move_day=round(spot * atm_iv / 100 * math.sqrt(1 / 365), 1),
        net_gex_cr=round(float(gex), 1),
        ce_doi_window=float(ce_doi), pe_doi_window=float(pe_doi),
        buildup=buildup,
        strikes={str(int(r.strike)): [r.ce_oi, r.pe_oi, r.ce_ltp, r.pe_ltp, r.ce_iv, r.pe_iv]
                 for r in win.itertuples()},
        ts=chain.get("fetched_at", datetime.now(IST).isoformat(timespec="seconds")),
    )


def options_score(m: ChainMetrics) -> tuple[float, list[str]]:
    """-1..+1 directional read from the option chain, with reasons."""
    s, why = 0.0, []
    # PCR (window) - moderate readings directional, extremes contrarian
    if m.pcr_window >= 1.6:
        s -= 0.1; why.append(f"PCR {m.pcr_window} extreme - stretched, watch for put unwinding")
    elif m.pcr_window >= 1.15:
        s += 0.3; why.append(f"PCR {m.pcr_window} - put writers in control")
    elif m.pcr_window <= 0.6:
        s += 0.05; why.append(f"PCR {m.pcr_window} oversold extreme")
    elif m.pcr_window <= 0.85:
        s -= 0.3; why.append(f"PCR {m.pcr_window} - call writers in control")
    # fresh writing today
    if m.pcr_doi >= 1.5:
        s += 0.25; why.append("Fresh put writing > call writing")
    elif 0 < m.pcr_doi <= 0.67:
        s -= 0.25; why.append("Fresh call writing > put writing")
    # spot vs max pain (pull towards max pain near expiry)
    if m.dte <= 2 and m.max_pain:
        diff = (m.max_pain - m.spot) / m.spot * 100
        if abs(diff) > 0.3:
            s += 0.15 * (1 if diff > 0 else -1)
            why.append(f"Expiry near - max pain {int(m.max_pain)} pulls {'up' if diff > 0 else 'down'}")
    # position inside the range
    rng = m.call_wall - m.put_wall
    if rng > 0:
        pos = (m.spot - m.put_wall) / rng
        if pos < 0.25:
            s += 0.1; why.append(f"Spot near put wall {int(m.put_wall)} (support)")
        elif pos > 0.75:
            s -= 0.1; why.append(f"Spot near call wall {int(m.call_wall)} (resistance)")
    if "Put writing" in m.buildup and "Call writing" not in m.buildup:
        s += 0.15
    if "Call writing" in m.buildup and "Put writing" not in m.buildup:
        s -= 0.15
    return max(-1, min(1, s)), why
