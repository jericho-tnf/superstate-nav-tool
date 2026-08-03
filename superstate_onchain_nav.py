"""
superstate_onchain_nav.py — Query USTB's NAV/share directly from Superstate's
on-chain price oracle on Ethereum, for a specific point in time.
Requires: pip install requests
"""
import requests
from datetime import datetime, timezone, date, time

RPC_URL = "https://ethereum-rpc.publicnode.com"  # free public Ethereum RPC, no key needed
ORACLE_ADDRESS = "0xe4fa682f94610ccd170680cc3b045d77d9e528a8"  # Superstate USTB Continuous Price Oracle
DECIMALS = 6
CHECKPOINT_EXPIRATION_PERIOD = 5 * 24 * 60 * 60  # 5 days, matches the on-chain constant

SEL_CHECKPOINTS = "b8a24252"           # checkpoints(uint256)
SEL_CALC_REALTIME_NAVS = "62955b9b"    # calculateRealtimeNavs(uint128,uint128,uint128,uint128,uint128)


def _pad(n):
    return format(int(n), "x").zfill(64)


def _eth_call(data):
    resp = requests.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
              "params": [{"to": ORACLE_ADDRESS, "data": "0x" + data}, "latest"]},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        return None
    result = payload.get("result")
    if not result or result == "0x":
        return None
    return result[2:]


def _words(hexdata):
    return [int(hexdata[i:i + 64], 16) for i in range(0, len(hexdata), 64)]


def get_checkpoint(index):
    raw = _eth_call(SEL_CHECKPOINTS + _pad(index))
    if raw is None:
        return None
    ts, effective_at, navs = _words(raw)
    return {"timestamp": ts, "effective_at": effective_at, "navs": navs}


def _checkpoint_count():
    lo, hi = 0, 1
    while get_checkpoint(hi) is not None:
        lo, hi = hi, hi * 2
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if get_checkpoint(mid) is not None:
            lo = mid
        else:
            hi = mid
    return lo + 1


def _find_bracket(target_ts, count):
    first = get_checkpoint(0)
    if target_ts < first["effective_at"]:
        raise ValueError("Requested time is before the oracle's first available checkpoint.")
    lo, hi = 0, count - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cp = get_checkpoint(mid)
        if cp["effective_at"] <= target_ts:
            lo = mid
        else:
            hi = mid - 1
    later_idx = lo
    earlier_idx = max(later_idx - 1, 0)
    return earlier_idx, later_idx


def get_onchain_nav_per_share_at(when):
    """`when` can be a datetime (naive = UTC), a date (23:59:00 UTC that day), or None (now)."""
    if when is None:
        target_dt = datetime.now(timezone.utc)
    elif isinstance(when, datetime):
        target_dt = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    elif isinstance(when, date):
        target_dt = datetime.combine(when, time(23, 59, 0), tzinfo=timezone.utc)
    else:
        raise TypeError("when must be a datetime, date, or None")

    target_ts = int(target_dt.timestamp())
    count = _checkpoint_count()
    earlier_idx, later_idx = _find_bracket(target_ts, count)
    earlier = get_checkpoint(earlier_idx)
    later = get_checkpoint(later_idx)
    stale = target_ts - later["effective_at"] > CHECKPOINT_EXPIRATION_PERIOD

    data = (
        SEL_CALC_REALTIME_NAVS
        + _pad(target_ts) + _pad(earlier["navs"]) + _pad(earlier["timestamp"])
        + _pad(later["navs"]) + _pad(later["timestamp"])
    )
    answer = _words(_eth_call(data))[0]

    return {
        "price": answer / (10 ** DECIMALS),
        "requested_utc": target_dt.isoformat(),
        "earlier_checkpoint_utc": datetime.fromtimestamp(earlier["timestamp"], tz=timezone.utc).isoformat(),
        "later_checkpoint_utc": datetime.fromtimestamp(later["timestamp"], tz=timezone.utc).isoformat(),
        "stale": stale,
    }


if __name__ == "__main__":
    import sys
    target = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    result = get_onchain_nav_per_share_at(target)
    print(f"USTB on-chain NAV/S as of {result['requested_utc']}: {result['price']}")
    print(f"  bracketing checkpoints: {result['earlier_checkpoint_utc']} -> {result['later_checkpoint_utc']}")
    if result["stale"]:
        print("  WARNING: beyond the oracle's 5-day extrapolation window (stale).")