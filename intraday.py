"""Intraday OI change detector. Called every MONITOR_INTERVAL_MIN during market hours."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from .analytics import ChainMetrics, options_score
from .config import DATA_DIR, IST, Underlying, settings

log = logging.getLogger(__name__)
LOOKBACK_MIN = 15


def _state_path(u: Underlying):
    return DATA_DIR / f"intraday_{datetime.now(IST):%Y%m%d}_{u.name}.json"


def load_state(u: Underlying) -> dict:
    p = _state_path(u)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"open": None, "history": [], "alerted": {}, "pcr_ref": None, "bias_ref": None,
            "last_digest_hour": None}


def save_state(u: Underlying, st: dict):
    st["history"] = st["history"][-40:]
    _state_path(u).write_text(json.dumps(st))


def _cool(st: dict, key: str, now: datetime) -> bool:
    """True if the alert key is still in cooldown."""
    t = st["alerted"].get(key)
    return bool(t) and now - datetime.fromisoformat(t) < timedelta(minutes=settings.cooldown_min)


def _mark(st, key, now):
    st["alerted"][key] = now.isoformat(timespec="seconds")


def _lakh(x: float) -> str:
    return f"{x / 1e5:+.1f}L" if abs(x) < 1e7 else f"{x / 1e7:+.2f}Cr"


def _interpret(side: str, d_oi: float, d_px: float) -> tuple[str, str]:
    """(label, direction emoji) for an OI/price combination."""
    if side == "CE":
        if d_oi > 0 and d_px <= 0:
            return "Call WRITING (resistance building)", "🔴"
        if d_oi > 0 and d_px > 0:
            return "Call BUYING (upside bets)", "🟢"
        if d_oi < 0 and d_px > 0:
            return "Call SHORT-COVERING (bullish)", "🟢"
        return "Call long unwinding", "🟠"
    if d_oi > 0 and d_px <= 0:
        return "Put WRITING (support building)", "🟢"
    if d_oi > 0 and d_px > 0:
        return "Put BUYING (downside bets)", "🔴"
    if d_oi < 0 and d_px > 0:
        return "Put SHORT-COVERING (bearish)", "🔴"
    return "Put long unwinding", "🟠"


def detect(u: Underlying, m: ChainMetrics) -> list[str]:
    """Compare the new snapshot with history; return alert messages (HTML)."""
    now = datetime.now(IST)
    st = load_state(u)
    snap = {"ts": now.isoformat(timespec="seconds"), **{k: getattr(m, k) for k in (
        "spot", "pcr_window", "max_pain", "call_wall", "put_wall", "atm_iv", "straddle",
        "call_wall_oi", "put_wall_oi", "net_gex_cr", "buildup")}, "strikes": m.strikes}
    alerts: list[str] = []
    step = u.strike_step

    if st["open"] is None:
        st["open"] = snap
        st["pcr_ref"] = m.pcr_window
        st["bias_ref"] = options_score(m)[0]
        st["history"].append(snap)
        save_state(u, st)
        alerts.append(
            f"🟦 <b>{u.name} OI monitor started</b> ({m.expiry})\n"
            f"Spot {m.spot:,.1f} | PCR {m.pcr_window} | Max pain {int(m.max_pain)}\n"
            f"Resistance {int(m.call_wall)} / {int(m.call_wall_2)} | Support {int(m.put_wall)} / {int(m.put_wall_2)}")
        return alerts

    prev = st["history"][-1]
    # snapshot ~LOOKBACK_MIN ago for slower build-ups
    ref = prev
    for h in reversed(st["history"]):
        if now - datetime.fromisoformat(h["ts"]) >= timedelta(minutes=LOOKBACK_MIN):
            ref = h
            break

    # 1) PCR regime shift
    if st["pcr_ref"] is not None and abs(m.pcr_window - st["pcr_ref"]) >= settings.pcr_shift:
        up = m.pcr_window > st["pcr_ref"]
        alerts.append(f"{'🟢' if up else '🔴'} <b>{u.name} PCR shift</b> {st['pcr_ref']} → <b>{m.pcr_window}</b> "
                      f"({'puts being written - bullish tilt' if up else 'calls being written - bearish tilt'})\n"
                      f"Spot {m.spot:,.1f}")
        st["pcr_ref"] = m.pcr_window

    # 2) Wall shifts
    for kind, old, new, emo_up in (("Resistance (call wall)", prev["call_wall"], m.call_wall, "🟢"),
                                    ("Support (put wall)", prev["put_wall"], m.put_wall, "🟢")):
        if new != old and not _cool(st, f"wall_{kind}", now):
            moved_up = new > old
            emo = emo_up if moved_up else "🔴"
            alerts.append(f"{emo} <b>{u.name} {kind} shifted</b> {int(old)} → <b>{int(new)}</b> "
                          f"({'higher' if moved_up else 'lower'})\nSpot {m.spot:,.1f}")
            _mark(st, f"wall_{kind}", now)

    # 3) Spot breaching walls
    if m.spot > prev["call_wall"] and prev["spot"] <= prev["call_wall"] and not _cool(st, "breakout", now):
        alerts.append(f"🚀 <b>{u.name} BREAKOUT</b> above call wall {int(prev['call_wall'])} - spot {m.spot:,.1f}. "
                      f"Watch for call short-covering acceleration.")
        _mark(st, "breakout", now)
    if m.spot < prev["put_wall"] and prev["spot"] >= prev["put_wall"] and not _cool(st, "breakdown", now):
        alerts.append(f"⚠️ <b>{u.name} BREAKDOWN</b> below put wall {int(prev['put_wall'])} - spot {m.spot:,.1f}. "
                      f"Watch for put unwinding acceleration.")
        _mark(st, "breakdown", now)

    # 4) Strike-level OI surges vs lookback snapshot
    surges = []
    for k, (ce_oi, pe_oi, ce_px, pe_px, *_ ) in m.strikes.items():
        old = ref["strikes"].get(k)
        if not old:
            continue
        for side, cur, o, px, opx in (("CE", ce_oi, old[0], ce_px, old[2]), ("PE", pe_oi, old[1], pe_px, old[3])):
            d = cur - o
            pct = 100 * d / o if o else (100 if d > 0 else 0)
            if abs(d) >= settings.strike_oi_min_lakh * 1e5 and abs(pct) >= settings.strike_oi_surge_pct:
                surges.append((abs(d), k, side, d, pct, px - opx))
    surges.sort(reverse=True)
    lines = []
    for _, k, side, d, pct, dpx in surges[:4]:
        key = f"surge_{k}_{side}_{'up' if d > 0 else 'dn'}"
        if _cool(st, key, now):
            continue
        lbl, emo = _interpret(side, d, dpx)
        lines.append(f"{emo} {k} {side}: OI {_lakh(d)} ({pct:+.0f}%) - {lbl}")
        _mark(st, key, now)
    if lines:
        alerts.append(f"📊 <b>{u.name} OI change (last {LOOKBACK_MIN}m)</b> spot {m.spot:,.1f}\n" + "\n".join(lines))

    # 5) IV spike / crush
    if abs(m.atm_iv - ref["atm_iv"]) >= settings.iv_spike_pts and not _cool(st, "iv", now):
        up = m.atm_iv > ref["atm_iv"]
        alerts.append(f"{'⚡' if up else '🧊'} <b>{u.name} ATM IV {'spike' if up else 'crush'}</b> "
                      f"{ref['atm_iv']} → {m.atm_iv} | straddle {m.straddle}")
        _mark(st, "iv", now)

    # 6) Max pain migration
    if abs(m.max_pain - prev["max_pain"]) >= 2 * step and not _cool(st, "maxpain", now):
        alerts.append(f"🎯 <b>{u.name} Max pain moved</b> {int(prev['max_pain'])} → {int(m.max_pain)}")
        _mark(st, "maxpain", now)

    # 7) Option-chain bias flip
    s, why = options_score(m)
    if st["bias_ref"] is not None and s * st["bias_ref"] < 0 and abs(s) >= 0.25 and not _cool(st, "flip", now):
        alerts.append(f"🔁 <b>{u.name} OI BIAS FLIP → {'BULLISH' if s > 0 else 'BEARISH'}</b> ({s:+.2f})\n"
                      + "\n".join(f"• {w}" for w in why[:3]))
        st["bias_ref"] = s
        _mark(st, "flip", now)

    # 8) Hourly digest
    if settings.hourly_digest and now.minute < settings.monitor_interval_min + 1 \
            and st["last_digest_hour"] != now.hour and now.hour >= 10:
        o = st["open"]
        alerts.append(
            f"🕐 <b>{u.name} {now:%H:%M} digest</b>\n"
            f"Spot {o['spot']:,.1f} → {m.spot:,.1f} ({m.spot - o['spot']:+.0f})\n"
            f"PCR {o['pcr_window']} → {m.pcr_window} | Max pain {int(m.max_pain)}\n"
            f"Range {int(m.put_wall)} – {int(m.call_wall)} | ATM IV {m.atm_iv} | {m.buildup}\n"
            f"OI bias: {'Bullish' if s > 0.1 else 'Bearish' if s < -0.1 else 'Neutral'} ({s:+.2f})")
        st["last_digest_hour"] = now.hour

    st["history"].append(snap)
    save_state(u, st)
    return alerts
