# Run it in the cloud, free, with no local machine

## Which free option fits

| Option | Cost | Works for this scanner? | Why |
|---|---|---|---|
| **GitHub Actions + cron-job.org trigger** | Free (public repo) | ✅ **Recommended** | Runs the Python code unchanged as two sessions a day. An external trigger fires at the exact minute. No server and no credit card. |
| **Oracle Cloud Always Free VM (Mumbai)** | Free (card needed for verification) | ✅ Best reliability | A real 24×7 Linux box with an Indian IP, so NSE data works best. Uses `deploy/index-scanner.service` as-is. |
| Google Cloud e2-micro | Free (card needed) | ✅ | Always free, but US regions only, so NSE may block it. |
| Claude scheduled task | Included in plan | ❌ | The task's sandbox can't reach the Dhan, NSE or Telegram APIs. It also can't run more often than about hourly, and the OI monitor needs every 3 min. |
| Cloudflare Workers only | Free | ❌ for the scanner itself | The free plan allows 10 ms of CPU per run, which is too little to parse a full option chain. It would also need a JavaScript rewrite. It is a good free **trigger**, though (see `deploy/cloudflare-dispatcher.js`). |
| PythonAnywhere / Render / Railway free tiers | — | ❌ | Outbound sites are restricted, apps sleep, or the free tier has been removed. |

---

## Option 1: GitHub Actions (about 15 min, no card)

### How it runs

- Each trading day the workflow `.github/workflows/scanner.yml` runs `python -m scanner run` in two sessions, because one GitHub job can last at most 6 hours.

  | Session | IST | Covers |
  |---|---|---|
  | A | 08:00 → 13:50 | Dhan TOTP login, 08:30 brief, 09:25 opening check, OI alerts every 3 min |
  | B | 13:55 → 15:50 | OI alerts until close, 15:40 EOD summary |

- Session A's intraday OI history is passed to session B through the Actions cache. The Dhan token is deleted before saving, so no secrets are stored there.
- On weekends and holidays the job exits within a minute.
- **Use a public repository.** Standard GitHub runners are free and unlimited for public repos. A private repo gets 2,000 minutes a month, and this needs about 8,000.
  - Your secrets stay encrypted and are never shown in logs.
  - Only the code and the run logs (alert text, no credentials) are public.

### Steps

1. **Enable Dhan TOTP.** This is required for cloud runs.
   - Go to web.dhan.co, then DhanHQ Trading APIs, then Setup TOTP.
   - Copy the **secret key text**.
   - Without TOTP the cloud job can't log in by itself each day.
2. **Create the repo.** On github.com/new, create a **public** repo called `index-options-scanner`. Upload everything in this folder, including the `.github` folder. Do **not** upload a `.env` file.
3. **Add secrets.** Go to Repo, then Settings, then Secrets and variables, then Actions, then **New repository secret**. Add:
   - `DHAN_CLIENT_ID`
   - `DHAN_PIN`
   - `DHAN_TOTP_SECRET`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - Optional: `ANTHROPIC_API_KEY`, `GIFT_NIFTY_URL`
4. **Add variables (optional).** On the **Variables** tab, add:
   - `EXTRA_HOLIDAYS`: NSE holidays as `2026-10-20,2026-11-10,…`. This is a backstop in case NSE's holiday API blocks GitHub's servers.
   - `MONITOR_INTERVAL_MIN`: defaults to `3`.
5. **Test.**
   - Go to the Actions tab, select index-options-scanner, then **Run workflow**, and set command to `test-telegram`. You should get a message on Telegram.
   - Run it again with `token`. The log should show "Dhan token OK".
   - Run it again with `premarket` to send a brief right away.
6. **Set up a precise trigger.** GitHub's own `schedule` can start 5–30 minutes late and is paused in repos with no activity for 60 days, so use an external trigger.
   1. Create a fine-grained token at github.com/settings/personal-access-tokens:
      - Repository access: only this repo.
      - Permissions: Actions set to **Read and write**.
   2. Create a free account at **cron-job.org** and create two jobs.

      | Setting | Value |
      |---|---|
      | URL | `https://api.github.com/repos/<you>/index-options-scanner/actions/workflows/scanner.yml/dispatches` |
      | Schedule | 08:00 and 13:55, Mon–Fri, timezone Asia/Kolkata |
      | Method | **POST** (under Advanced) |
      | Headers | `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28` |
      | Body | `{"ref":"main","inputs":{"command":"auto"}}` |

   3. A successful call returns **204**.
   4. Prefer Cloudflare? `deploy/cloudflare-dispatcher.js` does the same job as a free Worker with cron triggers.

Done. The scanner now runs every trading day with your computer off.

### Limits to know

- **NSE data:** GitHub runners are US servers, and NSE sometimes blocks them. If that happens, FII/DII cash and participant OI show as "skipped" and the bias is computed from the other factors. The Dhan option chain, global cues, GIFT and news are not affected. If NSE data matters a lot to you, use Option 2.
- **Start-up time:** a session takes about 30–60 seconds to boot, so the first OI snapshot arrives around 09:17.
- **Duplicate triggers:** if both the external trigger and the backup schedule fire, the second run waits and then exits immediately.

---

## Option 2: Oracle Cloud Always Free VM in Mumbai

1. Sign up at cloud.oracle.com and choose home region **India West (Mumbai)**. A card is needed for verification only; you are not charged within Always Free limits.
2. Create an instance with these settings:
   - Shape: `VM.Standard.E2.1.Micro` (Always Free), or Ampere A1 with 1 OCPU and 6 GB.
   - Image: Ubuntu 22.04.
   - Download the SSH key.
3. SSH in, then run:
   ```bash
   sudo apt update && sudo apt install -y python3-venv git
   git clone https://github.com/<you>/index-options-scanner && cd index-options-scanner
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   cp .env.example .env && nano .env && chmod 600 .env
   .venv/bin/python -m scanner test-telegram
   sudo cp deploy/index-scanner.service /etc/systemd/system/   # edit User/paths if not 'ubuntu'
   sudo systemctl daemon-reload && sudo systemctl enable --now index-scanner
   ```
4. **Avoid reclamation.** Oracle reclaims *idle* Always Free VMs, and this scanner uses very little CPU. To prevent it, upgrade the account to **Pay As You Go**. Always Free resources still cost ₹0, and the idle-reclaim rule no longer applies.

You can also use GitHub Actions now and move to Oracle later. Both run the same code.
