"""Entry point.

  python -m scanner run            # long-running scheduler (use this in production)
  python -m scanner premarket      # run the 08:30 brief now
  python -m scanner monitor        # one OI-monitor cycle now
  python -m scanner eod            # EOD summary now
  python -m scanner token          # check / refresh the Dhan token
  python -m scanner test-telegram  # send a test message
Add --dry-run to print instead of sending to Telegram.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta

from .config import DATA_DIR, IST, settings


def _hm(s: str):
    h, m = s.split(":")
    return int(h), int(m)


def _at(now: datetime, s: str) -> datetime:
    h, m = _hm(s)
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def run_forever(until: str | None = None):
    """Main loop. With `until` (HH:MM IST) the loop exits at that time - used by cloud
    runners that have a maximum job duration (GitHub Actions: 6 h)."""
    from . import jobs, nse
    log = logging.getLogger("scheduler")
    log.info("Scheduler started (IST). Brief %s, monitor %s-%s every %sm%s",
             settings.premarket_time, settings.monitor_start, settings.monitor_end,
             settings.monitor_interval_min, f", exits at {until}" if until else "")
    if until:
        now = datetime.now(IST)
        if now >= _at(now, until):
            log.info("Already past %s - nothing to do in this session", until)
            return
        try:
            if not nse.is_trading_day(now.date()):
                log.info("Not a trading day - exiting")
                return
        except Exception:
            if now.weekday() >= 5:
                return
    done: set[str] = set()
    next_monitor: datetime | None = None
    while True:
        now = datetime.now(IST)
        if until and now >= _at(now, until):
            log.info("Session end %s reached - exiting", until)
            return
        day = now.strftime("%Y%m%d")
        try:
            trading = nse.is_trading_day(now.date())
        except Exception:
            trading = now.weekday() < 5
        if trading:
            for name, t, fn in (("token", settings.token_refresh_time, jobs.job_token_refresh),
                                ("premarket", settings.premarket_time, jobs.job_premarket),
                                ("opening", settings.opening_check_time, jobs.job_opening_check),
                                ("eod", settings.eod_time, jobs.job_eod)):
                key = f"{day}-{name}"
                start = _at(now, t)
                # run once, if within 45 minutes after its slot (handles late start / sleep)
                if key not in done and start <= now < start + timedelta(minutes=45):
                    done.add(key)
                    try:
                        fn()
                    except Exception:
                        log.exception("job %s failed", name)
            ms, me = _at(now, settings.monitor_start), _at(now, settings.monitor_end)
            if ms <= now <= me and (next_monitor is None or now >= next_monitor):
                next_monitor = now + timedelta(minutes=settings.monitor_interval_min)
                try:
                    jobs.job_monitor()
                except Exception:
                    log.exception("monitor failed")
        if len(done) > 50:
            done = {k for k in done if k.startswith(day)}
        time.sleep(20)


def main(argv=None):
    p = argparse.ArgumentParser(prog="scanner")
    p.add_argument("cmd", choices=["run", "premarket", "monitor", "eod", "opening", "token", "test-telegram"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--until", help="run: exit at this IST time (HH:MM)")
    a = p.parse_args(argv)
    if a.dry_run:
        settings.dry_run = True
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(DATA_DIR / "scanner.log")])

    from . import jobs, telegram
    if a.cmd == "run":
        run_forever(a.until)
    elif a.cmd == "premarket":
        jobs.job_premarket()
    elif a.cmd == "monitor":
        jobs.job_monitor()
    elif a.cmd == "opening":
        jobs.job_opening_check()
    elif a.cmd == "eod":
        jobs.job_eod()
    elif a.cmd == "token":
        jobs.job_token_refresh()
    elif a.cmd == "test-telegram":
        ok = telegram.send(f"✅ Scanner connected - {datetime.now(IST):%d %b %H:%M} IST")
        print("sent" if ok else "failed")


if __name__ == "__main__":
    main()
