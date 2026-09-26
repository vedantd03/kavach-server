"""Audit trail, with a CSV export as the evidence pack."""
from __future__ import annotations

import json
import time

import pandas as pd
import streamlit as st

import ui

EVENT_LABEL = {
    "scan.requested": "Scan requested", "scan.delivered": "Scan sent to device",
    "scan.accepted": "Scan accepted by device", "scan.rejected": "Scan rejected by device",
    "scan.completed": "Scan completed", "scan.failed": "Scan failed",
    "findings.recorded": "Findings recorded", "action.suggested": "Action suggested by policy",
    "action.approved": "Action approved", "action.rejected": "Action rejected",
    "action.done": "Action done", "action.failed": "Action failed",
}
GROUPS = {"Everything": None, "Scans": "scan.", "Findings": "findings.", "Actions": "action."}


def summarise(event: str, d: dict) -> str:
    if event.startswith("scan."):
        bits = [d.get("scan_id") or ""]
        if d.get("roots"):
            bits.append(", ".join(d["roots"]))
        if event in ("scan.completed", "scan.failed"):
            bits.append(f"{d.get('files_done', 0)} files, {d.get('findings', 0)} findings")
        if d.get("reason"):
            bits.append(f"reason {d['reason']}")
        return "; ".join(b for b in bits if b)
    if event == "findings.recorded":
        tiers = ", ".join(f"{v} {k}" for k, v in (d.get("by_tier") or {}).items()) or "no items"
        return f"{d.get('files', 0)} files, {d.get('items', 0)} items ({tiers}); {d.get('pending', 0)} pending"
    if event.startswith("action."):
        name, _ = ui.split_path(d.get("file_path", ""))
        return f"{d.get('action_type', '')} on {name} ({d.get('tier', '')})"
    return json.dumps(d)[:160]


ui.header("Audit trail", "Who did what, when. Export it as the evidence pack.")
group = st.segmented_control("Show", list(GROUPS), default="Everything", key="a_group")
rows = ui.call(ui.api().audit, event=GROUPS.get(group or "Everything"), limit=5000)

if not rows:
    st.info("No events yet. Scans, recorded findings and policy-suggested actions appear here as they happen.")
    st.stop()

table = pd.DataFrame([{
    "Time (UTC)": r["ts"].replace("T", " ").rstrip("Z"),
    "Actor": r["actor"],
    "Event": EVENT_LABEL.get(r["event"], r["event"]),
    "Summary": summarise(r["event"], r.get("details") or {}),
    "Action": r.get("action_id") or "",
    "Finding": r.get("finding_id") or "",
} for r in rows])

export = pd.DataFrame([{
    "audit_id": r["audit_id"], "ts_utc": r["ts"], "actor": r["actor"], "event": r["event"],
    "action_id": r.get("action_id") or "", "finding_id": r.get("finding_id") or "",
    "details_json": json.dumps(r.get("details") or {}, ensure_ascii=False),
} for r in rows])

c1, c2 = st.columns([3, 1])
c1.caption(f"{len(rows)} events, newest first. The export holds ids, tiers, counts and file paths; "
           "it never contains identifier values.")
c2.download_button("Download evidence pack (CSV)", export.to_csv(index=False).encode("utf-8"),
                   file_name=f"kavach-audit-{time.strftime('%Y%m%d-%H%M')}.csv", mime="text/csv",
                   type="primary", width="stretch")
st.dataframe(table, hide_index=True, width="stretch", height=560,
             column_config={"Summary": st.column_config.TextColumn(width="large")})
