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

st.set_page_config(page_title="Kavach Console", page_icon="🛡️", layout="wide",
                   initial_sidebar_state="expanded")
ui.setup()


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

pages = [
    st.Page(str(HERE / "pages" / "overview.py"), title="Overview", default=True),
    st.Page(str(HERE / "pages" / "findings.py"), title="Findings"),
    st.Page(str(HERE / "pages" / "approvals.py"), title="Approvals"),
    st.Page(str(HERE / "pages" / "audit.py"), title="Audit trail"),
    st.Page(str(HERE / "pages" / "accuracy.py"), title="Accuracy"),
]
nav = st.navigation(pages, position="hidden")

with st.sidebar:
    st.markdown('<div class="kv-brand"><span class="dev">कवच</span><span class="lat">Kavach Console</span></div>',
                unsafe_allow_html=True)
    st.caption("Personal and confidential data on company laptops")
    st.write("")
    for page in pages:
        st.page_link(page)
    st.divider()
    try:
        h = ui.api().health()
        st.caption(f"Server online, policy {h.get('policy_version')}")
    except Exception:
        st.caption(f"Server not reachable at {ui.api().base_url}")

nav.run()
