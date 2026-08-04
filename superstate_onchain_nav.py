"""
superstate_onchain_nav.py — Query USTB's NAV/share from Superstate's on-chain
continuous price oracle on Ethereum.

What is actually stored on-chain is ONE checkpoint per business day: the 17:00 ET
NAV/S checkpoint, published the next business morning at ~13:07 UTC. Every value between
those daily anchors is computed at read time by `calculateRealtimeNavs`, a pure
function that straight-line interpolates between two checkpoints. So a price
returned for 13:15 UTC is *derived* on-chain, not stored on-chain — no block ever
contains it. The arithmetic is the contract's (executed by a node via eth_call),
but the number is not retrievable data.

Verified against mainnet:
  checkpoints(uint256)                 selector b8a24252  (keccak-checked)
  calculateRealtimeNavs(uint128 x5)    selector 62955b9b  (keccak-checked)
  decimals()                           == 6
  CHECKPOINT_EXPIRATION_PERIOD()       == 432000 (5 days)
  timestamp and effective_at are both strictly increasing over all checkpoints,
  and effective_at > timestamp always (publication lag 0.67d to 3.68d).

Requires: pip install requests tzdata
"""
import time as _time
from datetime import datetime, timezone, date

import requests

from nav_time import iso, checkpoint_utc, to_utc, utc_today

RPC_URL = "https://ethereum-rpc.publicnode.com"  # free public Ethereum RPC, no key needed
ORACLE_ADDRESS = "0xe4fa682f94610ccd170680cc3b045d77d9e528a8"  # Superstate USTB Continuous Price Oracle
DECIMALS = 6
CHECKPOINT_EXPIRATION_PERIOD = 5 * 24 * 60 * 60

SEL_CHECKPOINTS = "b8a24252"           # checkpoints(uint256)
SEL_CALC_REALTIME_NAVS = "62955b9b"    # calculateRealtimeNavs(uint128,uint128,uint128,uint128,uint128)

ETHERSCAN_READ_URL = f"https://etherscan.io/address/{ORACLE_ADDRESS}#readContract"

# ABI parameter order of calculateRealtimeNavs, which is also the order the Etherscan
# Read Contract form presents. Keep these aligned or the worksheet misleads.
CALC_PARAM_ORDER = (
    "targetTimestamp",
    "earlierCheckpointNavs",
    "earlierCheckpointTimestamp",
    "laterCheckpointNavs",
    "laterCheckpointTimestamp",
)

# Which checkpoint field to bracket the target instant against.
#
#   ORACLE_VIEW    keys on effective_at, reproducing exactly what the oracle reported
#                  at that instant — i.e. what a smart contract reading it would have
#                  seen. A checkpoint is invisible until it is published, so a Saturday
#                  query extrapolates off the Wed->Thu slope even though Friday's NAV
#                  was already set.
#   HINDSIGHT_VIEW keys on timestamp, using every checkpoint now known. This is what you
#                  want when reconciling against officially reported NAV, because it
#                  does not pretend not to know Friday's number.
#
# The two agree except inside publication gaps, where they diverge by up to ~0.04 bps.
ORACLE_VIEW = "oracle"
HINDSIGHT_VIEW = "hindsight"
_BRACKET_KEY = {ORACLE_VIEW: "effective_at", HINDSIGHT_VIEW: "timestamp"}

_COUNT_TTL_SECONDS = 300


class OracleRpcError(RuntimeError):
    """A transport or node failure — deliberately distinct from a contract revert."""


# The checkpoint array is append-only, so a checkpoint once read never changes and is
# safe to cache forever. Only the count can grow, so that gets a short TTL.
_checkpoint_cache = {}
_count_cache = {"value": None, "fetched_at": 0.0}


def clear_cache():
    _checkpoint_cache.clear()
    _count_cache.update(value=None, fetched_at=0.0)


def _pad(value):
    number = int(value)
    if number < 0:
        raise ValueError(f"cannot ABI-encode a negative value: {number}")
    return format(number, "x").zfill(64)


def _is_contract_revert(error):
    """True only for a genuine revert, which callers may read as 'no such value'.

    Out-of-bounds array access returns {'code': 3, 'message': 'execution reverted'}.
    Rate limits, node errors and timeouts must NOT be mistaken for this: the
    checkpoint-count search treats None as 'past the end of the array', so a throttled
    response misread as a revert would silently truncate the array and hand back a
    stale price with no error raised.
    """
    return error.get("code") == 3 and "revert" in (error.get("message") or "").lower()


