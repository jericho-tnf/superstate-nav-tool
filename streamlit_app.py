import streamlit as st
from datetime import datetime, timezone

from superstate_nav import get_nav_per_share_at, get_daily_close_nav
from superstate_onchain_nav import get_onchain_nav_per_share_at

FUND_IDS = {"USTB": 1, "USCC": 2}

st.set_page_config(page_title="Superstate NAV Lookup", page_icon="📈", layout="wide")

st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    header {visibility: hidden;}
    footer {visibility: hidden;}
    [data-testid="stToolbar"] {visibility: hidden;}
    [data-testid="stDecoration"] {visibility: hidden;}
    [data-testid="stStatusWidget"] {visibility: hidden;}

    .stApp { background-color: #0b0f19; }

    .header-row { display: flex; align-items: center; gap: 18px; margin-bottom: 24px; }
    .header-icon {
        width: 64px; height: 64px; border-radius: 16px;
        background: linear-gradient(135deg, #0891b2, #22d3ee);
        display: flex; align-items: center; justify-content: center; font-size: 28px; flex-shrink: 0;
    }
    .header-title { font-size: 34px; font-weight: 800; color: white; margin: 0; }
    .header-subtitle { color: #94a3b8; margin: 0; font-size: 15px; }

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
    .result-label { color: #cbd5e1; font-size: 15px; font-weight: 500; margin-bottom: 4px; }
    .result-price { color: white; font-size: 34px; font-weight: 800; margin: 4px 0; }
    .result-caption-warn { color: #f59e0b; font-size: 13px; }
    .result-caption-info { color: #94a3b8; font-size: 13px; }

    .stButton > button {
        background: linear-gradient(135deg, #0891b2, #22d3ee);
        color: white; border: none; border-radius: 10px;
        padding: 10px 24px; font-weight: 600; font-size: 16px; width: 100%;
    }
    .stButton > button:hover { opacity: 0.9; color: white; }
    </style>
""", unsafe_allow_html=True)

st.markdown("""
    <div class="header-row">
        <div class="header-icon">📈</div>
        <div>
            <p class="header-title">Superstate NAV/Share Lookup</p>
            <p class="header-subtitle">Query NAV per share at a specific UTC timestamp — off-chain (API) vs on-chain (oracle).</p>
        </div>
    </div>
""", unsafe_allow_html=True)

with st.container(border=True):
    st.markdown('<div class="section-label">📅 Lookup Parameters</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1.3])
    with c1:
        fund = st.selectbox("Fund", list(FUND_IDS.keys()))
    with c2:
        date = st.date_input("Date (UTC)")
    with c3:
        time = st.time_input("Time (UTC)", value=datetime.strptime("23:59:00", "%H:%M:%S").time())
    with c4:
        st.write("")
        st.write("")
        query_clicked = st.button("Query NAV →", use_container_width=True)

if query_clicked:
    dt = datetime.combine(date, time).replace(tzinfo=timezone.utc)
    st.markdown(f'🕐 NAV/share as of <span style="color:#22d3ee; font-weight:700;">{dt.isoformat()}</span>', unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)

    with col1:
        with st.container(border=True):
            try:
                api_result = get_nav_per_share_at(dt, fund)
                st.markdown('<div class="result-icon">☁️</div>', unsafe_allow_html=True)
                st.markdown('<div class="result-label">Off-chain API</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="result-price">${api_result["price"]:.6f}</div>', unsafe_allow_html=True)
                if api_result.get("snapped_to_nearest_available"):
                    st.markdown('<div class="result-ca
