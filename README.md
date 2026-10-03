# Nifty / Sensex Index-Options Scanner

This scanner sends a pre-market directional brief to your Telegram channel at 08:30 IST. During market hours it watches the Nifty and Sensex weekly option chains and sends an alert whenever open-interest (OI) positioning changes meaningfully.

## What you get on Telegram

| Time (IST) | Message |
|---|---|
| 08:10 | Silent Dhan token refresh. You only get a message if the login fails. |
| **08:30** | **Pre-market scan.** Shows the day bias (Strong Bullish to Strong Bearish) with a score from −100 to +100, a confidence level, a regime call, GIFT Nifty gap, global cues, FII/DII cash, FII index-futures long %, a full option-chain read for Nifty and Sensex, news and event risk, and the levels that decide the day. |
| 09:25 | **Opening check.** Compares the actual open with the 08:30 call. |
| 09:17–15:30 | **Live OI alerts every 3 min.** See the alert table below. |
| 10:00–15:00 | Hourly digest showing the change since the open. |
| 15:40 | **EOD summary.** Day's OI shift plus walls to carry into tomorrow. |

Weekends and NSE trading holidays are skipped automatically.

### How the day bias is built

Seven factors each produce a score from −1 to +1. They are combined by weight. If a factor's data is missing, it is dropped and the remaining weights are rescaled.

| Factor | Weight | Logic |
|---|---|---|
| GIFT Nifty gap | 22 | Gap vs Nifty close. ±0.6% maps to the full score. |
| Option chain | 22 | Near-ATM PCR, fresh put vs call writing, the writer behaviour behind each OI change, position between walls, and max-pain pull near expiry. |
| FII derivatives | 16 | FII index-futures long % (from NSE participant OI), day-on-day change, and change in FII index options. |
| Global | 12 | S&P and Nasdaq futures 65%, Nikkei/Hang Seng/Kospi 35%. |
| FII/DII cash | 10 | FII net plus the DII cushion. |
| News | 10 | Finance lexicon over ET, Moneycontrol, Mint, Business Standard and CNBC-TV18 RSS, weighted by recency. High-impact events (RBI, Fed, CPI, budget, war, tariff) are flagged. |
| Macro | 8 | Brent, USD/INR, DXY and US 10Y. A rise in any of them counts as risk-off. |

- **Confidence** is the weighted share of factors that agree with the final direction, multiplied by data coverage.
- **Regime** uses net gamma exposure (GEX) and India VIX:
  - Positive GEX with a weak score means a range day is likely, so sell into the walls.
  - Negative GEX or a VIX spike means expansion is likely, so respect breakouts.

### Intraday OI alerts

| Alert | Trigger |
|---|---|
| PCR shift | Near-ATM PCR moves at least 0.10 from the last alert. |
| Wall shift | The highest-OI call strike (resistance) or put strike (support) moves. |
| Breakout / breakdown | Spot crosses the call wall or the put wall. |
| Strike OI surge | A strike's OI changes by at least 25% and at least 10 lakh units within 15 min. Each surge is labelled from OI and premium direction: call writing, call short-covering, put writing, put short-covering, buying, or unwinding. |
| IV spike / crush | ATM IV moves at least 1.5 vol points within 15 min. |
| Max pain moved | Max pain moves 2 strikes or more. |
| OI bias flip | The option-chain score changes sign with conviction. |

Every threshold can be changed in `.env`. A 15-minute cooldown stops the same alert from repeating.

## Setup (about 20 minutes)

### 1. Telegram

1. In Telegram, open **@BotFather**, send `/newbot`, and copy the **bot token**.
2. Add the bot to your channel as an **Administrator** with permission to post messages.
3. Set the chat ID:
   - Public channel: `@yourchannelname`.
   - Private channel: forward any channel post to **@userinfobot** and use the `-100…` id it gives you.

### 2. Dhan (automatic daily login)

The Dhan access token expires every 24 hours. To avoid pasting a new one daily, enable **TOTP**:

1. Go to web.dhan.co, then **DhanHQ Trading APIs**, then **Setup TOTP**.
2. Scan the QR code with Google Authenticator. Also copy the **secret key text** shown with the QR code.
3. Put `DHAN_CLIENT_ID`, `DHAN_PIN` and `DHAN_TOTP_SECRET` in `.env`.

Each morning the scanner then calls Dhan's `generateAccessToken` with a fresh TOTP code. No browser is needed.

**Without TOTP:** paste a web-generated token into `DHAN_ACCESS_TOKEN`. The scanner extends it every day with Dhan's `RenewToken`. If the token ever dies (for example, the PC was off for more than 24 hours), you get a "Dhan login needed" alert.

