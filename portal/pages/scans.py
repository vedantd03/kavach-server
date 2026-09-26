"""Scans: an admin starts a scan of chosen folders on a laptop and watches it run."""
from __future__ import annotations

import time

import streamlit as st

import ui
from api_client import ApiError
from detect_core.contracts import DEFAULT_EXCLUDE_DIRS, DEFAULT_INCLUDE_TYPES, CreateScanRequest

STATUS_COLOR = {"queued": "#A1A1A1", "delivered": "#0070F3", "running": "#F5A524",
                "completed": "#0CCE6B", "failed": "#E5484D", "rejected": "#E5484D"}
STATUS_LABEL = {"queued": "Queued", "delivered": "Sent to laptop", "running": "Scanning",
                "completed": "Completed", "failed": "Failed", "rejected": "Rejected"}
REJECT_LABEL = {"PATH_NOT_FOUND": "none of the folders exist on the laptop",
                "UNSUPPORTED_COMMAND": "the agent does not support this command"}


def clean_roots(raw: str) -> list[str]:
    """One folder per line. Strips the quotes Windows 'Copy as path' adds; keeps backslashes."""
    out = []
    for line in raw.splitlines():
        p = line.strip().strip('"').strip("'").strip()
        if p and p not in out:
            out.append(p)
    return out


api = ui.api()
ui.header("Scans", "Start a scan on a laptop and follow it as it runs.")
devices = ui.call(api.devices)

with st.container(border=True):
    st.markdown("**Start a scan**")
    if not devices:
        st.caption("No laptop has checked in yet. A laptop appears here after its agent polls the server once.")
    else:
        with st.form("start-scan", border=False):
            c1, c2 = st.columns([1, 2])
            labels = {d.device_id: f"{d.device_id}  ({'online' if d.online else 'offline, last seen ' + ui.ago(d.last_seen)})"
                      for d in devices}
            device = c1.selectbox("Laptop", list(labels), format_func=labels.get)
            roots_raw = c2.text_area(
                "Folders to scan", height=96,
                placeholder="One folder per line, for example\nC:\\Users\\priya\\Documents\nC:\\Users\\priya\\Downloads",
                help="Paths as they appear on the laptop. Windows and macOS paths both work; "
                     "quotes from Explorer's 'Copy as path' are removed.")
            with st.expander("Options"):
                o1, o2 = st.columns(2)
                force = o1.toggle("Rescan unchanged files", value=False,
                                  help="Off: files already scanned with the same content are skipped.")
                max_mb = o2.number_input("Skip files larger than (MB)", min_value=1, max_value=500, value=50)
                types = st.multiselect("File types", DEFAULT_INCLUDE_TYPES, default=DEFAULT_INCLUDE_TYPES)
            submitted = st.form_submit_button("Start scan", type="primary")
        if submitted:
            roots = clean_roots(roots_raw)
            if not roots:
                st.error("Add at least one folder to scan.")
            elif not types:
                st.error("Choose at least one file type.")
            else:
                try:
                    res = api.start_scan(CreateScanRequest(
                        device_id=device, roots=roots, force=force, max_file_mb=int(max_mb),
                        include_types=types, exclude_dirs=list(DEFAULT_EXCLUDE_DIRS)))
                    st.success(f"Scan {res.scan_id} queued for {device}. "
                               "It starts when the laptop next checks in, usually within a few seconds.")
                except ApiError as e:
                    msg = (f"{device} has never checked in with the server." if e.code == "DEVICE_UNKNOWN"
                           else f"{e.code}: {e.message}")
                    st.error(f"The scan was not started. {msg}")


@st.fragment(run_every=ui.REFRESH_SEC)
def recent() -> None:
    st.subheader("Recent scans")
    scans = ui.call(api.scans, limit=25)
    if not scans:
        st.caption("No scans yet.")
        return
    rows = []
    for s in scans:
        seen = s.files_done + s.files_unscannable + s.files_skipped + s.files_failed
        pct = (seen / s.files_discovered * 100) if s.files_discovered else (100 if s.status == "completed" else 0)
        progress = (f'<div style="display:flex;align-items:center;gap:8px"><div class="bar" style="min-width:90px">'
                    f'<span style="width:{pct:.0f}%;background:#171717"></span></div>'
                    f'<span class="faint" style="font-size:12px;white-space:nowrap">{seen}/{s.files_discovered or "?"} files</span></div>')
        note = ""
        if s.status == "rejected":
            note = f'<div class="faint" style="font-size:12px">{ui.e(REJECT_LABEL.get(s.reject_reason or "", s.reject_reason or ""))}</div>'
        elif s.error:
            note = f'<div class="faint" style="font-size:12px">{ui.e(s.error)}</div>'
        tiers = ", ".join(f"{v} {k}" for k, v in s.findings_by_tier.items()) or "-"
        dur = f"{s.duration_ms / 1000:.0f}s" if s.duration_ms else "-"
        rows.append([
            f'<span class="mono">{ui.e(s.scan_id)}</span><div class="faint" style="font-size:12px">{ui.e(ui.ago(s.created_at))}</div>',
            f'<span class="mono">{ui.e(s.device_id)}</span>',
            "<br>".join(f'<span class="mono">{ui.e(r)}</span>' for r in s.roots),
            ui.status(STATUS_LABEL.get(s.status, s.status), STATUS_COLOR.get(s.status, "#A1A1A1")) + note,
            progress,
            f'{s.findings}<div class="faint" style="font-size:12px">{ui.e(tiers)}</div>',
            dur,
        ])
    ui.html_block('<div style="overflow-x:auto">' + ui.table(
        ["Scan", "Laptop", "Folders", "Status", "Progress", "Items", "Took"], rows, {6}) + "</div>")
    st.caption(f"Updated {time.strftime('%H:%M:%S')}.")


recent()
