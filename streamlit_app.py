"""Superstate NAV/share lookup — off-chain API vs on-chain oracle vs official daily NAV/S."""
from datetime import datetime, timezone

import streamlit as st

import superstate_onchain_nav as onchain
from nav_time import (checkpoint_utc, describe_offset_from_checkpoint,
                      end_of_day_utc, utc_today)
from superstate_nav import FUND_IDS, NavUnavailable, get_nav_per_share_at, resolve_daily_nav

STATUS_STYLE = {
    "checkpoint": ("ok", "NAV/S calculated this date at 17:00 ET"),
    "weekend": ("warn", "No NAV/S checkpoint this date — weekend"),
    "holiday": ("warn", "No NAV/S checkpoint this date — market holiday"),
    "pending": ("warn", "NAV/S checkpoint not yet published"),
    "unavailable": ("warn", "No data for this date"),
}

# Internal keys for the time-basis radio. Kept separate from the displayed labels so the
# labels can carry a date-dependent UTC time without any branch keying off label text.
PRESET_CHECKPOINT = "checkpoint"
PRESET_EOD = "eod"
PRESET_NOW = "now"
PRESET_CUSTOM = "custom"

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
            <p class="header-subtitle">NAV per share at a UTC instant — off-chain API, on-chain oracle, and the official daily NAV/S checkpoint.</p>
        </div>
    </div>
""", unsafe_allow_html=True)

with st.container(border=True):
    st.markdown('<div class="section-label">📅 Lookup Parameters</div>', unsafe_allow_html=True)

    # The date must be chosen before the time-basis options are rendered, because the
    # checkpoint's UTC hour is date-dependent: 17:00 America/New_York is 21:00 UTC under
    # EDT and 22:00 UTC under EST. Labelling it "17:00 ET" inside a control marked UTC
    # forced the reader to do that conversion themselves.
    top_left, top_mid, top_right = st.columns([2, 2, 1.4])
    with top_left:
        fund = st.selectbox("Fund", list(FUND_IDS.keys()), key="fund")
    with top_mid:
        query_date = st.date_input("Date (UTC)", value=utc_today(), key="query_date")
    with top_right:
        st.markdown('<div style="height:29px;"></div>', unsafe_allow_html=True)
        query_clicked = st.button("Query NAV →", use_container_width=True)

    checkpoint_dt = checkpoint_utc(query_date)

    # Every widget below carries an explicit `key`. Without one, Streamlit derives the
    # widget's identity from its parameters, so anything volatile reaching the widget --
    # a clock in a format_func label, or the checkpoint time changing with the date --
    # makes it look like a brand new widget on the next rerun and silently resets it to
    # the first option. A stable key pins the identity and keeps the choice in
    # session_state. The current time is therefore kept out of the labels entirely and
    # shown in the caption instead, which is not a widget.
    # The option labels carry the actual UTC time, so no explanatory caption is needed
    # below them. The DST detail lives in the tooltip for anyone who wants it.
    preset = st.radio(
        "Time basis (all times UTC)",
        [PRESET_CHECKPOINT, PRESET_EOD, PRESET_NOW, PRESET_CUSTOM],
        horizontal=True,
        key="time_basis",
        format_func=lambda p: {
            PRESET_CHECKPOINT: f"NAV/S checkpoint — {checkpoint_dt:%H:%M:%S}",
            PRESET_EOD: "End of day — 23:59:59",
            PRESET_NOW: "Now",
            PRESET_CUSTOM: "Custom",
        }[p],
        help="The NAV/S checkpoint is 17:00 America/New_York, which is 21:00 UTC under EDT and "
             "22:00 UTC under EST — so it moves with US DST, and the option above shows the right "
             "UTC time for the date you picked. Only at the checkpoint are all three figures "
             "comparable; end of day sits 2–3h later, so the continuous price reads above the "
             "official daily NAV/S. Custom is minute-granularity (Streamlit's floor), which is "
             "finer than the data anyway — the 6th decimal only moves every ~82s.",
    )

    custom_time = None
    if preset == PRESET_CUSTOM:
        custom_time = st.time_input("Time (UTC)", value=checkpoint_dt.time(), step=60,
                                    key="custom_time")

if query_clicked:
    if preset == PRESET_CHECKPOINT:
        target_dt = checkpoint_utc(query_date)
    elif preset == PRESET_EOD:
        target_dt = end_of_day_utc(query_date)
    elif preset == PRESET_NOW:
        target_dt = datetime.now(timezone.utc)
    else:
        target_dt = datetime.combine(query_date, custom_time, tzinfo=timezone.utc)

    st.markdown(
        f'🕐 NAV/share as of <span style="color:#22d3ee; font-weight:700;">{target_dt.isoformat()}</span>'
        f' &nbsp;<span style="color:#94a3b8; font-size:13px;">({describe_offset_from_checkpoint(target_dt, query_date)})</span>',
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
            # Always ORACLE_VIEW. It brackets on effective_at, which is what the contract
            # itself does, so this reproduces exactly what a smart contract would have read.
            # The module still exposes HINDSIGHT_VIEW for analytical use from the CLI, but
            # it is deliberately not offered here: it is not an on-chain figure, and showing
            # it beside the official NAV/S invited recording it as one.
            st.markdown('<div class="result-icon">🔗</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="result-label-row"><span class="result-label">On-chain Oracle'
                '<span class="derived-tag">derived</span></span></div>',
                unsafe_allow_html=True,
            )
            chain_result = None
            try:
                chain_result = onchain.get_onchain_nav_per_share_at(
                    target_dt, view=onchain.ORACLE_VIEW)
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
                    note = (f'<span class="cap-warn">⚠️ {chain_result["age_days"]:.1f} days past the last checkpoint — '
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
                st.markdown('<div class="result-label-row"><span class="result-label">Official Daily NAV/S</span></div>',
                            unsafe_allow_html=True)
                st.markdown('<div class="result-price">—</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="result-caption-slot cap-bad">Lookup failed: {exc}</div>',
                            unsafe_allow_html=True)
            else:
                tone, headline = STATUS_STYLE[daily["status"]]
                # Label the date the NAV/S was actually calculated, never the date requested:
                # the API echoes the requested date back with a carried-forward value.
                as_of = (f'{daily["as_of_date"]:%m/%d/%Y} 17:00 ET' if daily["as_of_date"] else "—")
                st.markdown(
                    '<div class="result-label-row"><span class="result-label">Official Daily NAV/S</span>'
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
            st.markdown(f"**Daily NAV/S** — {daily['detail']}")
            if daily["carried_forward"]:
                st.warning(
                    f"The API row for {daily['requested_date']:%Y-%m-%d} is forward-filled. Its "
                    f"`net_asset_value_date` field reads **{daily['api_reported_date']}**, but no NAV "
                    f"was calculated that date — the value is **{daily['as_of_date']:%Y-%m-%d}**'s close. "
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
                args = chain_result["etherscan_inputs"]
                e_idx, l_idx = chain_result["earlier_index"], chain_result["later_index"]

                st.markdown(
                    f"**On-chain** — `{chain_result['price_units']}` (1e-6 units) computed by "
                    f"`calculateRealtimeNavs` from checkpoints `{e_idx}` and `{l_idx}`. Straight-line "
                    "interpolation between two daily anchors — this number is stored in no block, so "
                    "the figures below are what make it reproducible."
                )
                st.markdown("###### Re-perform this on Etherscan")
                st.markdown(
                    f"[Open the oracle's Read Contract tab]({onchain.ETHERSCAN_READ_URL}) → "
                    f"**Step 1:** call `checkpoints` for indices `{e_idx}` and `{l_idx}` to confirm the "
                    f"two anchors below are genuinely stored. **Step 2:** paste these five values into "
                    f"`calculateRealtimeNavs`."
                )
                st.code("\n".join(f"{name}\n{args[name]}" for name in onchain.CALC_PARAM_ORDER),
                        language="text")
                st.markdown(
                    f"Expected answer: **`{chain_result['price_units']}`** → ÷ 1e6 → "
                    f"**${chain_result['price']:.6f}** — must equal the card above."
                )

                st.markdown("###### How each input was derived")
                st.markdown(f"""
