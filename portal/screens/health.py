"""Health: agents reporting, AI calls, latency, cost, and live checks that nothing leaks."""
from __future__ import annotations

import streamlit as st

import ui

api = ui.api()
ui.header("Health")

devices = ui.call(api.devices)
stats = ui.call(api.pipeline_stats)
priv = ui.call(api.privacy_check)
health = ui.call(api.health)

calls = stats.get("llm_calls", {})
p50 = stats.get("p50_latency_ms", {})
online = sum(d.online for d in devices)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Laptops online", f"{online} / {len(devices)}")
c2.metric("AI calls", sum(calls.values()), help="Verify, classify and OCR calls to the model (cache hits excluded).")
c3.metric("Estimated AI cost", f"${stats.get('est_cost_usd', 0):.4f}",
          help="From token counts at the configured per-million-token prices.")
c4.metric("Privacy checks", f"{sum(c['ok'] for c in priv['checks'])} / {len(priv['checks'])} passing")

st.subheader("Nothing leaking")
rows = [[ui.status("Pass", "#0CCE6B") if c["ok"] else ui.status("Fail", "#E5484D"),
         ui.e(c["name"]), f'<span class="faint">{ui.e(c["detail"])}</span>'] for c in priv["checks"]]
ui.html_block(ui.table(["", "Check", "Detail"], rows))
st.caption("Run live against the server's database on every page load.")

left, right = st.columns(2, gap="large")
with left:
    st.subheader("AI calls")
    cache = stats.get("cache_hits", {})
    names = {"verify": "Verify uncertain items", "classify": "Classify documents", "ocr": "Read images (OCR)"}
    rows = [[ui.e(names.get(k, k)), str(calls.get(k, 0)), str(cache.get(k, 0)),
             f"{p50[k] / 1000:.1f}s" if p50.get(k) else "-"] for k in ("verify", "classify", "ocr")]
    ui.html_block(ui.table(["Call", "Calls", "Cache hits", "Median time"], rows, {1, 2, 3}))
    tok = stats.get("tokens", {})
    st.caption(f"{stats.get('llm_errors', 0)} failed calls, {stats.get('key_rotations', 0)} key rotations, "
               f"{tok.get('in', 0):,} tokens in and {tok.get('out', 0):,} out. Model calls stand in for the "
               "company's own LLM and OCR.")
with right:
    st.subheader("Who decided")
    dec = stats.get("findings_by_decided_by", {})
    total = sum(dec.values()) or 1
    names = {"rules": "Rules and checksums", "server": "AI on the server", "local_model": "AI on the laptop"}
    rows = [[ui.e(names.get(k, k)), str(v), f"{v / total:.0%}"] for k, v in dec.items()]
    ui.html_block(ui.table(["Source", "Items", "Share"], rows, {1, 2}))
    st.caption("Only items the rules cannot settle go to the AI, in batches of up to 15.")

st.subheader("Laptops")
rows = [[f'<span class="mono">{ui.e(d.device_id)}</span>',
         ui.status("Online", "#0CCE6B") if d.online else ui.status("Offline", "#A1A1A1"),
         ui.e(ui.ago(d.last_seen)), ui.e(d.agent_version or "-"), str(d.files_scanned)] for d in devices]
ui.html_block(ui.table(["Laptop", "Status", "Last seen", "Agent version", "Files scanned"], rows, {4})
              if rows else '<p class="faint">No laptop has checked in yet.</p>')
st.caption(f"Server contract {health.get('contract_version')}, policy {health.get('policy_version')}.")
