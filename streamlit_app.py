"""Superstate NAV/share lookup — off-chain API vs on-chain oracle vs official daily NAV."""
from datetime import datetime, time, timezone

import streamlit as st

import superstate_onchain_nav as onchain
from nav_time import describe_offset_from_strike, end_of_day_utc, strike_utc, utc_today
from superstate_nav import FUND_IDS, NavUnavailable, get_nav_per_share_at, resolve_daily_nav

STATUS_STYLE = {
    "strike": ("ok", "Struck this date at 17:00 ET"),
    "weekend": ("warn", "No strike this date — weekend"),
    "holiday": ("warn", "No strike this date — market holiday"),
    "pending": ("warn", "Strike not yet published"),
    "unavailable": ("warn", "No data for this date"),
}

st.set_page_config(page_title="Superstate NAV Lookup", page_icon="📈", layout="centered")

st.markdown("""
    <style>
    #MainMenu, header, footer, [data-testid="stToolbar"],
    [data-testid="stDecoration"], [data-testid="stStatusWidget"] { visibility: hidden; }

    .stApp { background-color: #0b0f19; }

    .header-row { display: flex; align-items: center; gap: 18px; margin-bottom: 24px; }
    .header-icon {
        width: 64px; height: 64px; border-radius: 16px;
        background: linear-gradient(135deg, #0891b2, #22d3ee);
        display: flex; align-items: center; justify-content: center; font-size: 28px; flex-shrink: 0;
    }
    .header-title { font-size: 30px; font-weight: 800; color: white; margin: 0; }
    .header-subtitle { color: #94a3b8; margin: 0; font-size: 14px; }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #101827;
        border: 1px solid #1e293b !important;
        border-radius: 14px !important;
    }

    .section-label { color: #22d3ee; font-weight: 600; font-size: 15px; margin-bottom: 10px; }
    .result-icon {
        width: 34px; height: 34px; border-radius: 8px; background: #0e2230;
        display: flex; align-items: center; justify-content: center; font-size: 16px; margin-bottom: 8px;
    }
    .result-label-row {
        display: flex; justify-content: space-between; align-items: baseline;
        min-height: 18px; margin-bottom: 4px; gap: 8px;
    }
    .result-label { color: #cbd5e1; font-size: 15px; font-weight: 500; }
    .result-date { color: #94a3b8; font-size: 12px; white-space: nowrap; }
    .result-price { color: white; font-size: 30px; font-weight: 800; margin: 4px 0; }
    .result-caption-slot { min-height: 34px; font-size: 12.5px; line-height: 1.35; }
    .cap-warn { color: #f59e0b; }
    .cap-ok { color: #64748b; }
    .cap-bad { color: #f87171; }
    .derived-tag {
        display: inline-block; font-size: 10.5px; font-weight: 600; letter-spacing: .04em;
        text-transform: uppercase; color: #7dd3fc; background: #0c2b3a;
        border: 1px solid #164e63; border-radius: 5px; padding: 1px 6px; margin-left: 6px;
    }

    .stButton > button {
        background: linear-gradient(135deg, #0891b2, #22d3ee);
        color: white; border: none; border-radius: 10px;
        padding: 10px 18px; font-weight: 600; font-size: 15px; width: 100%; white-space: nowrap;
    }
    .stButton > button:hover { opacity: 0.9; color: white; }
    </style>
""", unsafe_allow_html=True)

st.markdown("""
    <div class="header-row">
        <div class="header-icon">📈</div>
        <div>
            <p class="header-title">Superstate NAV/Share Lookup</p>
            <p class="header-subtitle">NAV per share at a UTC instant — off-chain API, on-chain oracle, and the official daily strike.</p>
        </div>
    </div>
""", unsafe_allow_html=True)

