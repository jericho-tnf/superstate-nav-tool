import streamlit as st
from datetime import datetime, timezone

from superstate_nav import get_nav_per_share_at, get_daily_close_nav
from superstate_onchain_nav import get_onchain_nav_per_share_at

FUND_IDS = {"USTB": 1, "USCC": 2}

st.set_page_config(page_title="Superstate NAV Lookup", page_icon="📈")
st.title("Superstate NAV/Share Lookup")
st.caption("Query NAV per share at a specific UTC timestamp — off-chain (API) vs on-chain (oracle).")

fund = st.selectbox("Fund", list(FUND_IDS.keys()))
col1, col2 = st.columns(2)
with col1:
    date = st.date_input("Date")
with col2:
    time = st.time_input("Time (UTC)", value=datetime.strptime("23:59:00", "%H:%M:%S").time())

if st.button("Query NAV"):
    dt = datetime.combine(date, time).replace(tzinfo=timezone.utc)

    st.write(f"Querying NAV/share as of **{dt.isoformat()}**")

    try:
        api_result = get_nav_per_share_at(dt, fund)
        st.write("Raw off-chain API result:", api_result)
    except Exception as e:
        st.error(f"Off-chain API error: {e}")

    if fund == "USTB":
        try:
            onchain_result = get_onchain_nav_per_share_at(dt)
            st.write("Raw on-chain oracle result:", onchain_result)
        except Exception as e:
            st.error(f"On-chain oracle error: {e}")
    else:
        st.info("On-chain oracle lookup currently only covers USTB.")

    try:
        daily = get_daily_close_nav(date, fund)
        st.write("Raw daily NAV result:", daily)
    except Exception as e:
        st.error(f"Daily NAV error: {e}")
