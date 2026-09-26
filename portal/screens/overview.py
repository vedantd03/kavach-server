"""Overview: where sensitive data is, and what to fix first. Refreshes itself so it fills
while an agent scans."""
from __future__ import annotations

import time
from collections import Counter, defaultdict

import altair as alt
import pandas as pd
import streamlit as st

import ui
import views

ui.header("Overview", "Where sensitive data sits across your laptops, and what to fix first.")


@st.fragment(run_every=ui.REFRESH_SEC)
def live() -> None:
    api = ui.api()
    s = ui.call(api.summary)
    devices = ui.call(api.devices)
    if not devices:
        st.info("No laptop has reported yet. Start the agent on a laptop, or trigger a scan from the Scans page. "
                "This page fills in as files are scanned.")
        return
    files = ui.call(api.findings, kind="document", limit=1000)

    ftier = s.get("files_by_tier", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Laptops online", f"{s.get('devices_online', 0)} / {s.get('devices', 0)}",
              help="Checked in within the last 30 seconds.")
    c2.metric("Restricted files", ftier.get("restricted", 0),
              help="Bulk identifiers, secrets, KYC documents, payroll or board papers.")
    c3.metric("High-risk files", s.get("high_risk_files", 0), help="Risk score 70 or more.")
    c4.metric("Pending approvals", s.get("pending_approvals", 0),
              help="Actions the policy suggested that nobody has approved yet.")

    st.subheader("Laptops")
    per_dev: dict[str, Counter] = defaultdict(Counter)
    for f in files:
        per_dev[f.device_id][f.sensitivity_tier] += 1
    by_device = s.get("by_device", {})
    rows = []
    for d in devices:
        counts = per_dev.get(d.device_id, Counter())
        mix = ", ".join(f"{counts[t]} {t}" for t in ui.TIERS if counts.get(t)) or "none"
        rows.append([
            f'<span class="mono">{ui.e(d.device_id)}</span>',
            ui.status("Online", "#0CCE6B") if d.online else ui.status("Offline", "#A1A1A1"),
            ui.e(ui.ago(d.last_seen)),
            str(d.files_scanned),
            f'<div style="display:flex;gap:10px;align-items:center">{ui.tier_bar(dict(counts))}'
            f'<span class="faint" style="white-space:nowrap;font-size:12px">{ui.e(mix)}</span></div>',
            str(by_device.get(d.device_id, 0)),
        ])
    ui.html_block('<div style="overflow-x:auto">' + ui.table(
        ["Laptop", "Status", "Last seen", "Files scanned", "Sensitive files by tier", "Items"], rows, {3, 5}) + "</div>")

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Files by tier")
        df = pd.DataFrame([{"tier": t.capitalize(), "files": ftier.get(t, 0), "o": i} for i, t in enumerate(ui.TIERS)])
        st.altair_chart(alt.Chart(df).mark_bar(cornerRadiusEnd=3).encode(
            x=alt.X("files:Q", title=None, axis=alt.Axis(tickMinStep=1, grid=False)),
            y=alt.Y("tier:N", sort=alt.SortField("o"), title=None, axis=alt.Axis(ticks=False, domain=False)),
            color=alt.Color("tier:N", legend=None, scale=alt.Scale(
                domain=[t.capitalize() for t in ui.TIERS], range=[ui.TIER_COLOR[t] for t in ui.TIERS])),
            tooltip=["tier", "files"]).properties(height=150).configure_view(stroke=None), width="stretch")
    with right:
        st.subheader("Items by type")
        by_type = s.get("by_type", {})
        tdf = pd.DataFrame([{"type": ui.TYPE_LABEL.get(k, k), "items": v} for k, v in by_type.items()])
        if not tdf.empty:
            st.altair_chart(alt.Chart(tdf).mark_bar(color="#171717", cornerRadiusEnd=3).encode(
                x=alt.X("items:Q", title=None, axis=alt.Axis(grid=False)),
                y=alt.Y("type:N", sort="-x", title=None, axis=alt.Axis(ticks=False, domain=False)),
                tooltip=["type", "items"]).properties(height=max(150, 30 * len(tdf))).configure_view(stroke=None),
                width="stretch")
    st.subheader("Fix first")
    rows = []
    for f in s.get("top_risky_files", []):
        name, _ = ui.split_path(f["file_path"])
        rows.append([
            views.link(views.file_href(f["device_id"], f["file_path"]), ui.e(name))
            + f'<div class="mono faint">{ui.e(ui.short_folder(f["file_path"], 3))}</div>',
            ui.badge(f.get("sensitivity_tier")),
            f'<b>{f["max_risk_score"]:.0f}</b>',
            str(f["findings"]),
            f'<span class="mono">{ui.e(f["device_id"])}</span>',
        ])
    ui.html_block(f'<div style="overflow-x:auto">{ui.table(["File", "Tier", "Risk", "Items", "Laptop"], rows, {2, 3})}</div>'
                  if rows else '<p class="faint">No findings yet.</p>')

    st.caption(f"Updated {time.strftime('%H:%M:%S')}. Refreshes every {ui.REFRESH_SEC:g} seconds.")


live()