with st.container(border=True):
    st.markdown('<div class="section-label">📅 Lookup Parameters</div>', unsafe_allow_html=True)

    top_left, top_right = st.columns([2, 3])
    with top_left:
        fund = st.selectbox("Fund", list(FUND_IDS.keys()))
    with top_right:
        # The strike is 17:00 America/New_York, which is 21:00 UTC under EDT and 22:00
        # under EST — so the UTC instant to query is date-dependent, not a constant.
        preset = st.radio(
            "Time basis (UTC)",
            ["Strike — 17:00 ET", "End of day — 23:59:59", "Now", "Custom"],
            horizontal=True,
            help="Only at the strike are all three figures comparable. End of day sits "
                 "2h (EST) to 3h (EDT) later, so the continuous NAV reads above the daily NAV.",
        )

    bottom_left, bottom_mid, bottom_right = st.columns([2, 2, 1.4])
    with bottom_left:
        query_date = st.date_input("Date (UTC)", value=utc_today())
    with bottom_mid:
        if preset == "Custom":
            # Streamlit's time_input rejects step < 60s, so minute granularity is the
            # floor here. That is finer than the data: at ~0.00106/day accrual the 6th
            # decimal only moves every ~82 seconds.
            custom_time = st.time_input("Time (UTC)", value=time(21, 0), step=60)
        else:
            custom_time = None
            st.markdown('<div style="height:29px;"></div>', unsafe_allow_html=True)
            st.caption({
                "Strike — 17:00 ET": f"→ {strike_utc(query_date).strftime('%H:%M')} UTC on this date",
                "End of day — 23:59:59": "→ 23:59:59 UTC",
                "Now": "→ current UTC time",
            }[preset])
    with bottom_right:
        st.markdown('<div style="height:29px;"></div>', unsafe_allow_html=True)
        query_clicked = st.button("Query NAV →", use_container_width=True)

    view = st.radio(
        "On-chain bracketing",
        [onchain.ORACLE_VIEW, onchain.HINDSIGHT_VIEW],
        horizontal=True,
        format_func=lambda v: {
            onchain.ORACLE_VIEW: "As the oracle saw it (by effective_at)",
            onchain.HINDSIGHT_VIEW: "Best estimate, all strikes known (by timestamp)",
        }[v],
        help="A strike is invisible to the oracle until published (0.67–3.68 days later). "
             "'As the oracle saw it' reproduces what a smart contract would have read at that "
             "instant. 'Best estimate' uses every strike now known — better for reconciliation. "
             "They differ only inside publication gaps, by up to ~0.04 bps.",
    )

