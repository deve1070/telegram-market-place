# Deploying the marketplace bot to a real server

Result: your bot runs 24/7 on a server, behind HTTPS, restarts itself after crashes/reboots, backs up the database
every day, drops spam, and messages you when something breaks.

```
Telegram ──HTTPS──► Caddy (certificates, ports 80/443) ──► bot (webhook, :8080) ──► Postgres + Redis
                                                            └─ backup job → ./backups (daily)
```

---

## 0. What you need

| Thing                   | Notes                                                                                                                                                                                  |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A server (VPS)          | Ubuntu 22.04 or 24.04, 2 GB RAM, 1-2 vCPU, 20 GB disk. Roughly $5-7/month at Hetzner, DigitalOcean, Vultr, Contabo, etc. (check current prices)                                        |
| A domain or subdomain   | Telegram only sends webhooks to **HTTPS** addresses with a valid certificate. A `.com` is about $10/year (Namecheap, Cloudflare, Porkbun). A free subdomain (e.g. DuckDNS) also works. |
| A **separate test bot** | Create a second bot in @BotFather for your laptop. See the warning in step 8.                                                                                                          |
| Your laptop terminal    | Mac/Linux terminal, or Windows PowerShell / WSL, with `ssh` and `rsync` (on Windows without rsync use `scp -r`).                                                                       |

### Where should the server be? (important decision)

|                          | Server **outside** Ethiopia (e.g. Europe)                                                                                                                 | Server **in** Ethiopia                                                                                     |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Reaching Telegram        | Reliable                                                                                                                                                  | Your own log showed Telegram becoming unreachable for ~20 minutes: risky for a shop that must be always on |
| Telebirr receipt lookups | **Unknown**: one open-source project reports the Telebirr page may need an Ethiopian IP. Test with `/checkref` (step 11)                                  | Works                                                                                                      |
| Recommendation           | **Start here.** If Telebirr blocks the server's IP, use `VERIFIER_PROXY` (step 11); sellers fall back to manual confirmation meanwhile, so nothing breaks | Only if Telebirr cannot be proxied                                                                         |

---

## 0b. Try production mode on your own computer first (recommended)

You can run the real production setup (webhook mode, the Mini App and the seller dashboard) on your own computer by giving it a
public HTTPS address through a **tunnel**. Telegram needs HTTPS to open a Mini App, and a tunnel provides that without a server.

