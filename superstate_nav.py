"""
superstate_nav.py — Query Superstate USTB/USCC NAV per share at a specific timestamp.
Requires: pip install requests
"""
import requests
from datetime import datetime, timezone, date, time

BASE_URL = "https://api.superstate.com"
FUND_IDS = {"USTB": 1, "USCC": 2}


def _fund_id(fund):
    if isinstance(fund, int):
        return fund
    fid = FUND_IDS.get(fund.upper())
    if fid is None:
        raise ValueError(f"Unknown fund '{fund}'. Use one of {list(FUND_IDS)} or a numeric id.")
    return fid


def get_nav_per_share_at(when, fund="USTB"):
    """
    Return the continuous NAV/share for `fund` at a specific point in time.

    `when` can be:
      - a datetime (naive datetimes are assumed UTC)
      - a date (interpreted as 23:59:00 UTC on that date)
      - None (defaults to "now")
    """
    fid = _fund_id(fund)

    if when is None:
        unix_ts, requested_dt = None, None
    else:
        if isinstance(when, datetime):
            dt = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        elif isinstance(when, date):
            dt = datetime.combine(when, time(23, 59, 0), tzinfo=timezone.utc)
        else:
            raise TypeError("when must be a datetime, date, or None")
        requested_dt = dt.astimezone(timezone.utc)
        unix_ts = int(requested_dt.timestamp())

    resp = requests.post(
        f"{BASE_URL}/v1/funds/{fid}/real-time-price",
        json={"unix_timestamp": unix_ts},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    actual_dt = datetime.fromtimestamp(data["unix_timestamp"], tz=timezone.utc)
    snapped = requested_dt is not None and abs((actual_dt - requested_dt).total_seconds()) > 1

    return {
        "fund": fund,
        "price": float(data["price"]),
        "requested_utc": requested_dt.isoformat() if requested_dt else None,
        "actual_utc": actual_dt.isoformat(),
        "snapped_to_nearest_available": snapped,
    }


def get_daily_close_nav(day, fund="USTB"):
    """Superstate's officially reported daily NAV/S for a calendar date."""
    fid = _fund_id(fund)
    ds = day.isoformat() if isinstance(day, date) else day
    resp = requests.get(
        f"{BASE_URL}/v1/funds/{fid}/nav-daily",
        params={"start_date": ds, "end_date": ds},
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


if __name__ == "__main__":
    import sys
    from datetime import datetime as _dt

    if len(sys.argv) > 1:
        raw = sys.argv[1]
        # Accepts "2026-08-01" (defaults to 23:59:00 UTC) or "2026-08-01T14:32:07"
        if "T" in raw or " " in raw:
            raw = raw.replace(" ", "T")
            target_dt = _dt.fromisoformat(raw).replace(tzinfo=timezone.utc)
        else:
            target_dt = datetime.combine(date.fromisoformat(raw), time(23, 59, 0), tzinfo=timezone.utc)
    else:
        target_dt = None

    fund = sys.argv[2] if len(sys.argv) > 2 else "USTB"

    rt = get_nav_per_share_at(target_dt, fund=fund)
    daily = get_daily_close_nav(target_dt.date() if target_dt else date.today(), fund=fund)

    label = target_dt.isoformat() if target_dt else "now"
    print(f"{fund} continuous NAV/S as of {label} UTC: {rt['price']}")
    print(f"  (actual oracle timestamp used: {rt['actual_utc']}, snapped={rt['snapped_to_nearest_available']})")
    if daily:
        print(f"{fund} officially reported Daily NAV/S for {target_dt.date() if target_dt else date.today()}: {daily['net_asset_value']}")