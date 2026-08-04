"""
superstate_nav.py — Query Superstate NAV per share from the off-chain API.

Two distinct series live here, and conflating them is the main hazard:

  real-time-price  the continuous NAV, defined at every second, accruing yield
                   between daily checkpoints. Matches the on-chain oracle exactly.
  nav-daily        the officially reported daily NAV/S — one calculated value per business
                   day, as of 17:00 ET.

Two API behaviours to guard against, both confirmed live:

  * A timestamp before the fund's price history returns "0.000000" with your own
    timestamp echoed back, so nothing marks it as missing. Treated as an error here.
  * nav-daily forward-fills indefinitely. Ask for a Saturday, a market holiday, or
    2026-12-31 and it stamps your requested date into `net_asset_value_date` and
    returns the last calculated value. That field therefore carries no provenance;
    resolve_daily_nav() establishes the real as-of date instead.

Requires: pip install requests tzdata
"""
from datetime import date, datetime, timezone
from decimal import Decimal

import requests

from nav_time import iso, checkpoint_utc, to_utc, utc_today

NAV_UNITS = Decimal(10 ** 6)  # the oracle stores NAV/share in 1e-6 units
_MATCH_TOLERANCE = Decimal("0.0000005")

BASE_URL = "https://api.superstate.com"
FUND_IDS = {"USTB": 1}

# Funds whose officially reported daily NAV the API serves, even where the continuous
# real-time-price endpoint does not respond. USCC (id 2) is daily-only: its
# real-time-price returns HTTP 400 at every timestamp tried.
DAILY_ONLY_FUND_IDS = {"USCC": 2}


class NavUnavailable(RuntimeError):
    """The requested NAV does not exist, as opposed to the request having failed."""


def _fund_id(fund, allow_daily_only=False):
    if isinstance(fund, int):
        return fund
    known = dict(FUND_IDS)
    if allow_daily_only:
        known.update(DAILY_ONLY_FUND_IDS)
    fund_id = known.get(fund.upper())
    if fund_id is None:
        raise ValueError(f"Unknown fund {fund!r}. Use one of {sorted(known)} or a numeric id.")
    return fund_id


