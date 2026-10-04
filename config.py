"""Central configuration. Everything is read from environment variables (.env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # dotenv optional
    pass

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(float(_env(name, str(default))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Underlying:
    name: str            # display name
    security_id: int     # Dhan UnderlyingScrip
    segment: str         # Dhan UnderlyingSeg
    strike_step: int     # strike interval
    yf_symbol: str       # Yahoo symbol for prev close / fallback


@dataclass
class Settings:
    # --- Dhan ---
    dhan_client_id: str = field(default_factory=lambda: _env("DHAN_CLIENT_ID"))
    dhan_access_token: str = field(default_factory=lambda: _env("DHAN_ACCESS_TOKEN"))
    dhan_pin: str = field(default_factory=lambda: _env("DHAN_PIN"))
    dhan_totp_secret: str = field(default_factory=lambda: _env("DHAN_TOTP_SECRET"))

    # --- Telegram ---
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))

    # --- Optional AI narrative (Claude API) ---
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))

    # --- Optional GIFT Nifty override source ---
    gift_nifty_url: str = field(default_factory=lambda: _env("GIFT_NIFTY_URL"))

    # --- Schedule (IST, HH:MM) ---
    premarket_time: str = field(default_factory=lambda: _env("PREMARKET_TIME", "08:30"))
    token_refresh_time: str = field(default_factory=lambda: _env("TOKEN_REFRESH_TIME", "08:10"))
    opening_check_time: str = field(default_factory=lambda: _env("OPENING_CHECK_TIME", "09:25"))
    eod_time: str = field(default_factory=lambda: _env("EOD_TIME", "15:40"))
    monitor_start: str = field(default_factory=lambda: _env("MONITOR_START", "09:17"))
    monitor_end: str = field(default_factory=lambda: _env("MONITOR_END", "15:30"))
    monitor_interval_min: int = field(default_factory=lambda: _int("MONITOR_INTERVAL_MIN", 3))

    # --- Intraday alert thresholds ---
    strikes_window: int = field(default_factory=lambda: _int("STRIKES_WINDOW", 10))          # ±N strikes around ATM
    pcr_shift: float = field(default_factory=lambda: _float("PCR_SHIFT_ALERT", 0.10))
    strike_oi_surge_pct: float = field(default_factory=lambda: _float("STRIKE_OI_SURGE_PCT", 25))
    strike_oi_min_lakh: float = field(default_factory=lambda: _float("STRIKE_OI_MIN_LAKH", 10))  # min abs ΔOI (lakh units)
    iv_spike_pts: float = field(default_factory=lambda: _float("IV_SPIKE_PTS", 1.5))
    cooldown_min: int = field(default_factory=lambda: _int("ALERT_COOLDOWN_MIN", 15))
    hourly_digest: bool = field(default_factory=lambda: _env("HOURLY_DIGEST", "true").lower() == "true")

    underlyings: tuple = (
        Underlying("NIFTY", _int("NIFTY_SECURITY_ID", 13), "IDX_I", 50, "^NSEI"),
        Underlying("SENSEX", _int("SENSEX_SECURITY_ID", 51), "IDX_I", 100, "^BSESN"),
    )

    dry_run: bool = field(default_factory=lambda: _env("DRY_RUN", "false").lower() == "true")


settings = Settings()