if query_clicked:
    if preset == "Strike — 17:00 ET":
        target_dt = strike_utc(query_date)
    elif preset == "End of day — 23:59:59":
        target_dt = end_of_day_utc(query_date)
    elif preset == "Now":
        target_dt = datetime.now(timezone.utc)
    else:
        target_dt = datetime.combine(query_date, custom_time, tzinfo=timezone.utc)

    st.markdown(
        f'🕐 NAV/share as of <span style="color:#22d3ee; font-weight:700;">{target_dt.isoformat()}</span>'
        f' &nbsp;<span style="color:#94a3b8; font-size:13px;">({describe_offset_from_strike(target_dt, query_date)})</span>',
        unsafe_allow_html=True,
    )

    api_col, chain_col, daily_col = st.columns(3)

    with api_col:
        with st.container(border=True):
            st.markdown('<div class="result-icon">☁️</div>', unsafe_allow_html=True)
            st.markdown('<div class="result-label-row"><span class="result-label">Off-chain API</span></div>',
                        unsafe_allow_html=True)
            try:
                api_result = get_nav_per_share_at(target_dt, fund)
            except NavUnavailable as exc:
                st.markdown('<div class="result-price">—</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="result-caption-slot cap-bad">{exc}</div>', unsafe_allow_html=True)
            except Exception as exc:
                st.markdown('<div class="result-price">—</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="result-caption-slot cap-bad">Request failed: {exc}</div>',
                            unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="result-price">${api_result["price"]:.6f}</div>', unsafe_allow_html=True)
                if api_result["clamped_from_future"]:
                    note = ('<span class="cap-warn">⚠️ That instant is in the future — the API answered '
                            f'for {api_result["actual_utc"][:19]}Z instead.</span>')
                elif api_result["snapped_to_nearest_available"]:
                    note = f'<span class="cap-warn">⚠️ Snapped to {api_result["actual_utc"][:19]}Z</span>'
                else:
                    note = '<span class="cap-ok">Continuous price, exact timestamp</span>'
                st.markdown(f'<div class="result-caption-slot">{note}</div>', unsafe_allow_html=True)

    with chain_col:
        with st.container(border=True):
            st.markdown('<div class="result-icon">🔗</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="result-label-row"><span class="result-label">On-chain Oracle'
                '<span class="derived-tag">derived</span></span></div>',
                unsafe_allow_html=True,
            )
            chain_result = None
            try:
                chain_result = onchain.get_onchain_nav_per_share_at(target_dt, view=view)
            except (ValueError, onchain.OracleRpcError) as exc:
                st.markdown('<div class="result-price">—</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="result-caption-slot cap-bad">{exc}</div>', unsafe_allow_html=True)
            except Exception as exc:
                st.markdown('<div class="result-price">—</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="result-caption-slot cap-bad">Oracle read failed: {exc}</div>',
                            unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="result-price">${chain_result["price"]:.6f}</div>', unsafe_allow_html=True)
                if chain_result["stale"]:
                    note = (f'<span class="cap-warn">⚠️ {chain_result["age_days"]:.1f} days past the last strike — '
                            'beyond the oracle\'s 5-day expiry. Treat as unusable.</span>')
                elif chain_result["extrapolated"]:
                    note = (f'<span class="cap-warn">Extrapolated {chain_result["age_days"]:.2f} days '
                            f'past checkpoint {chain_result["later_index"]}</span>')
                else:
                    note = ('<span class="cap-ok">Interpolated between checkpoints '
                            f'{chain_result["earlier_index"]}–{chain_result["later_index"]}</span>')
                st.markdown(f'<div class="result-caption-slot">{note}</div>', unsafe_allow_html=True)

    with daily_col:
        with st.container(border=True):
            st.markdown('<div class="result-icon">🛡️</div>', unsafe_allow_html=True)
            daily = None
            try:
                daily = resolve_daily_nav(query_date, fund)
            except Exception as exc:
                st.markdown('<div class="result-label-row"><span class="result-label">Official Daily NAV</span></div>',
                            unsafe_allow_html=True)
                st.markdown('<div class="result-price">—</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="result-caption-slot cap-bad">Lookup failed: {exc}</div>',
                            unsafe_allow_html=True)
            else:
                tone, headline = STATUS_STYLE[daily["status"]]
                # Label the date the NAV was actually struck, never the date requested:
                # the API echoes the requested date back with a carried-forward value.
                as_of = (f'{daily["as_of_date"]:%m/%d/%Y} 17:00 ET' if daily["as_of_date"] else "—")
                st.markdown(
                    '<div class="result-label-row"><span class="result-label">Official Daily NAV</span>'
                    f'<span class="result-date">📅 {as_of}</span></div>',
                    unsafe_allow_html=True,
                )
                price = f'${float(daily["nav_text"]):.8f}' if daily["nav_text"] else "—"
                st.markdown(f'<div class="result-price">{price}</div>', unsafe_allow_html=True)
                icon = "⚠️ " if tone == "warn" else ""
                st.markdown(
                    f'<div class="result-caption-slot cap-{tone}">{icon}{headline}'
                    + (f'<br>Showing {daily["as_of_date"]:%a %d %b} close.' if daily["carried_forward"] else "")
                    + "</div>",
                    unsafe_allow_html=True,
                )

    if daily and daily["status"] != "unavailable":
        with st.expander("Provenance & reconciliation"):
            st.markdown(f"**Daily NAV** — {daily['detail']}")
            if daily["carried_forward"]:
                st.warning(
                    f"The API row for {daily['requested_date']:%Y-%m-%d} is forward-filled. Its "
                    f"`net_asset_value_date` field reads **{daily['api_reported_date']}**, but no NAV "
                    f"was struck that date — the value is **{daily['as_of_date']:%Y-%m-%d}**'s close. "
                    "That field echoes whatever date you request, so it carries no provenance."
                )
            if daily["checkpoint_index"] is not None:
                verdict = "matches" if daily["matches_onchain"] else "**does NOT match**"
                st.markdown(
                    f"Cross-check: on-chain checkpoint `{daily['checkpoint_index']}` {verdict} "
                    f"the API's reported value of `{daily['nav_text']}`."
                )
            st.markdown(
                f"AUM `{daily['assets_under_management']}` · shares outstanding "
                f"`{daily['outstanding_shares']}`"
            )

            if chain_result:
                st.markdown("---")
                st.markdown(
                    f"**On-chain** — value `{chain_result['price_units']}` (1e-6 units) computed by "
                    f"`calculateRealtimeNavs` from checkpoints `{chain_result['earlier_index']}` "
                    f"({chain_result['earlier_checkpoint_utc']}) and `{chain_result['later_index']}` "
                    f"({chain_result['later_checkpoint_utc']}, effective "
                    f"{chain_result['later_effective_utc']}). Straight-line interpolation between two "
                    "daily anchors — this number is not stored in any block."
                )
                st.caption(f"Oracle contract `{onchain.ORACLE_ADDRESS}` · bracketing view "
                           f"`{chain_result['view']}`")
