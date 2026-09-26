"""Shared look and helpers for the Kavach Console."""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Optional

import streamlit as st

from api_client import Api, ApiError

TIERS = ["restricted", "confidential", "internal", "public"]
TIER_COLOR = {"restricted": "#B3261E", "confidential": "#B86E00", "internal": "#2F6F73", "public": "#7C8799"}
TIER_TINT = {"restricted": "#FBEAE9", "confidential": "#FCF1E0", "internal": "#E6F1F1", "public": "#EEF0F4"}
TYPE_LABEL = {
    "AADHAAR": "Aadhaar", "PAN_INDIVIDUAL": "PAN (individual)", "PAN_BUSINESS": "PAN (business)",
    "GSTIN": "GSTIN", "UPI_ID": "UPI ID", "BANK_ACCOUNT": "Bank account", "MOBILE_IN": "Mobile",
    "EMAIL": "Email", "SECRET": "Secret / key",
}
FOLDER_LABEL = {"synced": "Cloud-synced", "shared": "Shared", "downloads": "Downloads",
                "desktop": "Desktop", "documents": "Documents", "other": "Other"}
REFRESH_SEC = float(os.environ.get("PORTAL_REFRESH_SEC", "3"))

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Mukta:wght@400;500;600;700&display=swap');
:root { --ink:#1F2A5C; --paper:#F6F7FB; --line:#D9DDEA; --muted:#5B6479; }
html, body, .stApp, .stMarkdown, p, li, label, input, textarea, select, button, h1, h2, h3,
[data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-testid="stCaptionContainer"] {
  font-family: 'Mukta', system-ui, sans-serif !important;
}
[data-testid="stIconMaterial"] { font-family: 'Material Symbols Rounded' !important; }
h1, h2, h3 { color: var(--ink); letter-spacing: -0.01em; }
h1 { font-weight: 700 !important; font-size: 2.1rem !important; margin-bottom: 0 !important; }
h3 { font-weight: 600 !important; font-size: 1.15rem !important; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; color: var(--ink); font-weight: 600; }
[data-testid="stMetricLabel"] p { color: var(--muted); font-size: 0.95rem; }
[data-testid="stMetric"] { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; }
.kv-sub { color: var(--muted); margin-top: -4px; font-size: 1rem; }
.kv-brand { display:flex; align-items:baseline; gap:10px; margin: 0 0 4px 0; }
.kv-brand .dev { font-size: 2.3rem; font-weight: 700; color: var(--ink); line-height: 1; }
.kv-brand .lat { font-size: 1.05rem; color: var(--muted); }
.stamp { display:inline-block; padding: 1px 9px 0; border: 1.5px solid; border-radius: 4px;
  font-weight: 600; font-size: 0.86rem; line-height: 1.5; white-space: nowrap; }
.stamp.big { transform: rotate(-2deg); font-size: 0.95rem; }
table.kv { width:100%; border-collapse: collapse; background:#fff; border:1px solid var(--line); border-radius:6px; }
table.kv th { text-align:left; color: var(--muted); font-weight:500; padding:8px 12px; border-bottom:1px solid var(--line); }
table.kv td { padding:9px 12px; border-bottom:1px solid #EDF0F6; vertical-align: middle; }
table.kv tr:last-child td { border-bottom: none; }
table.kv td.num { font-variant-numeric: tabular-nums; text-align:right; }
table.kv .path { color: var(--muted); font-size: 0.85rem; }
table.kv td.nowrap { white-space: nowrap; }
.dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
.note { background:#fff; border-left: 3px solid var(--ink); padding: 10px 14px; color:#2B3350; border-radius: 0 6px 6px 0; }
</style>
"""


def setup() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


@st.cache_resource
def api() -> Api:
    return Api()


def call(fn, *args, **kwargs):
    """Run an API call; on failure show a clear message and stop the page."""
    try:
        return fn(*args, **kwargs)
    except ApiError as e:
        if e.code == "SERVER_UNREACHABLE":
            st.error(f"The Kavach server at {api().base_url} is not responding. "
                     "Check that it is running and that PORTAL_API_URL points at it.")
        elif e.status == 401:
            st.error("The server rejected the console's admin token. Set ADMIN_TOKEN to the server's value.")
        else:
            st.error(f"The server returned {e.status} {e.code}: {e.message}")
        st.stop()


def stamp(tier: Optional[str], big: bool = False) -> str:
    if not tier:
        return '<span class="path">none</span>'
    c, t = TIER_COLOR.get(tier, "#7C8799"), TIER_TINT.get(tier, "#EEF0F4")
    return (f'<span class="stamp{" big" if big else ""}" style="color:{c};border-color:{c};background:{t}">'
            f'{html.escape(tier.capitalize())}</span>')


def split_path(path: str) -> tuple[str, str]:
    p = path.replace("\\", "/")
    return (p.rsplit("/", 1)[-1], p.rsplit("/", 1)[0] if "/" in p else "")


def short_folder(path: str, keep: int = 2) -> str:
    """Last `keep` folders of a file's directory, e.g. '.../files/Downloads'."""
    parts = [x for x in split_path(path)[1].split("/") if x]
    return ("…/" if len(parts) > keep else "") + "/".join(parts[-keep:])


def ago(ts: Optional[str]) -> str:
    if not ts:
        return "never"
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return ts
    s = int((datetime.now(timezone.utc) - dt).total_seconds())
    if s < 60:
        return f"{max(s, 0)} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return dt.strftime("%d %b %H:%M")


def header(title: str, sub: str) -> None:
    st.title(title)
    st.markdown(f'<p class="kv-sub">{html.escape(sub)}</p>', unsafe_allow_html=True)
