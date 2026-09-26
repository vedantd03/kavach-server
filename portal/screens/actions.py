"""Actions: what the policy suggested doing about each file. Approve / reject / done arrive
with Phase 1B; until then every suggestion is listed with the tier and policy behind it."""
from __future__ import annotations

from collections import defaultdict

import streamlit as st

import ui
import views

ACTION_LABEL = {"quarantine": "Quarantine", "encrypt": "Encrypt", "masked_copy": "Masked copy",
                "suggest_delete": "Suggest delete"}
STATE = {"action.suggested": ("Awaiting approval", "#F5A524"), "action.approved": ("Approved", "#0070F3"),
         "action.rejected": ("Rejected", "#A1A1A1"), "action.done": ("Done", "#0CCE6B"),
         "action.failed": ("Failed", "#E5484D")}

ui.header("Actions", "What to do about each sensitive file, who approved it, and whether it is done.")
events = ui.call(ui.api().audit, event="action.", limit=5000)

# Latest state per action (events are newest first).
latest: dict[str, dict] = {}
first: dict[str, dict] = {}
history: dict[str, list] = defaultdict(list)
for ev in events:
    aid = ev.get("action_id")
    if not aid:
        continue
    latest.setdefault(aid, ev)
    first[aid] = ev if ev["event"] == "action.suggested" else first.get(aid, ev)
    history[aid].append(ev)

counts = defaultdict(int)
for ev in latest.values():
    counts[ev["event"]] += 1
c1, c2, c3, c4 = st.columns(4)
c1.metric("Awaiting approval", counts["action.suggested"])
c2.metric("Approved", counts["action.approved"])
c3.metric("Done", counts["action.done"])
c4.metric("Rejected or failed", counts["action.rejected"] + counts["action.failed"])

ui.html_block('<div class="callout">Approving and rejecting from the console, and the agent carrying out '
              'approved actions, arrive with Phase 1B. Every suggestion below already records the file, the tier '
              'and the policy version that produced it, and appears in the Audit trail.</div>')
st.write("")

if not latest:
    st.info("No actions yet. The policy suggests actions as soon as a scan finds restricted or confidential files.")
    st.stop()

tier_rank = {t: i for i, t in enumerate(ui.TIERS)}
items = sorted(latest.items(), key=lambda kv: (tier_rank.get(first[kv[0]]["details"].get("tier"), 9), kv[1]["ts"]))
rows = []
for aid, ev in items:
    d = first[aid].get("details") or {}
    name, _ = ui.split_path(d.get("file_path", ""))
    label, color = STATE.get(ev["event"], (ev["event"], "#A1A1A1"))
    who = f'<div class="faint" style="font-size:12px">by {ui.e(ev["actor"])}</div>' if ev["event"] != "action.suggested" else ""
    rows.append([
        views.link(views.file_href(d.get("device_id", ""), d.get("file_path", "")), ui.e(name))
        + f'<div class="mono faint">{ui.e(ui.short_folder(d.get("file_path", ""), 3))}</div>',
        f'<span class="mono">{ui.e(d.get("device_id", ""))}</span>',
        ui.badge(d.get("tier")),
        ui.e(ACTION_LABEL.get(d.get("action_type", ""), d.get("action_type", ""))),
        ui.status(label, color) + who,
        ui.e(ev["ts"].replace("T", " ").rstrip("Z")),
    ])
ui.html_block('<div style="overflow-x:auto">' + ui.table(
    ["File", "Laptop", "Tier", "Action", "Status", "Last change (UTC)"], rows) + "</div>")
