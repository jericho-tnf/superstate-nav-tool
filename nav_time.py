"""
nav_time.py — Time handling shared by the off-chain and on-chain NAV lookups.

Everything in this project is UTC on the wire. The one thing that is *not* UTC is the
fund's daily NAV/S checkpoint: Superstate calculates it at 17:00 America/New_York,
which lands on 21:00 UTC under EDT and 22:00 UTC under EST. Verified against all 412
on-chain checkpoints — 265 at 21:00 UTC, 147 at 22:00 UTC, no other value.

Terminology follows docs.superstate.com: the daily valuation event is a "NAV/S
checkpoint", and `checkpoint_utc(day)` returns the same instant that appears on-chain
as `checkpoints(N).timestamp`.
"""
from datetime import datetime, timezone, date, time

try:
    from zoneinfo import ZoneInfo

    NEW_YORK = ZoneInfo("America/New_York")
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "Could not load the 'America/New_York' timezone. Windows ships no tz "
        "database, so zoneinfo needs the tzdata package: pip install tzdata"
    ) from exc

CHECKPOINT_HOUR_ET = 17


def utc_today():
    """Today's date in UTC. Not date.today(), which is the machine's local date."""
    return datetime.now(timezone.utc).date()


def to_utc(when):
    """Coerce a datetime/date/None into an aware UTC datetime.

    A naive datetime is assumed to already be UTC. A plain date resolves to that day's
    NAV/S checkpoint, the only instant where the continuous price and the officially
    reported daily NAV/S are directly comparable.
    """
    if when is None:
        return datetime.now(timezone.utc)
    if isinstance(when, datetime):
        aware = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc)
    if isinstance(when, date):
        return checkpoint_utc(when)
    raise TypeError(f"expected datetime, date, or None; got {type(when).__name__}")


def checkpoint_utc(day):
    """The UTC instant of `day`'s 17:00 ET NAV/S checkpoint.

    Equals `checkpoints(N).timestamp` on-chain for the matching index, when one exists.
    """
    return datetime.combine(day, time(CHECKPOINT_HOUR_ET, 0),
                            tzinfo=NEW_YORK).astimezone(timezone.utc)


def end_of_day_utc(day):
    """23:59:59 UTC on `day` — a period-end valuation convention.

    Note this sits 2h (EST) to 3h (EDT) *after* the NAV/S checkpoint, so the continuous
    price here always reads above the officially reported daily NAV/S for the same date.
    """
    return datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc)


def iso(unix_ts):
    """Format a unix timestamp as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def describe_offset_from_checkpoint(target_dt, day):
    """Human-readable gap between `target_dt` and `day`'s checkpoint, for UI captions."""
    delta_h = (target_dt - checkpoint_utc(day)).total_seconds() / 3600
    if abs(delta_h) < 1 / 60:
        return "at the 17:00 ET NAV/S checkpoint"
    direction = "after" if delta_h > 0 else "before"
    return f"{abs(delta_h):.2f}h {direction} the 17:00 ET NAV/S checkpoint"


def parse_cli_instant(raw):
    """Parse a CLI argument into an aware UTC datetime.

    Accepts "2026-08-01" (-> that day's NAV/S checkpoint), "2026-08-01T14:32:07", or the
    same with an explicit offset such as "2026-08-01T14:32:07-04:00". An explicit offset
    is honoured rather than overwritten.
    """
    text = raw.strip().replace(" ", "T")
    if "T" not in text:
        return checkpoint_utc(date.fromisoformat(text))
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
