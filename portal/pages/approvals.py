"""Approvals: arrives with Phase 1B (the action endpoints are not built yet)."""
from __future__ import annotations

import streamlit as st

import ui

ui.header("Approvals", "Approve or reject the actions the policy suggests.")
s = ui.call(ui.api().summary)

st.markdown(
    f"<div class='note'>The policy has suggested <b>{s.get('pending_approvals', 0)}</b> actions so far "
    "(quarantine or encrypt for restricted files, a masked copy for confidential ones). "
    "Approving them from here arrives with Phase 1B, together with the agent running approved actions "
    "and reporting each one as done.</div>", unsafe_allow_html=True)
st.write("")
st.markdown("Until then, every suggested action is already recorded in the **Audit trail** with the file, "
            "tier and policy version that produced it.")
