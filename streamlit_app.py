import streamlit as st
from datetime import datetime, timezone

from superstate_nav import get_nav_per_share_at, get_daily_close_nav
from superstate_onchain_nav import get_onchain_nav_per_share_at

FUND_IDS = {"USTB": 1, "USCC": 2}

st.set_page_config(page_title="Superstate NAV Lookup", page_icon="📈")
st.title("Superstate NAV/Share Lookup")
st.caption("Query NAV per share at a specific UTC timestamp — off-chain (API) vs on-chain (oracle).")

fund = st.selectbox("Fund", list(FUND_IDS.keys()))
col_a, col_b = st.columns(2)
with col_a:
    date = st.date_input("Date")
with col_b:
    time = st.time_input("Time (UTC)", value=datetime.strptime("23:59:00", "%H:%M:%S").time())

if st.button("Query NAV"):
    dt = datetime.combine(date, time).replace(tzinfo=timezone.utc)
    st.write(f"NAV/share as of **{dt.isoformat()}**")

    col1, col2, col3 = st.columns(3)

    try:
        api_result = get_nav_per_share_at(dt, fund)
        with col1:
            st.metric("Off-chain API", f"${api_result['price']:.6f}")
            if api_result.get("snapped_to_nearest_available"):
                st.caption("⚠️ Snapped to nearest available data")
    except Exception as e:
        with col1:
            st.error(f"Error: {e}")

    if fund == "USTB":
        try:
            onchain_result = get_onchain_nav_per_share_at(dt)
            with col2:
                st.metric("On-chain Oracle", f"${onchain_result['price']:.6f}")
                if onchain_result.get("stale"):
                    st.caption("⚠️ Checkpoint data may be stale")
        except Exception as e:
            with col2:
                st.error(f"Error: {e}")
    else:
        with col2:
            st.info("USTB only")

    try:
        daily = get_daily_close_nav(date, fund)
        with col3:
            st.metric("Official Daily NAV", f"${float(daily['net_asset_value']):.8f}")
            st.caption(f"As of {daily['net_asset_value_date']}")
    except Exception as e:
        with col3:
            st.error(f"Error: {e}")
