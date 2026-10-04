"""Builds the pre-market brief and EOD summary messages (Telegram HTML)."""
from __future__ import annotations

import html
from datetime import datetime

from .analytics import ChainMetrics
from .bias import BiasResult
from .config import IST

EMO = {"STRONG BULLISH": "🟢🟢", "BULLISH": "🟢", "NEUTRAL / RANGE": "🟡", "BEARISH": "🔴",
       "STRONG BEARISH": "🔴🔴"}
DISCLAIMER = "<i>Automated market read for information only - not investment advice.</i>"


def _bar(score: float) -> str:
    n = int(round((score + 100) / 20))  # 0..10
    return "▰" * n + "▱" * (10 - n)


def _cr(x: float) -> str:
    return f"{x / 1e5:,.1f}L"


def chain_block(m: ChainMetrics) -> str:
    rng_lo, rng_hi = m.spot - m.exp_move_day, m.spot + m.exp_move_day
    return (
        f"<b>{m.underlying}</b> {m.spot:,.1f} | exp {m.expiry} ({m.dte:.1f}d)\n"
        f"• PCR {m.pcr_window} (all {m.pcr}) | ΔOI P/C {m.pcr_doi}\n"
        f"• Resistance <b>{int(m.call_wall)}</b> ({_cr(m.call_wall_oi)}), {int(m.call_wall_2)}\n"
        f"• Support <b>{int(m.put_wall)}</b> ({_cr(m.put_wall_oi)}), {int(m.put_wall_2)}\n"
        f"• Max pain {int(m.max_pain)} | Fresh adds: CE {int(m.top_ce_add)}, PE {int(m.top_pe_add)}\n"
        f"• ATM IV {m.atm_iv} | skew {m.iv_skew:+} | straddle {m.straddle}\n"
        f"• 1σ day range {rng_lo:,.0f} – {rng_hi:,.0f} | GEX {m.net_gex_cr:+} | {m.buildup}"
    )


def premarket_message(bias: BiasResult, cues: dict, gift: dict, flows: dict | None,
                      part: dict | None, metrics: list[ChainMetrics], news: dict | None,
                      narrative: str | None) -> str:
    now = datetime.now(IST)
    lines = [f"📈 <b>INDEX OPTIONS PRE-MARKET SCAN</b> — {now:%a %d %b %Y, %H:%M} IST", "",
             f"{EMO.get(bias.label, '')} <b>DAY BIAS: {bias.label}</b>  ({bias.score:+.0f}/100)",
             f"{_bar(bias.score)}  Confidence: <b>{bias.confidence}</b>",
             f"Regime: {bias.regime}", ""]

    if narrative:
        lines += ["🧠 <b>Desk note</b>", html.escape(narrative), ""]

    lines.append("🌐 <b>Cues</b>")
    if gift:
        src = "live" if gift.get("source") == "live" else "implied est."
        lines.append(f"• GIFT Nifty {gift['price']:,.0f} ({gift['gap_pts']:+.0f} / {gift['gap_pct']:+.2f}%, {src})")
    for s in ("ES=F", "NQ=F", "^N225", "^HSI", "BZ=F", "INR=X", "DX-Y.NYB", "^TNX", "^INDIAVIX"):
        q = cues.get(s)
        if q:
            lines.append(f"• {q['label']} {q['last']:,.2f} ({q['chg_pct']:+.2f}%)")
    lines.append("")

    lines.append("💰 <b>Flows & positioning</b>")
    if flows and "fii" in flows:
        lines.append(f"• FII cash {flows['fii']['net']:+,.0f} cr | DII {flows.get('dii', {}).get('net', 0):+,.0f} cr "
                     f"({flows.get('date', '')})")
    fii = (part or {}).get("FII")
    if fii:
        lines.append(f"• FII index futures long {fii['fut_long_pct']:.1f}% "
                     f"(net {fii['fut_net']:+,.0f}, chg {fii.get('fut_net_chg', 0):+,.0f})")
        lines.append(f"• FII idx options: calls net {fii['call_net']:+,.0f}, puts net {fii['put_net']:+,.0f}")
    cl = (part or {}).get("Client")
    if cl:
        lines.append(f"• Retail idx futures long {cl['fut_long_pct']:.1f}% (contrarian gauge)")
    lines.append("")

    if metrics:
        lines.append("🧮 <b>Option chain (prev. session OI)</b>")
        for m in metrics:
            lines += [chain_block(m), ""]

    lines.append("⚖️ <b>Score breakdown</b>")
    for f in bias.factors:
        mark = "🟢" if f.score > 0.1 else "🔴" if f.score < -0.1 else "⚪"
        lines.append(f"{mark} {f.name.replace('_', ' ')} {f.score:+.2f} — {html.escape(f.note)}"
                     + ("" if f.available else " (skipped)"))
    lines.append("")

    if news:
        if news.get("events"):
            lines.append(f"📅 <b>Event risk:</b> {', '.join(news['events'][:6])}")
        if news.get("top"):
            lines.append("📰 <b>Market-moving headlines</b>")
            for it in news["top"][:5]:
                mark = "🟢" if it["score"] > 0 else "🔴"
                lines.append(f"{mark} {html.escape(it['title'][:140])} <i>({it['source']})</i>")
        lines.append("")

    if metrics:
        lines.append("🎯 <b>Levels that decide the day</b>")
        for m in metrics:
            lines.append(f"• {m.underlying}: above {int(m.call_wall)} → short-covering leg; "
                         f"below {int(m.put_wall)} → put unwinding leg; between = range")
        lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def opening_check_message(bias: BiasResult, metrics: list[ChainMetrics], prev_close: dict) -> str:
    lines = [f"🔔 <b>OPENING CHECK</b> {datetime.now(IST):%H:%M} — pre-market bias was <b>{bias.label}</b>"]
    for m in metrics:
        pc = prev_close.get(m.underlying)
        gap = f"{m.spot - pc:+.0f} pts vs prev close" if pc else ""
        lines.append(f"• {m.underlying} {m.spot:,.1f} {gap} | PCR {m.pcr_window} | {m.buildup}")
        lines.append(f"  Range {int(m.put_wall)} – {int(m.call_wall)} | ATM IV {m.atm_iv}")
    return "\n".join(lines)


def eod_message(metrics: list[ChainMetrics], states: dict) -> str:
    lines = [f"🏁 <b>EOD OI SUMMARY</b> — {datetime.now(IST):%a %d %b}"]
    for m in metrics:
        o = (states.get(m.underlying) or {}).get("open") or {}
        lines.append(
            f"\n<b>{m.underlying}</b> close {m.spot:,.1f}"
            + (f" (from {o.get('spot', 0):,.1f} at open, {m.spot - o.get('spot', m.spot):+.0f})" if o else ""))
        lines.append(f"• PCR {o.get('pcr_window', '-')} → {m.pcr_window} | Max pain {int(m.max_pain)}")
        lines.append(f"• Tomorrow's walls: R {int(m.call_wall)} / {int(m.call_wall_2)} | "
                     f"S {int(m.put_wall)} / {int(m.put_wall_2)}")
        lines.append(f"• Day ΔOI near ATM: CE {_cr(m.ce_doi_window)} | PE {_cr(m.pe_doi_window)} | {m.buildup}")
    lines.append("\n" + DISCLAIMER)
    return "\n".join(lines)
