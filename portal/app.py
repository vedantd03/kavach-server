"""Kavach Console (Phase 3). Reads the server only over HTTP.

    streamlit run portal/app.py

Env: PORTAL_API_URL, ADMIN_TOKEN, PORTAL_PASSWORD (optional sign-in), PORTAL_REFRESH_SEC.
"""
from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "detect_core"))

try:
    from dotenv import load_dotenv
    load_dotenv(HERE.parent / ".env")
except ImportError:
    pass

import streamlit as st  # noqa: E402

import ui  # noqa: E402

st.set_page_config(page_title="Kavach Console", page_icon=str(HERE / "assets" / "logo.svg"),
                   layout="wide", initial_sidebar_state="collapsed")
ui.setup()
st.logo(str(HERE / "assets" / "logo.svg"), size="large")


def _signed_in() -> bool:
    password = os.environ.get("PORTAL_PASSWORD", "")
    if not password or st.session_state.get("signed_in"):
        return True
    st.title("Kavach Console")
    with st.form("sign-in"):
        entered = st.text_input("Console password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            if hmac.compare_digest(entered, password):
                st.session_state["signed_in"] = True
                st.rerun()
            st.error("That password is not correct.")
    return False


if not _signed_in():
    st.stop()

P = HERE / "screens"  # not "pages/": Streamlit would auto-discover that folder and bypass st.navigation
nav = st.navigation([
    st.Page(str(P / "overview.py"), title="Overview", default=True),
    st.Page(str(P / "findings.py"), title="Findings"),
    st.Page(str(P / "scans.py"), title="Scans"),
    st.Page(str(P / "actions.py"), title="Actions"),
    st.Page(str(P / "audit.py"), title="Audit"),
    st.Page(str(P / "accuracy.py"), title="Accuracy"),
    st.Page(str(P / "health.py"), title="Health"),
], position="top")
nav.run()