| Input | Value | Where it comes from |
|---|---|---|
| `targetTimestamp` | `{args['targetTimestamp']}` | Your query instant, {chain_result['requested_utc']}, as a unix timestamp |
| `earlierCheckpointNavs` | `{args['earlierCheckpointNavs']}` | `checkpoints({e_idx}).navs` |
| `earlierCheckpointTimestamp` | `{args['earlierCheckpointTimestamp']}` | `checkpoints({e_idx}).timestamp` — {chain_result['earlier_checkpoint_utc']} |
| `laterCheckpointNavs` | `{args['laterCheckpointNavs']}` | `checkpoints({l_idx}).navs` |
| `laterCheckpointTimestamp` | `{args['laterCheckpointTimestamp']}` | `checkpoints({l_idx}).timestamp` — {chain_result['later_checkpoint_utc']} |
""")
                st.markdown(
                    f"**Why checkpoints {e_idx} and {l_idx}?** The oracle brackets on "
                    f"**`effective_at`** — when a checkpoint becomes usable, not when its NAV/S was "
                    f"calculated. Checkpoint `{l_idx}` is the newest whose `effective_at` falls at or "
                    f"before your target instant, and `{e_idx}` is the one before it. Checkpoint "
                    f"`{l_idx}` became effective {chain_result['later_effective_utc']}."
                )
                if chain_result["extrapolated"]:
                    st.info(
                        f"Your target is **{chain_result['age_days']:.2f} days past** checkpoint {l_idx}, "
                        "so the contract extrapolates along that line rather than interpolating within "
                        "it. The figure is a projection, not an official NAV/S."
                    )
                st.caption(f"Oracle contract `{onchain.ORACLE_ADDRESS}` · selector "
                           f"`0x{onchain.SEL_CALC_REALTIME_NAVS}`")
