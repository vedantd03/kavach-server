"""Detail views shared by pages: one file, one scan. Linked by URL query parameters so each view
has its own address (Findings?device=..&file=.., Scans?scan=..)."""
from __future__ import annotations

from collections import Counter, defaultdict
from urllib.parse import urlencode

import streamlit as st

import ui

DECIDED = {"rules": "Rules", "server": "AI (server)", "local_model": "AI (laptop)"}
ACTION_LABEL = {"quarantine": "Quarantine", "encrypt": "Encrypt", "masked_copy": "Masked copy",
                "suggest_delete": "Suggest delete"}


def file_href(device_id: str, file_path: str) -> str:
    return "findings?" + urlencode({"device": device_id, "file": file_path})


def scan_href(scan_id: str) -> str:
    return "scans?" + urlencode({"scan": scan_id})


def link(href: str, text: str, cls: str = "") -> str:
    return f'<a href="{ui.e(href)}" target="_self" class="lnk {cls}">{text}</a>'


def crumb(parent_href: str, parent: str, current: str) -> None:
    ui.html_block(f'<div class="crumb">{link(parent_href, ui.e(parent))} <span class="faint">/</span> '
                  f'<span>{ui.e(current)}</span></div>')


def where(location: str) -> str:
    """Human-readable location from 'sheet=1;row=12;column=4;header=Aadhaar' style strings."""
    if location == "document":
        return "Whole file"
    kv = dict(p.split("=", 1) for p in location.split(";") if "=" in p)
    if "sheet" in kv:
        head = f", column “{kv['header']}”" if kv.get("header") else f", column {kv.get('column')}"
        return f"Sheet {kv['sheet']}, row {kv.get('row')}{head}"
    parts = []
    if "page" in kv:
        parts.append(f"Page {kv['page']}")
    parts.append(f"text chunk {int(kv.get('chunk', 0)) + 1}, character {kv.get('char', '?')}")
    s = ", ".join(parts)
    return s[0].upper() + s[1:] + (" (read by OCR)" if kv.get("ocr") else "")


def type_mix(items) -> str:
    c = Counter(ui.TYPE_LABEL.get(f.pii_type or "", f.pii_type or "") for f in items)
    return ", ".join(f"{n} {t}" for t, n in c.most_common())


