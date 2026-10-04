"""The jobs the scheduler runs."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from . import global_cues, news, nse, report, telegram
from .analytics import analyze
from .bias import compute_bias
from .config import DATA_DIR, IST, settings
from .dhan import DhanAuthError, DhanClient, TokenManager
from .intraday import detect, load_state

log = logging.getLogger(__name__)
_client: DhanClient | None = None
_auth_alert_day = None


def client() -> DhanClient:
    global _client
    if _client is None:
        _client = DhanClient(TokenManager())
    return _client


def _chains() -> list:
    out = []
    for u in settings.underlyings:
        try:
            out.append(analyze(client().option_chain(u), u.strike_step, settings.strikes_window))
        except DhanAuthError:
            raise
        except Exception as e:
            log.error("%s chain failed: %s", u.name, e)
    return out


def job_token_refresh():
    try:
        tm = client().tokens
        tm.ensure()
        log.info("Dhan token OK till %s", tm.expiry)
    except DhanAuthError as e:
        telegram.send(f"🔑 <b>Dhan login needed</b>\n{e}")


def job_premarket():
    log.info("Running pre-market scan")
    cues = global_cues.global_cues()
    nifty_prev = cues.get("^NSEI", {}).get("last")
    gift = global_cues.gift_nifty(nifty_prev, cues)
    flows = nse.fii_dii_cash()
    part = nse.participant_oi()
    try:
        metrics = _chains()
    except DhanAuthError as e:
        telegram.send(f"🔑 <b>Dhan login needed</b> - brief sent without option chain\n{e}")
        metrics = []
    items = news.fetch_news()
    nscore = news.score_news(items)
    bias = compute_bias(cues, gift, flows, part, metrics, nscore)

    narrative = None
    if settings.anthropic_api_key:
        ctx = {
            "bias": {"label": bias.label, "score": bias.score, "regime": bias.regime,
                     "factors": [(f.name, round(f.score, 2), f.note) for f in bias.factors]},
            "gift": gift, "flows": flows, "fii_deriv": (part or {}).get("FII"),
            "chains": [{k: v for k, v in m.to_dict().items() if k != "strikes"} for m in metrics],
            "headlines": [i["title"] for i in items[:25]], "events": nscore["events"],
        }
        narrative = news.ai_narrative(json.dumps(ctx, default=str)[:12000])

    msg = report.premarket_message(bias, cues, gift, flows, part, metrics, nscore, narrative)
    telegram.send(msg)
    (DATA_DIR / f"premarket_{datetime.now(IST):%Y%m%d}.json").write_text(json.dumps({
        "label": bias.label, "score": bias.score, "confidence": bias.confidence,
        "prev_close": {"NIFTY": nifty_prev, "SENSEX": cues.get("^BSESN", {}).get("last")}}, default=str))
    return msg


def job_opening_check():
    p = DATA_DIR / f"premarket_{datetime.now(IST):%Y%m%d}.json"
    if not p.exists():
        return
    pm = json.loads(p.read_text())
    from .bias import BiasResult
    b = BiasResult(pm["score"], pm["label"], pm["confidence"], "")
    try:
        metrics = _chains()
    except DhanAuthError as e:
        telegram.send(f"🔑 {e}")
        return
    telegram.send(report.opening_check_message(b, metrics, pm.get("prev_close", {})))


def job_monitor():
    for u in settings.underlyings:
        try:
            m = analyze(client().option_chain(u), u.strike_step, settings.strikes_window)
        except DhanAuthError as e:
            global _auth_alert_day
            today = datetime.now(IST).date()
            if _auth_alert_day != today:
                _auth_alert_day = today
                telegram.send(f"🔑 <b>Dhan login needed - OI monitor paused</b>\n{e}")
            return
        except Exception as e:
            log.error("monitor %s: %s", u.name, e)
            continue
        for a in detect(u, m):
            telegram.send(a)


def job_eod():
    try:
        metrics = _chains()
    except DhanAuthError:
        return
    states = {u.name: load_state(u) for u in settings.underlyings}
    telegram.send(report.eod_message(metrics, states), silent=True)
