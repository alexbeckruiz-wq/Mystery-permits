# Mt. Whitney Permit Watcher

Checks Recreation.gov every ~20 seconds for Mt. Whitney day-use and overnight
permit openings, and posts a Discord alert with a direct link to reserve
the moment one appears. Runs for free on GitHub Actions — nothing to leave
on at home.

**It does not book anything for you.** It only reads public availability
and pings you. See the note in `watcher.py` for why.

## Setup (about 10 minutes)

### 1. Create the Discord webhook
1. In Discord, go to the server/channel you want alerts in.
2. Channel Settings → Integrations → Webhooks → **New Webhook**.
3. Name it (e.g. "Whitney Watcher"), copy the **Webhook URL**.
4. Make sure notifications are turned on for that channel on your phone.

### 2. Create a GitHub repo
1. Create a new **public** repo. (Public means unlimited free GitHub Actions
   minutes, which is what lets this check every 20 seconds around the clock
   for $0 — see the caveats section below. Nothing sensitive lives in this
   code; your webhook URL is stored as an encrypted secret, not in the repo.)
2. Upload these files, keeping the folder structure:
   ```
   watcher.py
   .github/workflows/watch.yml
   ```

### 3. Add your webhook as a secret
Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `DISCORD_WEBHOOK_URL`
- Value: the webhook URL from step 1

### 4. Edit your trip details
Open `watcher.py` and edit the CONFIG section near the top:
- `WATCH_DATES` — the dates you're hoping for
- `PARTY_SIZE` — how many spots you need
- `WATCH_TYPES` — `["day", "overnight"]`, or just one

Commit and push.

### 5. Turn it on
- Go to the **Actions** tab of your repo. If prompted, click "I understand
  my workflows, enable them."
- You can trigger it immediately via **Actions → Whitney Permit Watcher →
  Run workflow**, or just wait — the schedule kicks in on its own.

### 6. Test it before trusting it
Run this locally once (needs Python 3, no extra packages required):
```
python watcher.py --debug
```
This prints the raw data Recreation.gov returns without sending any alerts,
so you can confirm dates/permit types are being read correctly. If it
errors out, Recreation.gov's API may have shifted slightly — paste the
error (and the `--debug` output) back to me and I'll help patch it.

## When you get an alert

Tap the link — it should drop you straight onto the reservation page for
that date and permit type. **Stay logged into Recreation.gov in your
phone's browser ahead of time** so you're not fumbling a password when a
rare cancellation shows up; that's the biggest time-saver available to you
here, more than shaving seconds off the check interval.

## A couple of honest caveats

- **This polls an undocumented Recreation.gov API.** It's the same one
  several long-running open-source tools use (camply, recdotgov, and a
  live public Whitney tracker), so it's well-trodden, but Recreation.gov
  could change it without notice. `--debug` is your first troubleshooting
  step.
- **Public repo = unlimited free minutes**, which is what makes a 20-second,
  round-the-clock check interval free. If you ever switch this repo to
  private, you'll only get 2,000 free Actions minutes/month — nowhere near
  enough for this workflow's ~44,000 minutes/month — so ask me to widen the
  poll interval (e.g. every 2–3 minutes) if you make that switch.
- **If alerts stop arriving**, check the Actions tab first. Repeated errors
  in the logs there most likely mean Recreation.gov's bot protection has
  started rate-limiting the checker — the fix is to raise
  `POLL_INTERVAL_SECONDS` back up (try 45–60), not to keep pushing it lower.
