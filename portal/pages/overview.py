"""Overview: devices reporting, restricted and high-risk files, pending approvals, top 10 files.
Refreshes itself every PORTAL_REFRESH_SEC seconds so it fills while the agent scans."""
from __future__ import annotations

import html
import time

import altair as alt
import pandas as pd
import streamlit as st

import ui

ui.header("Overview", "Where sensitive data sits across the fleet right now.")


@st.fragment(run_every=ui.REFRESH_SEC)
def live() -> None:
    api = ui.api()
    s = ui.call(api.summary)
    devices = ui.call(api.devices)
    st.caption(f"Updated {time.strftime('%H:%M:%S')}, refreshing every {ui.REFRESH_SEC:g} seconds")

    if not devices:
        st.info("No device has reported yet. Start the agent on a laptop "
                "(`python -m agent.main scan <folder>`) and this page fills in as files are scanned.")
        return

    ftier = s.get("files_by_tier", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Devices reporting", f"{s.get('devices_online', 0)} of {s.get('devices', 0)}",
              help="Online means the device polled or sent a heartbeat in the last 30 seconds.")
    c2.metric("Restricted files", ftier.get("restricted", 0),
              help="Files whose final tier is restricted: bulk identifiers, secrets, KYC or board papers.")
    c3.metric("High-risk files", s.get("high_risk_files", 0),
              help="Files with a risk score of 70 or more (tier weight x folder exposure x volume).")
    c4.metric("Pending approvals", s.get("pending_approvals", 0),
              help="Actions the policy suggested (quarantine, encrypt, masked copy) that nobody has approved yet.")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Top 10 risky files")
        top = s.get("top_risky_files", [])
        if not top:
            st.caption("No findings yet.")
        else:
            rows = []
            for f in top:
                name, folder = ui.split_path(f["file_path"])[0], ui.short_folder(f["file_path"])
                rows.append(
                    f"<tr><td>{ui.stamp(f.get('sensitivity_tier'), big=True)}</td>"
                    f"<td class='num'>{f['max_risk_score']:.0f}</td>"
                    f"<td>{html.escape(name)}<div class='path'>{html.escape(folder)}</div></td>"
                    f"<td class='nowrap'>{html.escape(f['device_id'])}</td>"
                    f"<td class='num'>{f['findings']}</td></tr>")
            st.markdown("<table class='kv'><thead><tr><th>Tier</th><th style='text-align:right'>Risk</th>"
                        "<th>File</th><th>Device</th><th style='text-align:right'>Findings</th></tr></thead>"
                        f"<tbody>{''.join(rows)}</tbody></table>", unsafe_allow_html=True)

    with right:
        st.subheader("Files by tier")
        df = pd.DataFrame([{"tier": t.capitalize(), "files": ftier.get(t, 0), "order": i}
                           for i, t in enumerate(ui.TIERS) if t != "public"])
        chart = alt.Chart(df).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("files:Q", title=None, axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("tier:N", sort=alt.SortField("order"), title=None),
            color=alt.Color("tier:N", scale=alt.Scale(
                domain=[t.capitalize() for t in ui.TIERS], range=[ui.TIER_COLOR[t] for t in ui.TIERS]), legend=None),
            tooltip=["tier", "files"]).properties(height=130)
        st.altair_chart(chart, width="stretch")

        st.subheader("Devices")
        rows = []
        for d in devices:
            color = "#2F6F73" if d.online else "#B0B6C4"
            rows.append(f"<tr><td class='nowrap'><span class='dot' style='background:{color}'></span>{html.escape(d.device_id)}</td>"
                        f"<td>{'online' if d.online else 'offline'}</td><td>{ui.ago(d.last_seen)}</td>"
                        f"<td class='num'>{d.files_scanned}</td></tr>")
        st.markdown("<table class='kv'><thead><tr><th>Device</th><th>Status</th><th>Last seen</th>"
                    "<th style='text-align:right'>Files</th></tr></thead>"
                    f"<tbody>{''.join(rows)}</tbody></table>", unsafe_allow_html=True)

        by_type = s.get("by_type", {})
        if by_type:
            st.subheader("Findings by type")
            tdf = pd.DataFrame([{"type": ui.TYPE_LABEL.get(k, k), "findings": v} for k, v in by_type.items()])
            st.altair_chart(alt.Chart(tdf).mark_bar(color="#1F2A5C", cornerRadiusEnd=3).encode(
                x=alt.X("findings:Q", title=None), y=alt.Y("type:N", sort="-x", title=None),
                tooltip=["type", "findings"]).properties(height=28 * len(tdf) + 10), width="stretch")


live()