**1. Pick a tunnel** (check each provider's current terms, they change)
| | ngrok | cloudflared (Cloudflare Tunnel) |
|---|---|---|
| Account | free account | quick tunnels need no account; a stable named address needs a domain managed by Cloudflare |
| Address | the free plan includes one assigned dev domain, like `your-name.ngrok-free.app`, so it stays the same between runs | a quick tunnel prints a new random address every time you start it |
| Warning page | the free plan shows visitors a warning page once before your page loads (tap through; it is remembered for about a week) | none |
| Limits | free plan: about 20,000 requests a month and a monthly data allowance | no request limit published for quick tunnels at the time of writing |

For a first test either works. ngrok's stable address saves you from editing `.env` on every restart; cloudflared avoids the warning page.

**2. Start the tunnel** (it must point at port 8080 on your computer)

```bash
ngrok config add-authtoken <your token>     # once; the token is in your ngrok dashboard
ngrok http 8080                             # prints the https address (your dev domain)
# or:
cloudflared tunnel --url http://localhost:8080   # prints https://<random-words>.trycloudflare.com
```

**3. Tell the bot the address.** Add two lines to your local `.env` (use a test bot token, see the warning in step 8):

```
TUNNEL_URL=https://your-address-from-step-2
WEBHOOK_SECRET=<output of: openssl rand -hex 24>
```

**4. Start the bot in production mode:**

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d --build
docker compose logs --tail 20 bot          # look for: webhook registered for https://...
curl https://your-address-from-step-2/health   # -> {"ok": true}
```

**5. Try it in Telegram.** Open your bot: the main menu now has **🛍 Shop app**, and sellers get **📊 Dashboard** in their shop menu.
**6. Back to normal local mode** (polling, no Mini App): `docker compose up -d --build` without the second file.
If you use cloudflared, remember the address changes on every start: update `TUNNEL_URL` and run step 4 again.

**If the Mini App opens but shows nothing (or an error message)**, work through these in order:

1. **Open the address in a normal browser** on your computer: `https://your-address/app`. The page itself should load and say it must be opened from the Telegram bot. If you see ngrok's warning page, tap _Visit Site_. If you see an ngrok error code, the tunnel is not running or points at the wrong port (it must be `ngrok http 8080`).
2. **Watch the requests.** ngrok shows every request at `http://127.0.0.1:4040` while it runs. Open the Mini App in Telegram and look at what appears:
   - `GET /app` = 200 and then `GET /api/me` = **200**: the app is fine, reload it once.
   - `GET /api/me` = **401**: the login was rejected. The bot you opened is not the bot whose token is in `.env` (`BOT_TOKEN`). Open the right bot, or fix the token and run the `up -d --build` command again.
   - `GET /api/me` = **500** or **502**: look at `docker compose logs --tail 60 bot` for the error.
   - **No requests at all**: Telegram is not reaching the tunnel. Check that `TUNNEL_URL` in `.env` is exactly the address ngrok prints now (cloudflared addresses change on every start), then run the `up -d --build` command again and look for `webhook registered` in the logs.
3. **Update the files and fully close the Mini App** (swipe it away), then open it again; Telegram can keep an old copy.
4. The page now shows an error message with a **Try again** button instead of staying blank. Please copy that message when asking for help.

Good to know: your computer must stay on and online; replies depend on your home connection (your own earlier log showed Telegram
being unreachable for a while at times), so this is for testing only, not for real customers. When you are happy, continue with step 1 below to put it on a server.

---

## 1. Create the server

Create an Ubuntu server at your provider, add your **SSH public key** during creation (recommended over passwords),
and note its IP address (written as `SERVER_IP` below).

## 2. Point your domain at it

In your domain's DNS settings add an **A record**: name `bot` (so the address is `bot.yourdomain.com`), value `SERVER_IP`.
If you use Cloudflare, set the record to **DNS only** (grey cloud), not proxied. Wait a few minutes, then check from your laptop:

```bash
ping bot.yourdomain.com      # should show SERVER_IP
```

## 3. First login and one-time setup

```bash
scp deploy/setup-server.sh root@SERVER_IP:/root/
ssh root@SERVER_IP
bash setup-server.sh
```

It installs Docker, turns on the firewall (only SSH, 80, 443 open), brute-force protection, automatic security updates,
a 2 GB swap file, and creates a user called `deploy`. **Open a second terminal** and confirm `ssh deploy@SERVER_IP` works.
From now on use `deploy`, not root.

## 4. (Optional but recommended) disable password logins

Only after `ssh deploy@SERVER_IP` works with your key:

```bash
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

Keep your current session open and test a new login before closing it, so you can't lock yourself out.

## 5. Copy the project to the server

From your laptop, in the folder that contains `market-bot/`:

```bash
rsync -avz --exclude '.env' --exclude '__pycache__' --exclude 'backups' --exclude '.git' \
  market-bot/ deploy@SERVER_IP:~/market-bot/
```

## 6. Configure

```bash
ssh deploy@SERVER_IP
cd ~/market-bot
cp .env.production.example .env
chmod 600 .env
openssl rand -hex 24      # run twice: one value for POSTGRES_PASSWORD, one for WEBHOOK_SECRET
nano .env                 # fill in BOT_TOKEN, ADMIN_IDS, DOMAIN, POSTGRES_PASSWORD, WEBHOOK_SECRET, PLATFORM_ACCOUNTS
```

`BOT_TOKEN` must be the **production** bot's token. `DOMAIN` is just `bot.yourdomain.com` (no `https://`).

## 7. Start

```bash
echo "alias dc='docker compose -f docker-compose.prod.yml'" >> ~/.bashrc && source ~/.bashrc
dc up -d --build
dc ps                     # all services should be "running"; bot becomes "healthy" within ~1 minute
dc logs -f bot            # look for: webhook registered for https://...   (Ctrl+C to leave)
```

The first start takes a few minutes (building the image, getting the HTTPS certificate).

## 8. Verify

```bash
curl https://bot.yourdomain.com/health        # -> {"ok": true}
```

Then message your bot in Telegram. To see what Telegram thinks of the webhook, open this in a browser
(replace the token; don't share this link, it contains your token):
`https://api.telegram.org/bot<TOKEN>/getWebhookInfo` → `url` is set, `last_error_message` is absent.

> ⚠️ **Never run the local development bot with the production token.** Local mode (polling) _deletes_ the webhook.
> Keep a separate test bot token in your laptop's `.env` and leave `WEBHOOK_BASE_URL` empty there.

Starting fresh is cleanest. To carry over test shops from your laptop instead: on the laptop
`docker compose exec -T db pg_dump -U market -Fc market > laptop.dump`, copy the file to `~/market-bot/backups/`,
and run `bash deploy/restore.sh backups/laptop.dump` on the server.

## 8b. The Mini App (shop inside Telegram)

Once the bot runs behind HTTPS (steps 1-8) the Mini App is already live at `https://bot.yourdomain.com/app`. Buyers reach it from:

- the **🛍 Shop app** button in the bot's main menu,
- Telegram's own **menu button** next to the message box (the bot sets it automatically at startup),
- the **🛍 Open in the shop app** button on any shop page (opens straight on that shop).

Check it on your phone: open your bot, tap **🛍 Shop app**. If the buttons are missing, check that `DOMAIN` in `.env` is set and
`dc logs bot` shows `webhook registered`. The Mini App only exists in webhook mode (it is served by the same web server),
so the quickest way to try it is on the server, not on your laptop.

**Optional: a shareable direct link.** In @BotFather send `/newapp`, pick your bot, give it a title, short description and a
640x360 picture, set the Web App URL to `https://bot.yourdomain.com/app`, and choose a short name such as `shop`. You then get
`https://t.me/YOURBOT/shop`, and every seller can share their own storefront as
`https://t.me/YOURBOT/shop?startapp=shop_<their-shop-slug>` (put it in the Instagram or TikTok bio).

**Seller dashboard.** Shop owners see an extra **My shop** tab (and a **📊 Dashboard** button in the bot's shop menu): overview numbers,
orders with accept / pack / ship / cancel, confirming payments (with the buyer's screenshot), products with photos and stock,
delivery zones, payment accounts and cash on delivery. Everything runs through the same signed login, and a seller can only ever
see or change their own shop. Plan upgrades and identity verification stay in the chat because they involve payments and ID photos.

**Updating an existing deployment:** copy the new files, then `bash deploy/update.sh` and `dc restart caddy`
(the Caddyfile changed: it now also forwards `/app` and `/api`).

**Security in one paragraph:** every call from the app carries Telegram's signed login data, which the server verifies with the bot
token (forged or tampered data is rejected, and it expires after 24 hours). Prices, stock, delivery zones and payment methods are
always re-checked on the server, orders can only be read or changed by their buyer, and product photos are only served for visible
products of active shops. Payment references are still sent in the chat, where the existing verification flow runs.

---

## 9. Backups (read this one)

- A `backup` container dumps the database **every 24 hours** into `~/market-bot/backups/` and keeps 14 days
  (`BACKUP_KEEP_DAYS`). Check: `ls -lh ~/market-bot/backups` and `dc logs backup`.
- **Backups on the same server are not enough** (a dead server takes them along). Pull a copy to another machine
  daily. On your home computer (Linux/Mac), run `crontab -e` and add:
  ```
  0 4 * * * rsync -a deploy@SERVER_IP:~/market-bot/backups/ ~/market-backups/
  ```
  Or use `rclone` to copy the folder to Google Drive / Backblaze.
- Backups contain customers' phone numbers and addresses. Keep them private and don't put them in public storage.
- **Practice a restore once** before you need it:
  `bash deploy/restore.sh backups/market-YYYYMMDD-HHMMSS.dump` (it takes a safety copy first, then asks you to type YES).

## 10. Updating the bot later

After copying new files to the server (step 5's `rsync`), on the server:

```bash
cd ~/market-bot && bash deploy/update.sh
```

It backs up, rebuilds, restarts, and waits until the bot reports healthy. Database changes are applied automatically
on start (additive only; a backup is taken first).

## 11. Telebirr from a foreign server

After deploying, make a real small Telebirr payment and send `/checkref telebirr <reference>` to the bot.

- **PARSED OK** → nothing to do.
- **FETCH FAILED** (timeout / 403) → the portal may be blocking foreign IPs. Option: put a small proxy (squid/tinyproxy
  with a username and password, restricted to your server's IP) on a machine with an Ethiopian IP and set
  `VERIFIER_PROXY=http://user:pass@that-machine:3128` in `.env`, then `dc up -d`. Until then buyers' proofs fall back to
  seller confirmation automatically.

## 12. Monitoring

- **Uptime alerts (free):** create a monitor at uptimerobot.com for `https://bot.yourdomain.com/health` with email/Telegram alerts.
- **Error alerts:** unhandled bot errors are sent to every admin in Telegram (max one per minute).
- **Logs:** `dc logs --tail 100 bot`, `dc logs -f bot`. Logs rotate automatically (30 MB max per service).
- **Disk space:** `df -h` now and then; backups are small but images and logs add up.

## 13. Security and privacy checklist

- [ ] `.env` exists only on the server (`chmod 600`), never in git, never sent in chat.
- [ ] If the bot token ever leaks: @BotFather → `/revoke`, put the new token in `.env`, `dc up -d`.
- [ ] Firewall on (`sudo ufw status` shows only OpenSSH, 80, 443). Postgres and Redis are not reachable from outside.
- [ ] SSH keys only (step 4). Automatic security updates are on; reboot monthly: `sudo reboot`.
- [ ] You store phone numbers and delivery addresses. Add a short privacy notice for users, and check Ethiopia's current
      data-protection rules before launch.
- [ ] Off-site backups are working (step 9).

## 14. Troubleshooting

| Symptom                                    | Check                                                                                                                          |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `curl https://domain/health` fails         | DNS points to the server? (`ping`) · ports 80/443 open? · `dc logs caddy` (certificate errors)                                 |
| Certificate not issued                     | DNS record must be correct and not proxied; wait a few minutes; `dc restart caddy`                                             |
| Mini App opens but shows nothing           | See the checklist at the end of section 0b (the ngrok request viewer at `127.0.0.1:4040` shows what the app asked for)         |
| Bot silent in Telegram                     | `dc ps` (bot healthy?) · `dc logs --tail 80 bot` · getWebhookInfo `last_error_message`                                         |
| `WEBHOOK_SECRET must be…` on start         | 16+ chars of letters, digits, `_`, `-` only (use `openssl rand -hex 24`)                                                       |
| Bot stops answering after you test locally | The local bot used the production token and deleted the webhook. Use a separate test bot; restart production: `dc restart bot` |
| `bot` unhealthy                            | Postgres/Redis down? `dc ps`, `dc logs db`                                                                                     |
| Disk full                                  | `docker system prune -f` (safe: removes unused images) and old files in `backups/`                                             |

## 15. Cheat sheet (after the alias in step 7)

```bash
dc ps                      # status
dc logs -f bot             # live logs
dc restart bot             # restart the bot
dc up -d --build           # apply changes
bash deploy/update.sh      # safe update with backup
bash deploy/restore.sh backups/FILE.dump
dc exec db psql -U market -d market -c "select id, name, status, plan_key from shops;"
```