def _eth_call(data, attempts=4):
    """eth_call against the oracle. Returns hex payload, or None on a genuine revert.

    Raises OracleRpcError if the node cannot be reached or answers with a non-revert
    error, rather than returning None and letting a caller misinterpret it.
    """
    last_failure = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                RPC_URL,
                json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                      "params": [{"to": ORACLE_ADDRESS, "data": "0x" + data}, "latest"]},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            last_failure = exc
        else:
            error = payload.get("error")
            if error is None:
                result = payload.get("result")
                return None if not result or result == "0x" else result[2:]
            if _is_contract_revert(error):
                return None
            last_failure = OracleRpcError(f"RPC error {error.get('code')}: {error.get('message')}")
        if attempt + 1 < attempts:
            _time.sleep(0.4 * (2 ** attempt))
    raise OracleRpcError(f"eth_call to {RPC_URL} failed after {attempts} attempts: {last_failure}")


def _words(hexdata):
    if hexdata is None:
        raise OracleRpcError("expected return data from eth_call but the call produced none")
    return [int(hexdata[i:i + 64], 16) for i in range(0, len(hexdata), 64)]


def get_checkpoint(index):
    """One stored checkpoint, or None if `index` is past the end of the array."""
    if index in _checkpoint_cache:
        return _checkpoint_cache[index]
    raw = _eth_call(SEL_CHECKPOINTS + _pad(index))
    if raw is None:
        return None
    timestamp, effective_at, navs = _words(raw)
    checkpoint = {
        "index": index,
        "timestamp": timestamp,        # the NAV's as-of instant (17:00 ET)
        "effective_at": effective_at,  # when the oracle starts using it
        "navs": navs,                  # NAV/share in 1e-6 units
    }
    _checkpoint_cache[index] = checkpoint
    return checkpoint


def checkpoint_count(force=False):
    """How many checkpoints the oracle holds."""
    cached = _count_cache["value"]
    if cached is not None and not force and _time.time() - _count_cache["fetched_at"] < _COUNT_TTL_SECONDS:
        return cached

    if get_checkpoint(0) is None:
        raise OracleRpcError("the oracle reports no checkpoints at all")
    low, high = 0, 1
    while get_checkpoint(high) is not None:
        low, high = high, high * 2
    while high - low > 1:
        mid = (low + high) // 2
        if get_checkpoint(mid) is not None:
            low = mid
        else:
            high = mid

    count = low + 1
    _count_cache.update(value=count, fetched_at=_time.time())
    return count


def latest_checkpoint():
    return get_checkpoint(checkpoint_count() - 1)


def checkpoint_on_or_before(day):
    """The most recent checkpoint at or before `day`, or None if there is none."""
    want = int(checkpoint_utc(day).timestamp())
    count = checkpoint_count()
    if get_checkpoint(0)["timestamp"] > want:
        return None
    low, high = 0, count - 1
    while low < high:
        mid = (low + high + 1) // 2
        if get_checkpoint(mid)["timestamp"] <= want:
            low = mid
        else:
            high = mid - 1
    return get_checkpoint(low)


def find_checkpoint_for_date(day):
    """The checkpoint for `day`, or None if no NAV/S was calculated that date.

    This is the authoritative test for whether a calendar date has a real NAV: a
    checkpoint exists if and only if the fund calculated a NAV/S that day. It catches weekends
    market holidays alike without needing a holiday calendar.
    """
    candidate = checkpoint_on_or_before(day)
    if candidate is None:
        return None
    return candidate if candidate["timestamp"] == int(checkpoint_utc(day).timestamp()) else None


def _find_bracket(target_ts, count, view=ORACLE_VIEW):
    """Indices of the two checkpoints the oracle interpolates between."""
    key = _BRACKET_KEY[view]
    first = get_checkpoint(0)
    if target_ts < first[key]:
        raise ValueError(
            f"{iso(target_ts)} is before the oracle's first usable checkpoint "
            f"({iso(first[key])} by {key}), so no on-chain price exists for it."
        )

    low, high = 0, count - 1
    while low < high:
        mid = (low + high + 1) // 2
        if get_checkpoint(mid)[key] <= target_ts:
            low = mid
        else:
            high = mid - 1

    if low == 0:
        # Interpolation needs two distinct anchors; with only checkpoint 0 in scope the
        # contract would divide by a zero time delta and revert.
        raise ValueError(
            f"Only checkpoint 0 is usable at {iso(target_ts)}, so the oracle cannot "
            f"interpolate. In the {ORACLE_VIEW!r} view this affects instants between "
            f"checkpoints 0 and 1 becoming effective; the {HINDSIGHT_VIEW!r} view can "
            "usually still price them."
        )
    return low - 1, low