**Other requirements:**
- The option chain needs an active **Dhan Data API** subscription. `python -m scanner token` checks it.
- The scanner only *reads* data. It never places orders, so Dhan's static-IP rule for order APIs does not apply.

> 🔒 **Security.** Your PIN and TOTP secret together can log into your Dhan account.
> - Keep `.env` only on a machine you control and run `chmod 600 .env`.
> - Never commit `.env` to git or share it.

### 3. Install

```bash
cd index-options-scanner
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env
```

### 4. Test

```bash
python -m scanner test-telegram          # "Scanner connected" should appear in your channel
python -m scanner token                  # validates or refreshes the Dhan token
python -m scanner premarket --dry-run    # prints the full brief in the terminal
python -m scanner monitor                # one OI snapshot (posts "OI monitor started")
python tests/test_offline.py             # offline logic tests
```

### 5. Run it every day

> ☁️ **No local machine?** See **[CLOUD_SETUP.md](CLOUD_SETUP.md)**. It covers a free GitHub Actions setup with a precise cron-job.org or Cloudflare trigger, and an Oracle Always Free VM in Mumbai.

To run it yourself instead, the machine must be on from 08:10 to 15:40 IST on trading days.

- **Best: a small cloud VPS in the Mumbai region**, for example AWS Lightsail, DigitalOcean BLR or Oracle Free Tier.
  1. Edit `deploy/index-scanner.service` with your username and path.
  2. Run:
     ```bash
     sudo cp deploy/index-scanner.service /etc/systemd/system/
     sudo systemctl daemon-reload && sudo systemctl enable --now index-scanner
     journalctl -u index-scanner -f      # live logs
     ```
  3. The service restarts on crash and on reboot.
- **Windows PC:**
  1. Open Task Scheduler and create a task that runs `deploy\start_windows.bat` at log on.
  2. Disable sleep during market hours.
- **Mac or Linux desktop:** run `python -m scanner run` inside `tmux` or `screen`, or as a launchd/systemd user service.

The scheduler catches up within 45 minutes. For example, if the machine wakes at 08:50, the 08:30 brief still goes out.

## Optional upgrades

- **Claude-written desk note:** set `ANTHROPIC_API_KEY`. The 08:30 brief will then start with a 4–6 line note written from all the numbers above.
- **GIFT Nifty source:** GIFT Nifty (NSE IX) has no free official API.
  - The scanner tries a public quote feed first.
  - If that fails, it shows an *implied* gap estimated from US futures and Asia, labelled "implied est." in the message.
  - If you have a reliable JSON quote URL (for example from another broker API), put it in `GIFT_NIFTY_URL`. Any JSON containing a `lastPrice`, `ltp` or `price` field works.
- **More underlyings:** add `Underlying("BANKNIFTY", 25, "IDX_I", 100, "^NSEBANK")` to `config.py`. Each extra index adds 3 seconds per cycle because of Dhan's 1-request-per-3-second limit on the option chain.

## Files

```
scanner/
  config.py       settings from .env
  dhan.py         token manager (TOTP / renew / fallback) + option chain + expiries
  nse.py          FII/DII cash, participant-wise OI, holiday calendar
  global_cues.py  US futures, Asia, crude, USDINR, DXY, US10Y, VIX, GIFT Nifty
  news.py         RSS headlines, sentiment lexicon, event flags, optional Claude note
  analytics.py    PCR, max pain, walls, IV skew, straddle, 1σ range, GEX, writer behaviour
  bias.py         weighted composite score → label, confidence, regime
  intraday.py     OI change detector with cooldowns + hourly digest
  report.py       Telegram message layouts
  jobs.py         premarket / opening / monitor / EOD jobs
  __main__.py     CLI + IST scheduler
data/             token cache, daily snapshots, logs (created automatically)
```

## Known limits

- **NSE scraping:** NSE endpoints (FII/DII, participant OI, holidays) are public but sometimes rate-limit scripts. If one fails, the brief still goes out without that factor and shows it as "skipped".
- **FII/DII timing:** NSE publishes FII/DII figures around 6–7 PM and participant OI in the evening. The 08:30 brief therefore uses the **previous session's** flows, which is standard practice.
- **Pre-open option chain:** at 08:30 the option chain shows the previous session's closing OI. The live picture starts at 09:15, which is why the 09:25 opening check exists.
- **Not advice:** this is a decision-support tool, not investment advice. Size positions with your own risk rules.
