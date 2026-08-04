"""compare_nav.py — Reconcile the off-chain API, the on-chain oracle, and the official daily NAV.

Usage:
    python compare_nav.py                            # today's 17:00 ET strike
    python compare_nav.py 2026-07-27                 # that date's strike
    python compare_nav.py 2026-07-27T23:59:59        # a specific UTC instant
    python compare_nav.py 2026-07-27 --worksheet     # + Etherscan re-performance steps
"""
import sys

import superstate_onchain_nav as onchain
from nav_time import describe_offset_from_strike, parse_cli_instant, strike_utc, utc_today
from superstate_nav import NavUnavailable, get_nav_per_share_at, resolve_daily_nav

if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_worksheet = "--worksheet" in sys.argv
    target_dt = parse_cli_instant(argv[0]) if argv else strike_utc(utc_today())
    target_date = target_dt.date()

    print(f"USTB NAV/S at {target_dt.isoformat()}")
    print(f"  ({describe_offset_from_strike(target_dt, target_date)})\n")

    api_price = chain_price = None

    try:
        api_price = get_nav_per_share_at(target_dt, fund="USTB")["price"]
        print(f"  Off-chain API (continuous)          : {api_price:.6f}")
    except NavUnavailable as exc:
        print(f"  Off-chain API (continuous)          : unavailable — {exc}")

    try:
        chain = onchain.get_onchain_nav_per_share_at(target_dt, view=onchain.ORACLE_VIEW)
        chain_price = chain["price"]
        print(f"  On-chain oracle (derived, continuous): {chain_price:.6f}"
              f"   [checkpoints {chain['earlier_index']}->{chain['later_index']}]")
    except (ValueError, onchain.OracleRpcError) as exc:
        chain = None
        print(f"  On-chain oracle (derived, continuous): unavailable — {exc}")

    daily = resolve_daily_nav(target_date, fund="USTB")
    if daily["status"] == "unavailable":
        print(f"  Official daily NAV                  : {daily['detail']}")
    else:
        print(f"  Official daily NAV                  : {daily['nav_text']}"
              f"   (as of {daily['as_of_date']:%Y-%m-%d} 17:00 ET)")
        if daily["carried_forward"]:
            print(f"      CAUTION: no NAV was struck on {target_date:%Y-%m-%d} ({daily['status']}).")
            print(f"      {daily['detail']}")

    if api_price is not None and chain_price is not None:
        print(f"\n  API vs on-chain difference: {abs(api_price - chain_price):.6f}")

    if chain is not None:
        if chain["stale"]:
            print(f"  WARNING: {chain['age_days']:.2f} days past the last strike — beyond the "
                  "oracle's 5-day expiry window.")
        elif chain["extrapolated"]:
            print(f"  NOTE: extrapolated {chain['age_days']:.2f} days past the last usable strike.")

    if want_worksheet and chain is not None:
        for view in (onchain.ORACLE_VIEW, onchain.HINDSIGHT_VIEW):
            try:
                result = onchain.get_onchain_nav_per_share_at(target_dt, view=view)
            except (ValueError, onchain.OracleRpcError) as exc:
                print(f"\n{'=' * 72}\nWORKSHEET [{view}] unavailable: {exc}")
                continue
            print(f"\n{'=' * 72}")
            print(f"ETHERSCAN RE-PERFORMANCE WORKSHEET  [{view} view]")
            print("=" * 72)
            print(onchain.etherscan_worksheet(result))