def get_onchain_nav_per_share_at(when, view=ORACLE_VIEW):
    """NAV/share computed on-chain for a point in time.

    `when` may be a datetime (naive treated as UTC), a date (that day's 17:00 ET
    NAV/S checkpoint), or None for now.
    """
    if view not in _BRACKET_KEY:
        raise ValueError(f"view must be one of {sorted(_BRACKET_KEY)}, got {view!r}")

    target_dt = to_utc(when)
    target_ts = int(target_dt.timestamp())
    count = checkpoint_count()

    earlier_index, later_index = _find_bracket(target_ts, count, view)
    earlier, later = get_checkpoint(earlier_index), get_checkpoint(later_index)

    # Expiry is measured from the NAV's as-of date, not its publication time. The value
    # being guarded is an extrapolation forward from `timestamp` — which is exactly what
    # calculateRealtimeNavs is fed below — so measuring from effective_at instead would
    # be lenient by the publication lag, up to 3.68 days on the observed history.
    age_seconds = target_ts - later["timestamp"]

    answer = _words(_eth_call(
        SEL_CALC_REALTIME_NAVS
        + _pad(target_ts) + _pad(earlier["navs"]) + _pad(earlier["timestamp"])
        + _pad(later["navs"]) + _pad(later["timestamp"])
    ))[0]

    return {
        "price": answer / (10 ** DECIMALS),
        "price_units": answer,  # exact integer as returned by the contract
        "view": view,
        "requested_utc": target_dt.isoformat(),
        "earlier_index": earlier_index,
        "later_index": later_index,
        "earlier_checkpoint_utc": iso(earlier["timestamp"]),
        "later_checkpoint_utc": iso(later["timestamp"]),
        "later_effective_utc": iso(later["effective_at"]),
        "extrapolated": target_ts > later["timestamp"],
        "age_days": age_seconds / 86400,
        "stale": age_seconds > CHECKPOINT_EXPIRATION_PERIOD,
        "interpolated_from_daily": True,
        # Exactly what to paste into the Etherscan Read Contract form to re-perform
        # this figure by hand. Ordered to match the ABI and the form's field order.
        "etherscan_inputs": {
            "targetTimestamp": target_ts,
            "earlierCheckpointNavs": earlier["navs"],
            "earlierCheckpointTimestamp": earlier["timestamp"],
            "laterCheckpointNavs": later["navs"],
            "laterCheckpointTimestamp": later["timestamp"],
        },
    }


def etherscan_worksheet(result):
    """A copy-pasteable re-performance worksheet for a get_onchain_nav_per_share_at result.

    Reproducing this on a block explorer tests the only two things this module decides:
    the target timestamp it derived from your query, and which two checkpoints it picked.
    The interpolation itself is the contract's, so it is not under test.
    """
    args = result["etherscan_inputs"]
    key = _BRACKET_KEY[result["view"]]
    e_idx, l_idx = result["earlier_index"], result["later_index"]

    lines = [
        f"Oracle contract : {ORACLE_ADDRESS}",
        f"Read Contract   : {ETHERSCAN_READ_URL}",
        "",
        f"STEP 1 - read the two stored checkpoints (function: checkpoints)",
        f"  checkpoints({e_idx})  ->  navs={args['earlierCheckpointNavs']}  "
        f"timestamp={args['earlierCheckpointTimestamp']}  ({result['earlier_checkpoint_utc']})",
        f"  checkpoints({l_idx})  ->  navs={args['laterCheckpointNavs']}  "
        f"timestamp={args['laterCheckpointTimestamp']}  ({result['later_checkpoint_utc']})",
        "",
        f"STEP 2 - paste into calculateRealtimeNavs (selector 0x{SEL_CALC_REALTIME_NAVS})",
    ]
    for name in CALC_PARAM_ORDER:
        lines.append(f"  {name:28s} {args[name]}")
    lines += [
        "",
        f"STEP 3 - expected result",
        f"  answer (uint128)             {result['price_units']}",
        f"  divided by 1e{DECIMALS}                ${result['price']:.6f}",
        "",
        f"Bracket rationale ({result['view']} view, keyed on {key}):",
        f"  checkpoint {l_idx} is the newest whose {key} is at or before the target instant;",
        f"  checkpoint {e_idx} is the one before it. Those two define the line.",
    ]
    if result["extrapolated"]:
        lines.append(f"  The target is {result['age_days']:.2f} days PAST checkpoint {l_idx}, so this "
                     f"extrapolates beyond the line rather than interpolating within it.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    from nav_time import parse_cli_instant

    target = parse_cli_instant(sys.argv[1]) if len(sys.argv) > 1 else checkpoint_utc(utc_today())
    requested_view = sys.argv[2] if len(sys.argv) > 2 else ORACLE_VIEW

    result = get_onchain_nav_per_share_at(target, view=requested_view)
    print(f"USTB on-chain NAV/S at {result['requested_utc']} [{result['view']} view]: {result['price']:.6f}")
    print(f"  computed from checkpoints {result['earlier_index']} -> {result['later_index']}"
          f"  ({result['earlier_checkpoint_utc']} -> {result['later_checkpoint_utc']})")
    print("  this value is interpolated from daily checkpoints, not stored on-chain")
    if result["extrapolated"]:
        print(f"  NOTE: extrapolated {result['age_days']:.2f} days past the last usable checkpoint.")
    if result["stale"]:
        print(f"  WARNING: {result['age_days']:.2f} days past the last checkpoint, beyond the "
              f"oracle's {CHECKPOINT_EXPIRATION_PERIOD // 86400}-day expiration window.")