# --------------------------------------------------------------------------- file view
def render_file(device_id: str, file_path: str, back_href: str = "findings", back_label: str = "Findings") -> None:
    api = ui.api()
    rows = ui.call(api.findings, device_id=device_id, file=file_path, limit=1000)
    name, folder = ui.split_path(file_path)
    crumb(back_href, back_label, name)
    st.title(name)
    ui.html_block(f'<p class="sub"><span class="mono">{ui.e(folder)}</span> on '
                  f'<span class="mono">{ui.e(device_id)}</span></p>')
    if not rows:
        st.info("Nothing sensitive is recorded for this file. It may have been clean, or rescanned since.")
        return
    doc = next((f for f in rows if f.finding_kind == "document"), None)
    items = sorted((f for f in rows if f.finding_kind == "item"), key=lambda f: (-f.risk_score, f.pii_type or ""))
    top = doc or items[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Final tier", top.sensitivity_tier.capitalize())
    c2.metric("Risk", f"{max(f.risk_score for f in rows):.0f}", help=f"Band: {top.risk_band}")
    c3.metric("Items found", len(items))
    c4.metric("Decided by AI", sum(f.decided_by != "rules" for f in items),
              help="Items the rules could not settle and the AI judged from masked context.")

    ai = top.llm_suggested_tier
    agree = ("the same" if ai == top.sensitivity_tier else f"{ai}, but the code's tier stands") if ai else "no view"
    ui.html_block(
        f'<div class="callout"><div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">'
        f'{ui.badge(top.sensitivity_tier)}<span class="faint">{ui.e(top.doc_type.replace("_", " "))}, '
        f'{ui.e(ui.FOLDER_LABEL.get(top.folder_class, top.folder_class))}, {ui.e(top.file_type)}</span></div>'
        f'<div><b>Why this tier:</b> {ui.e(top.tier_reason)}</div>'
        f'<div><b>AI suggested:</b> {ui.e(agree)}. Policy {ui.e(top.policy_version)} sets the final tier.</div>'
        + (f'<div class="faint" style="margin-top:4px">{ui.e(doc.reason)}</div>' if doc else "") + "</div>")
    st.write("")

    st.subheader("What was found")
    if items:
        by_type = Counter(ui.TYPE_LABEL.get(f.pii_type or "", f.pii_type or "") for f in items)
        ui.html_block('<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">' + "".join(
            f'<span class="badge">{ui.e(t)} <b>{n}</b></span>' for t, n in by_type.most_common()) + "</div>")
        trs = [[
            ui.e(ui.TYPE_LABEL.get(f.pii_type or "", f.pii_type or "")),
            f'<span class="mono">{ui.e(f.masked_value or "")}</span>'
            + (' <span class="faint" style="font-size:12px">masked in file</span>' if f.masked_in_source else ""),
            ui.badge(f.sensitivity_tier),
            ui.e(where(f.location)),
            f"{f.confidence:.2f}",
            ui.e(DECIDED.get(f.decided_by, f.decided_by)),
            f'<span style="font-size:12.5px">{ui.e(f.reason)}</span>',
        ] for f in items]
        ui.html_block('<div style="overflow-x:auto">' + ui.table(
            ["Type", "Value", "Tier", "Where", "Confidence", "Decided by", "Why"], trs, {4}) + "</div>")
    else:
        st.caption("No individual identifiers; the tier comes from the document itself.")

    acts = [ev for ev in ui.call(api.audit, event="action.suggested", limit=5000)
            if (ev.get("details") or {}).get("file_path") == file_path
            and (ev.get("details") or {}).get("device_id") == device_id]
    if acts:
        st.subheader("Suggested actions")
        ui.html_block('<div style="display:flex;gap:6px;flex-wrap:wrap">' + "".join(
            f'<span class="badge">{ui.e(ACTION_LABEL.get(a["details"].get("action_type"), a["details"].get("action_type")))}</span>'
            for a in acts) + "</div>")
        st.caption("Awaiting approval. Approving arrives with Phase 1B.")


# --------------------------------------------------------------------------- scan view
STATUS_COLOR = {"queued": "#A1A1A1", "delivered": "#0070F3", "running": "#F5A524",
                "completed": "#0CCE6B", "failed": "#E5484D", "rejected": "#E5484D"}
STATUS_LABEL = {"queued": "Queued", "delivered": "Sent to laptop", "running": "Scanning",
                "completed": "Completed", "failed": "Failed", "rejected": "Rejected"}


def render_scan(scan_id: str) -> None:
    api = ui.api()
    s = ui.call(api.scan, scan_id)
    crumb("scans", "Scans", s.scan_id)
    st.title("Scan results")
    ui.html_block(
        f'<p class="sub" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
        f'{ui.status(STATUS_LABEL.get(s.status, s.status), STATUS_COLOR.get(s.status, "#A1A1A1"))}'
        f'<span class="mono">{ui.e(s.scan_id)}</span><span class="faint">on</span>'
        f'<span class="mono">{ui.e(s.device_id)}</span>'
        f'<span class="faint">started {ui.e(ui.ago(s.started_at or s.created_at))}'
        + (f", took {s.duration_ms / 1000:.0f}s" if s.duration_ms else "") + "</span></p>")

    findings = ui.call(api.findings, scan_id=scan_id, limit=1000)
    docs = {f.file_path: f for f in findings if f.finding_kind == "document"}
    items_by_file = defaultdict(list)
    for f in findings:
        if f.finding_kind == "item":
            items_by_file[f.file_path].append(f)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Files scanned", s.files_done, help=f"{s.files_discovered} discovered")
    c2.metric("Skipped or unreadable", s.files_skipped + s.files_unscannable + s.files_failed,
              help=f"{s.files_skipped} unchanged since last scan, {s.files_unscannable} unreadable, {s.files_failed} failed")
    c3.metric("Files with sensitive data", len(set(docs) | set(items_by_file)))
    c4.metric("Items found", sum(len(v) for v in items_by_file.values()))

    ui.html_block('<div class="faint" style="font-size:13px;margin:4px 0 14px">Folders: ' + ", ".join(
        f'<span class="mono">{ui.e(r)}</span>' for r in s.roots) + "</div>")
    if s.status == "rejected":
        st.error(f"The laptop rejected this scan: {s.reject_reason or 'no reason given'}.")

    st.subheader("Files")
    paths = sorted(set(docs) | set(items_by_file),
                   key=lambda p: -max([docs[p].risk_score] if p in docs else [f.risk_score for f in items_by_file[p]]))
    if not paths:
        st.caption("No sensitive data found in this scan yet." if s.status in ("running", "delivered")
                   else "No sensitive data found in this scan.")
        return
    rows = []
    for p in paths:
        d = docs.get(p)
        its = items_by_file.get(p, [])
        tier = d.sensitivity_tier if d else max((f.sensitivity_tier for f in its), key=lambda t: -ui.TIERS.index(t))
        risk = d.risk_score if d else max(f.risk_score for f in its)
        name, _ = ui.split_path(p)
        rows.append([
            link(file_href(s.device_id, p), ui.e(name)) + f'<div class="mono faint">{ui.e(ui.short_folder(p, 3))}</div>',
            ui.badge(tier), f"<b>{risk:.0f}</b>", str(len(its)),
            f'<span class="faint" style="font-size:12.5px">{ui.e(type_mix(its) or (d.doc_type.replace("_", " ") if d else ""))}</span>',
        ])
    ui.html_block('<div style="overflow-x:auto">' + ui.table(["File", "Tier", "Risk", "Items", "What"], rows, {2, 3}) + "</div>")
    st.caption("Only files with sensitive data are listed. Select a file to see every item found in it.")
