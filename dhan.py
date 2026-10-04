"""Dhan API v2: automatic 24h token management + option chain.

Token strategy (in order):
  1. Cached token in data/dhan_token.json  -> validated with /v2/profile
  2. If valid but expiring within 3h       -> /v2/RenewToken (works for tokens made on Dhan Web)
  3. If missing/expired and PIN+TOTP set   -> auth.dhan.co/app/generateAccessToken (fully headless)
  4. Else fall back to DHAN_ACCESS_TOKEN in .env, and alert on Telegram if that is dead too.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

import requests

from .config import DATA_DIR, IST, Underlying, settings

log = logging.getLogger(__name__)

API = "https://api.dhan.co/v2"
AUTH = "https://auth.dhan.co/app/generateAccessToken"
TOKEN_FILE = DATA_DIR / "dhan_token.json"


class DhanAuthError(RuntimeError):
    pass


def _parse_expiry(s: str | None) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def _jwt_expiry(token: str) -> datetime | None:
    """Read 'exp' from the JWT payload (no signature check needed - just a hint)."""
    import base64
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return datetime.fromtimestamp(exp, IST) if exp else None
    except Exception:
        return None


class TokenManager:
    def __init__(self):
        self.token: str = ""
        self.expiry: datetime | None = None
        self._load()

    # ---------- persistence ----------
    def _load(self):
        if TOKEN_FILE.exists():
            try:
                d = json.loads(TOKEN_FILE.read_text())
                self.token, self.expiry = d.get("token", ""), _parse_expiry(d.get("expiry"))
            except Exception:
                pass
        if not self.token and settings.dhan_access_token:
            self.token = settings.dhan_access_token
            self.expiry = _jwt_expiry(self.token)

    def _save(self):
        TOKEN_FILE.write_text(json.dumps({
            "token": self.token,
            "expiry": self.expiry.strftime("%Y-%m-%dT%H:%M:%S") if self.expiry else None,
        }))
        try:
            TOKEN_FILE.chmod(0o600)
        except OSError:
            pass

    # ---------- checks ----------
    def is_valid(self) -> bool:
        if not self.token:
            return False
        try:
            r = requests.get(f"{API}/profile", headers={"access-token": self.token}, timeout=15)
            if r.status_code != 200:
                return False
            d = r.json()
            exp = _parse_expiry(d.get("tokenValidity")) or _jwt_expiry(self.token)
            if exp:
                self.expiry = exp
            if str(d.get("dataPlan", "")).lower() not in ("active", ""):
                log.warning("Dhan Data API plan is not active - option chain calls will fail.")
            return True
        except requests.RequestException as e:
            log.warning("Profile check failed: %s", e)
            return False

    # ---------- refresh paths ----------
    def _renew(self) -> bool:
        try:
            r = requests.get(f"{API}/RenewToken", headers={
                "access-token": self.token, "dhanClientId": settings.dhan_client_id}, timeout=15)
            if r.status_code == 200:
                d = r.json()
                tok = d.get("accessToken") or d.get("token") or (d.get("data") or {}).get("accessToken")
                if tok:
                    self.token = tok
                    self.expiry = _parse_expiry(d.get("expiryTime")) or _jwt_expiry(tok)
                    self._save()
                    log.info("Dhan token renewed, valid till %s", self.expiry)
                    return True
            log.warning("RenewToken failed: %s %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning("RenewToken error: %s", e)
        return False

    def _generate_with_totp(self) -> bool:
        if not (settings.dhan_pin and settings.dhan_totp_secret and settings.dhan_client_id):
            return False
        import pyotp
        totp = pyotp.TOTP(settings.dhan_totp_secret.replace(" ", ""))
        # avoid using a code that is about to roll over
        if 30 - (time.time() % 30) < 5:
            time.sleep(6)
        try:
            r = requests.post(AUTH, params={
                "dhanClientId": settings.dhan_client_id,
                "pin": settings.dhan_pin,
                "totp": totp.now()}, timeout=20)
            d = r.json() if r.content else {}
            tok = d.get("accessToken")
            if r.status_code == 200 and tok:
                self.token = tok
                self.expiry = _parse_expiry(d.get("expiryTime")) or _jwt_expiry(tok)
                self._save()
                log.info("New Dhan token generated via TOTP, valid till %s", self.expiry)
                return True
            log.error("TOTP token generation failed: %s %s", r.status_code, str(d)[:200])
        except requests.RequestException as e:
            log.error("TOTP token generation error: %s", e)
        return False

    def ensure(self) -> str:
        """Return a working token or raise DhanAuthError."""
        now = datetime.now(IST)
        if self.token and self.is_valid():
            if self.expiry and self.expiry - now < timedelta(hours=3):
                # TOTP tokens can't be renewed -> regenerate; web tokens -> renew
                if not self._renew():
                    self._generate_with_totp()
            return self.token
        if self._generate_with_totp() and self.is_valid():
            return self.token
        # last resort: token pasted into .env (user may have refreshed it manually)
        if settings.dhan_access_token and settings.dhan_access_token != self.token:
            self.token = settings.dhan_access_token
            if self.is_valid():
                self._save()
                return self.token
        raise DhanAuthError(
            "Dhan access token missing/expired. Set DHAN_PIN + DHAN_TOTP_SECRET for automatic "
            "login, or paste a fresh token into DHAN_ACCESS_TOKEN in .env.")


class DhanClient:
    MIN_GAP = 3.1  # option chain rate limit: 1 unique request / 3s

    def __init__(self, tokens: TokenManager | None = None):
        self.tokens = tokens or TokenManager()
        self._last_call = 0.0
        self._expiry_cache: dict[int, tuple[str, list[str]]] = {}

    def _post(self, path: str, body: dict) -> dict:
        wait = self.MIN_GAP - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            token = self.tokens.ensure()
            self._last_call = time.time()
            r = requests.post(f"{API}{path}", json=body, timeout=20, headers={
                "access-token": token, "client-id": settings.dhan_client_id,
                "Content-Type": "application/json"})
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                self.tokens.token = ""  # force refresh next loop
            elif r.status_code == 429:
                time.sleep(4)
            log.warning("Dhan %s -> %s %s", path, r.status_code, r.text[:200])
            time.sleep(self.MIN_GAP)
        raise RuntimeError(f"Dhan API {path} failed after retries")

    def expiries(self, u: Underlying) -> list[str]:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        cached = self._expiry_cache.get(u.security_id)
        if cached and cached[0] == today:
            return cached[1]
        d = self._post("/optionchain/expirylist",
                       {"UnderlyingScrip": u.security_id, "UnderlyingSeg": u.segment})
        exps = sorted(d.get("data", []))
        self._expiry_cache[u.security_id] = (today, exps)
        return exps

    def nearest_expiry(self, u: Underlying) -> str:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        exps = [e for e in self.expiries(u) if e >= today]
        if not exps:
            raise RuntimeError(f"No active expiries for {u.name}")
        return exps[0]

    def option_chain(self, u: Underlying, expiry: str | None = None) -> dict:
        expiry = expiry or self.nearest_expiry(u)
        d = self._post("/optionchain", {
            "UnderlyingScrip": u.security_id, "UnderlyingSeg": u.segment, "Expiry": expiry})
        data = d.get("data", {})
        data["expiry"] = expiry
        data["underlying"] = u.name
        data["fetched_at"] = datetime.now(IST).isoformat(timespec="seconds")
        return data
