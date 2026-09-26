"""Findings: filterable, masked values only. Shows where the AI's suggested tier differs from
the tier the code set: the code's tier is final."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import ui

api = ui.api()
ui.header("Findings", "Every detected item, with masked values only.")

devices = ui.call(api.devices)
summary = ui.call(api.summary)

with st.container(border=True):
    c1, c2, c3, c4 = st.columns(4)
    device = c1.selectbox("Device", ["All devices"] + [d.device_id for d in devices])
    tier = c2.selectbox("Final tier", ["All tiers"] + [t.capitalize() for t in ui.TIERS])
    types = sorted(summary.get("by_type", {}).keys())
    ptype = c3.selectbox("Type", ["All types"] + types, format_func=lambda t: ui.TYPE_LABEL.get(t, t))
    folder = c4.selectbox("Folder", ["All folders"] + list(ui.FOLDER_LABEL),
                          format_func=lambda f: ui.FOLDER_LABEL.get(f, f))
    c5, c6 = st.columns([1, 3])
    view = c5.segmented_control("Show", ["Items", "Files"], default="Items",
                                help="Items are individual identifiers. Files are one row per file with its overall tier.")
    only_diff = c6.toggle("Only where the AI's suggestion differs from the final tier", value=False)

kind = "document" if view == "Files" else "item"
findings = ui.call(api.findings,
                   device_id=None if device == "All devices" else device,
                   tier=None if tier == "All tiers" else tier.lower(),
                   type=None if ptype == "All types" or kind == "document" else ptype,
                   folder=None if folder == "All folders" else folder,
                   kind=kind, limit=1000)

# The AI's view is per file, so the fair comparison is on file rows.
files = ui.call(api.findings, device_id=None if device == "All devices" else device, kind="document", limit=1000)
judged = [f for f in files if f.llm_suggested_tier]
differ = [f for f in judged if f.llm_suggested_tier != f.sensitivity_tier]
st.markdown(
    f"<div class='note'>The final tier is computed by code from policy "
    f"<b>{files[0].policy_version if files else '-'}</b>. The AI's suggested tier is stored alongside and never "
    f"overrides it. On <b>{len(differ)}</b> of <b>{len(judged)}</b> files the AI suggested a different tier.</div>",
    unsafe_allow_html=True)

if only_diff:
    findings = [f for f in findings if f.llm_suggested_tier and f.llm_suggested_tier != f.sensitivity_tier]

if not findings:
    st.info("No findings match these filters." if devices else
            "Nothing has been scanned yet. Findings appear here as soon as an agent sends files.")
    st.stop()

rows = []
for f in findings:
    name, path = ui.split_path(f.file_path)
    ai = f.llm_suggested_tier
    rows.append({
        "Final tier (code)": f.sensitivity_tier.capitalize(),
        "AI suggested": ai.capitalize() if ai else "-",
        "Match": "-" if not ai else ("same" if ai == f.sensitivity_tier else "differs"),
        "Type": ui.TYPE_LABEL.get(f.pii_type or "", f.doc_type.replace("_", " ")),
        "Masked value": f.masked_value or "",
        "File": name,
        "Folder": ui.FOLDER_LABEL.get(f.folder_class, f.folder_class),
        "Risk": f.risk_score,
        "Confidence": f.confidence,
        "Decided by": {"rules": "Rules", "server": "AI (server)", "local_model": "AI (laptop)"}[f.decided_by],
        "Device": f.device_id,
        "Why this tier": f.tier_reason,
        "Why detected": f.reason,
        "Location": f.location,
        "Path": path,
    })
df = pd.DataFrame(rows)
if kind == "document":
    df = df.drop(columns=["Masked value"])

st.caption(f"{len(df)} {'files' if kind == 'document' else 'items'}, highest risk first. Select a row for details.")
def _tier_css(v: str) -> str:
    t = str(v).lower()
    return f"color: {ui.TIER_COLOR[t]}; font-weight: 600" if t in ui.TIER_COLOR else ""


styled = (df.style.map(_tier_css, subset=["Final tier (code)", "AI suggested"])
          .map(lambda v: "color: #B3261E; font-weight: 600" if v == "differs" else "", subset=["Match"]))
event = st.dataframe(
    styled, hide_index=True, width="stretch", height=520, on_select="rerun", selection_mode="single-row",
    column_config={
        "Risk": st.column_config.ProgressColumn("Risk", min_value=0, max_value=100, format="%.0f"),
        "Confidence": st.column_config.NumberColumn("Confidence", format="%.2f"),
        "Why this tier": st.column_config.TextColumn(width="large"),
        "Why detected": st.column_config.TextColumn(width="large"),
    })

sel = event.selection.rows if event and event.selection else []
if sel:
    f = findings[sel[0]]
    with st.container(border=True):
        st.markdown(f"{ui.stamp(f.sensitivity_tier, big=True)} &nbsp; **{ui.TYPE_LABEL.get(f.pii_type or '', f.doc_type)}** "
                    f"{f.masked_value or ''}", unsafe_allow_html=True)
        a, b = st.columns(2)
        a.markdown(f"**Why this tier (code)**  \n{f.tier_reason}")
        a.markdown(f"**AI suggested tier**  \n{(f.llm_suggested_tier or 'none').capitalize()}")
        b.markdown(f"**Why detected**  \n{f.reason}")
        b.markdown(f"**Where**  \n`{f.file_path}`  \n{f.location}")
        st.caption(f"Finding {f.finding_id}, holder {f.holder}, category {f.category}, "
                   f"decided by {f.decided_by}, detected {f.detected_at}")
