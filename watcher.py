#!/usr/bin/env python3
"""
Mt. Whitney permit availability watcher.

Polls Recreation.gov's permit availability data for the Mt. Whitney Zone
permit product and posts a Discord alert the moment a slot opens up for a
date / permit-type you're watching, with a direct link to reserve it.

WHAT THIS DOES:  checks public availability, sends you a Discord message.
WHAT THIS DOES NOT DO:  log into your Recreation.gov account, hold a permit,
or complete a checkout for you. See the README for why.

--------------------------------------------------------------------------
A NOTE ON THE API

Recreation.gov's JSON endpoints used below are not officially documented.
The exact request shape here (one calendar month per request, and the
quota_usage_by_member_daily.remaining field) is verified against
github.com/mdeckebach/recdotgov, a tool built specifically to snapshot
Recreation.gov permit availability. It's been stable for years, but could
change without notice. If alerts stop coming in:

    python watcher.py --debug

This dumps the raw JSON Recreation.gov returns so you (or I, if you paste
it back to me) can see if a field name changed and fix the one function
that needs it (extract_remaining, below).
--------------------------------------------------------------------------
"""

import os
import sys
import json
import time
import logging
import argparse
import calendar
from datetime import date
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ----------------------------------------------------------------------------
# CONFIG — edit this section for your trip, then commit & push
# ----------------------------------------------------------------------------

PERMIT_ID = "233260"  # Mt. Whitney Zone permit product on recreation.gov

# Dates you want alerts for. Format: "YYYY-MM-DD". Add as many as you like —
# the more dates you watch, the more chances you have at a cancellation.
WATCH_DATES = [
    "2026-08-28",
    "2026-08-29",
    "2026-08-30",
    "2026-09-05",
    "2026-09-06",
    "2026-09-12",
    "2026-09-19",
    "2026-09-26",
    "2026-10-03",
    "2026-10-10",
    "2026-10-24",
]

# How many spots you need together. An "opening" only counts as an alert if
# at least this many spots are free. Set to 1 if any opening at all is useful.
PARTY_SIZE = 3

# Which permit types to watch.
WATCH_TYPES = ["day", "overnight"]  # any of: "day", "overnight"

# How often to poll, in seconds. Note: each cycle makes one request per
# distinct calendar month covered by WATCH_DATES (Recreation.gov only
# accepts single-month ranges) — watching 3 months means 3 requests/cycle,
# not 1. 20s is a reasonable floor for a small handful of months; if you
# watch many months at once and start seeing repeated fetch errors in the
# Actions logs, back this off (try 30–45) rather than push it lower.
POLL_INTERVAL_SECONDS = 20

# Re-remind if a slot is still open after this many minutes (so one alert
# doesn't get buried in your Discord history and you forget to book).
REMINDER_INTERVAL_MINUTES = 30

# Discord webhook URL — set via the DISCORD_WEBHOOK_URL environment variable
# / GitHub secret. Don't hardcode it here.
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# When running under GitHub Actions, the workflow hands off to a fresh job
# every ~5h45m (see .github/workflows/watch.yml). This makes the script exit
# cleanly a little before that job's timeout so the handoff is graceful.
# Ignore this if you're just running the script yourself on your own machine.
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", 60 * 60 * 24 * 365))

# ----------------------------------------------------------------------------

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; whitney-permit-watcher/1.0; personal, non-commercial use)",
    "Accept": "application/json",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("whitney-watcher")


def http_get_json(url, timeout=15):
    req = Request(url, headers=BASE_HEADERS)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_divisions(permit_id):
    """
    Returns {division_id: division_name}, e.g.
    {"1234": "Mt. Whitney Trail - Overnight", "1235": "Mt. Whitney Zone - Day Use"}
    Pulled from the permit product's content/metadata endpoint.
    """
    url = f"https://www.recreation.gov/api/permitcontent/{permit_id}"
    data = http_get_json(url)
    divisions = {}
    for div in data.get("payload", {}).get("divisions", {}).values():
        div_id = str(div.get("id") or div.get("division_id") or "")
        if div_id:
            divisions[div_id] = div.get("name", "")
    return divisions


def pick_division_ids(divisions, watch_types):
    """Match division names to 'day' / 'overnight' by keyword in the name."""
    picked = {}
    for div_id, name in divisions.items():
        lname = name.lower()
        if "whitney" not in lname:
            continue
        if "overnight" in watch_types and "overnight" in lname:
            picked[div_id] = ("overnight", name)
        elif "day" in watch_types and "day" in lname:
            picked[div_id] = ("day", name)
    return picked


def month_bounds(year, month):
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    return start.isoformat(), end.isoformat()