def get_nav_per_share_at(when, fund="USTB"):
    """The continuous NAV/share for `fund` at a point in time.

    `when` may be a datetime (naive treated as UTC), a date (that day's 17:00 ET
    NAV/S checkpoint), or None for now.
    """
    fund_id = _fund_id(fund)
    requested_dt = None if when is None else to_utc(when)
    unix_ts = None if requested_dt is None else int(requested_dt.timestamp())

    response = requests.post(
        f"{BASE_URL}/v1/funds/{fund_id}/real-time-price",
        json={"unix_timestamp": unix_ts},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    price = Decimal(str(data["price"]))
    actual_dt = datetime.fromtimestamp(int(data["unix_timestamp"]), tz=timezone.utc)

    if price == 0:
        stamp = (requested_dt or actual_dt).strftime("%Y-%m-%d %H:%M")
        raise NavUnavailable(
            f"{fund} has no continuous price at {stamp} UTC. The API returned 0.000000, "
            "which means the timestamp predates the fund's price history — it is not a NAV of zero."
        )

    drift_seconds = 0.0 if requested_dt is None else (actual_dt - requested_dt).total_seconds()
    return {
        "fund": fund,
        "price": float(price),
        "price_decimal": price,
        "requested_utc": requested_dt.isoformat() if requested_dt else None,
        "actual_utc": actual_dt.isoformat(),
        "drift_seconds": drift_seconds,
        # A future timestamp is silently clamped to "now" rather than rejected.
        "clamped_from_future": requested_dt is not None and drift_seconds < -60,
        "snapped_to_nearest_available": requested_dt is not None and abs(drift_seconds) > 1,
    }


def get_daily_nav_rows(start, end, fund="USTB"):
    """Raw nav-daily rows for a date range, oldest first.

    Remember these are forward-filled; use resolve_daily_nav() for provenance.
    """
    fund_id = _fund_id(fund, allow_daily_only=True)
    response = requests.get(
        f"{BASE_URL}/v1/funds/{fund_id}/nav-daily",
        params={
            "start_date": start.isoformat() if isinstance(start, date) else start,
            "end_date": end.isoformat() if isinstance(end, date) else end,
        },
        timeout=15,
    )
    response.raise_for_status()
    return sorted(response.json(), key=lambda row: row["net_asset_value_date"][-4:] +
                  row["net_asset_value_date"][:2] + row["net_asset_value_date"][3:5])


def get_daily_close_nav(day, fund="USTB"):
    """The raw nav-daily row for one date, or None.

    The row's `net_asset_value_date` is whatever date you asked for, not the date the
    NAV/S was calculated. Prefer resolve_daily_nav().
    """
    rows = get_daily_nav_rows(day, day, fund)
    return rows[0] if rows else None


def resolve_daily_nav(day, fund="USTB"):
    """The officially reported daily NAV for `day`, with its true as-of date.

    Because the API forward-fills, this cross-checks the on-chain checkpoint series to
    establish whether `day` has its own NAV/S. A checkpoint exists if and only if the
    fund calculated a NAV/S that date, which distinguishes real checkpoints from weekends,
    market holidays, and business days whose checkpoint has not published yet — no holiday
    calendar required.

    Returns a dict whose `status` is one of:
      checkpoint   `day` has its own NAV/S checkpoint
      weekend      no checkpoint; value carried forward from an earlier date
      holiday      market holiday on a weekday; value carried forward
      pending      `day` is a business day whose checkpoint has not been published yet
      unavailable  the API has no row at all (before the fund's history)
    """
    if fund.upper() != "USTB":
        raise ValueError(
            "resolve_daily_nav cross-checks the USTB on-chain oracle, so it only "
            f"supports USTB; got {fund!r}. Use get_daily_close_nav for other funds."
        )

    import superstate_onchain_nav as onchain

    row = get_daily_close_nav(day, fund)
    if row is None:
        return {
            "requested_date": day,
            "as_of_date": None,
            "as_of_utc": None,
            "nav": None,
            "nav_text": None,
            "status": "unavailable",
            "detail": f"The API has no daily NAV row for {day:%Y-%m-%d}.",
            "checkpoint_index": None,
            "carried_forward": False,
        }

    nav_text = row["net_asset_value"]
    common = {
        "requested_date": day,
        "nav": float(nav_text),
        "nav_text": nav_text,
        "assets_under_management": row.get("assets_under_management"),
        "outstanding_shares": row.get("outstanding_shares"),
        "api_reported_date": row["net_asset_value_date"],
    }

    own_checkpoint = onchain.find_checkpoint_for_date(day)
    if own_checkpoint is not None:
        return {
            **common,
            "as_of_date": day,
            "as_of_utc": checkpoint_utc(day),
            "status": "checkpoint",
            "detail": f"NAV/S calculated for {day:%a %d %b %Y} at 17:00 ET; on-chain checkpoint {own_checkpoint['index']}.",
            "checkpoint_index": own_checkpoint["index"],
            "carried_forward": False,
            "matches_onchain": abs(Decimal(nav_text) - Decimal(own_checkpoint["navs"]) / NAV_UNITS) < _MATCH_TOLERANCE,
        }

    # No checkpoint for `day`. If the oracle has already moved past it, the date was a
    # non-business day; if not, the checkpoint simply has not been published yet.
    previous = onchain.checkpoint_on_or_before(day)
    latest = onchain.latest_checkpoint()
    day_checkpoint_ts = int(checkpoint_utc(day).timestamp())

    if latest["timestamp"] < day_checkpoint_ts:
        status = "pending"
        detail = (
            f"{day:%a %d %b %Y} has no published NAV/S checkpoint yet — the latest on-chain checkpoint "
            f"is {iso(latest['timestamp'])[:10]}. Showing that value."
        )
    elif day.weekday() >= 5:
        status = "weekend"
        detail = f"No NAV/S calculated on {day:%a %d %b %Y} (weekend). Showing the previous checkpoint."
    else:
        status = "holiday"
        detail = f"No NAV/S calculated on {day:%a %d %b %Y} (market holiday). Showing the previous checkpoint."

    as_of_date = None
    if previous is not None:
        as_of_date = datetime.fromtimestamp(previous["timestamp"], tz=timezone.utc).date()

    return {
        **common,
        "as_of_date": as_of_date,
        "as_of_utc": checkpoint_utc(as_of_date) if as_of_date else None,
        "status": status,
        "detail": detail,
        "checkpoint_index": previous["index"] if previous else None,
        "carried_forward": True,
        "matches_onchain": (
            previous is not None
            and abs(Decimal(nav_text) - Decimal(previous["navs"]) / NAV_UNITS) < _MATCH_TOLERANCE
        ),
    }


if __name__ == "__main__":
    import sys

    from nav_time import describe_offset_from_checkpoint, parse_cli_instant

    target_dt = parse_cli_instant(sys.argv[1]) if len(sys.argv) > 1 else checkpoint_utc(utc_today())
    fund = sys.argv[2] if len(sys.argv) > 2 else "USTB"

    realtime = get_nav_per_share_at(target_dt, fund=fund)
    print(f"{fund} continuous NAV/S at {target_dt.isoformat()}: {realtime['price']:.6f}")
    print(f"  ({describe_offset_from_checkpoint(target_dt, target_dt.date())})")
    if realtime["clamped_from_future"]:
        print(f"  WARNING: that timestamp is in the future; the API answered for "
              f"{realtime['actual_utc']} instead.")
    elif realtime["snapped_to_nearest_available"]:
        print(f"  NOTE: snapped to {realtime['actual_utc']}.")

    if fund.upper() == "USTB":
        daily = resolve_daily_nav(target_dt.date(), fund=fund)
        if daily["status"] == "unavailable":
            print(f"{fund} daily NAV: {daily['detail']}")
        else:
            print(f"{fund} official daily NAV/S: {daily['nav_text']} "
                  f"(as of {daily['as_of_date']:%Y-%m-%d} 17:00 ET)")
            print(f"  status={daily['status']}  {daily['detail']}")
            if daily["carried_forward"]:
                print(f"  CAUTION: this is NOT {target_dt.date():%Y-%m-%d}'s NAV/S.")