def fetch_availability_month(permit_id, year, month):
    """One calendar month of availability. Recreation.gov 400s if you ask
    for a range spanning more than one month, so callers must chunk by month."""
    start_date, end_date = month_bounds(year, month)
    url = (
        f"https://www.recreation.gov/api/permitinyo/{permit_id}/availabilityv2"
        f"?start_date={start_date}&end_date={end_date}&commercial_acct=false"
    )
    return http_get_json(url)


def fetch_availability(permit_id, watch_dates):
    """
    Fetches every calendar month touched by watch_dates and merges them into
    one dict keyed by date string ("YYYY-MM-DD") -> {entry_id: entry_data}.
    """
    months_needed = sorted({(int(d[:4]), int(d[5:7])) for d in watch_dates})
    merged = {}
    for year, month in months_needed:
        data = fetch_availability_month(permit_id, year, month)
        merged.update(data.get("payload", {}))
    return merged


def extract_remaining(availability_by_date, division_id, target_date):
    """
    Pull the 'remaining spots' number for one division on one date. This is
    the one function to check first if Recreation.gov changes their API
    shape — run with --debug to see the raw JSON and adjust the key names
    below to match.
    """
    day = availability_by_date.get(target_date, {})
    entry = day.get(division_id) or day.get(str(division_id))
    if not entry:
        return None
    return entry.get("quota_usage_by_member_daily", {}).get("remaining")


def build_reservation_link(permit_id, permit_type, target_date):
    type_param = "overnight-permit" if permit_type == "overnight" else "day-permit"
    return (
        f"https://www.recreation.gov/permits/{permit_id}/registration/"
        f"detailed-availability?type={type_param}&date={target_date}"
    )


def send_discord_alert(target_date, permit_type, remaining, link):
    label = "Overnight — Mt. Whitney Trail" if permit_type == "overnight" else "Day Use — Mt. Whitney Zone"
    content = (
        f"**🏔️ Whitney permit opening — {label}**\n"
        f"**Date:** {target_date}\n"
        f"**Spots open:** {remaining}\n"
        f"{link}"
    )
    if not WEBHOOK_URL:
        log.warning("DISCORD_WEBHOOK_URL not set — would have sent:\n%s", content)
        return
    body = json.dumps({"content": content}).encode("utf-8")
    req = Request(WEBHOOK_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        urlopen(req, timeout=10)
        log.info("Discord alert sent: %s / %s / %s left", target_date, permit_type, remaining)
    except (URLError, HTTPError) as e:
        log.error("Failed to send Discord alert: %s", e)


def check_once(divisions_by_type, already_alerted):
    if not WATCH_DATES:
        return
    try:
        availability_by_date = fetch_availability(PERMIT_ID, WATCH_DATES)
    except Exception as e:
        log.error("Availability fetch failed (will retry next cycle): %s", e)
        return

    reminder_seconds = REMINDER_INTERVAL_MINUTES * 60
    for target_date in WATCH_DATES:
        for div_id, (ptype, _name) in divisions_by_type.items():
            remaining = extract_remaining(availability_by_date, div_id, target_date)
            key = (target_date, div_id)
            if remaining is not None and remaining >= PARTY_SIZE:
                first_seen = already_alerted.get(key)
                if first_seen is None or time.time() - first_seen > reminder_seconds:
                    link = build_reservation_link(PERMIT_ID, ptype, target_date)
                    send_discord_alert(target_date, ptype, remaining, link)
                already_alerted[key] = already_alerted.get(key, time.time())
            else:
                already_alerted.pop(key, None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="dump raw availability JSON and exit")
    args = parser.parse_args()

    log.info("Looking up permit divisions for permit %s...", PERMIT_ID)
    try:
        divisions = fetch_divisions(PERMIT_ID)
    except Exception as e:
        log.error("Could not fetch divisions (%s). Recreation.gov's API may have changed — try --debug.", e)
        sys.exit(1)

    divisions_by_type = pick_division_ids(divisions, WATCH_TYPES)
    if not divisions_by_type:
        log.error("Couldn't match any divisions to %s. All divisions found on this permit:", WATCH_TYPES)
        for div_id, name in divisions.items():
            log.error("  %s: %s", div_id, name)
        sys.exit(1)

    log.info("Watching divisions: %s", {k: v[1] for k, v in divisions_by_type.items()})
    log.info("Watching dates: %s (need >= %d spot(s))", WATCH_DATES, PARTY_SIZE)

    if args.debug:
        availability_by_date = fetch_availability(PERMIT_ID, WATCH_DATES)
        print(json.dumps(availability_by_date, indent=2)[:6000])
        return

    already_alerted = {}
    start_time = time.time()
    while True:
        check_once(divisions_by_type, already_alerted)
        if time.time() - start_time > MAX_RUNTIME_SECONDS:
            log.info("Max runtime reached — exiting cleanly so the next scheduled run can pick up.")
            break
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
