"""Shared look and helpers for the Kavach Console.

Look: monochrome, hairline borders, Geist (set in .streamlit/config.toml). Colour is reserved
for meaning: tier, status, and nothing else.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Optional

import streamlit as st

from api_client import Api, ApiError

TIERS = ["restricted", "confidential", "internal", "public"]
TIER_COLOR = {"restricted": "#E5484D", "confidential": "#F5A524", "internal": "#0070F3", "public": "#A1A1A1"}
TIER_TEXT = {"restricted": "#C9252D", "confidential": "#A35200", "internal": "#0060D1", "public": "#6F6F6F"}
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
:root { --fg:#171717; --muted:#666; --faint:#8F8F8F; --line:#EAEAEA; --subtle:#FAFAFA; }
.block-container { padding-top: 2.2rem; max-width: 1240px; }
h1 { letter-spacing: -0.03em; }
h2, h3 { letter-spacing: -0.01em; }
[data-testid="stMetric"] { border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; background:#fff; }
[data-testid="stMetricLabel"] p { color: var(--muted); font-size: 13px; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
.sub { color: var(--muted); margin: -6px 0 18px; font-size: 14px; }
.badge { display:inline-flex; align-items:center; gap:6px; padding: 1px 8px; border:1px solid var(--line);
  border-radius: 999px; font-size: 12.5px; color: var(--fg); background:#fff; white-space:nowrap; line-height:20px; }
.badge i { width:7px; height:7px; border-radius:50%; display:inline-block; }
.mono { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 12.5px; }
.faint { color: var(--faint); }
table.t { width:100%; border-collapse: separate; border-spacing:0; border:1px solid var(--line); border-radius:8px;
  overflow:hidden; font-size: 13.5px; background:#fff; }
table.t th { text-align:left; font-weight:500; color: var(--muted); background: var(--subtle); padding: 9px 12px;
  border-bottom:1px solid var(--line); font-size: 12.5px; }
table.t td { padding: 10px 12px; border-bottom:1px solid var(--line); vertical-align: middle; }
table.t tr:last-child td { border-bottom: none; }
table.t th, table.t td { border-left: none !important; border-right: none !important; border-top: none; }
table.t td.n, table.t th.n { text-align:right; font-variant-numeric: tabular-nums; }
table.t td.nw { white-space: nowrap; }
.callout { border:1px solid var(--line); border-radius:8px; padding: 12px 14px; background: var(--subtle);
  color:#333; font-size:13.5px; }
.bar { display:flex; height:8px; border-radius:4px; overflow:hidden; background:#F2F2F2; min-width:120px; }
.bar span { display:block; height:100%; }
a.lnk { color: var(--fg) !important; text-decoration: none; border-bottom: 1px solid #D4D4D4; }
a.lnk:hover { border-bottom-color: var(--fg); }
a.lnk:focus-visible { outline: 2px solid #0070F3; outline-offset: 2px; border-radius: 2px; }
.crumb { font-size: 13px; color: var(--muted); margin-bottom: 4px; }
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


def e(s: object) -> str:
    return html.escape(str(s))


def badge(tier: Optional[str]) -> str:
    if not tier:
        return '<span class="faint">none</span>'
    return f'<span class="badge"><i style="background:{TIER_COLOR.get(tier, "#A1A1A1")}"></i>{e(tier.capitalize())}</span>'


def status(label: str, color: str) -> str:
    return f'<span class="badge"><i style="background:{color}"></i>{e(label)}</span>'


def tier_bar(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    if not total:
        return '<div class="bar"></div>'
    parts = "".join(f'<span style="width:{counts.get(t, 0) / total * 100:.2f}%;background:{TIER_COLOR[t]}" '
                    f'title="{counts.get(t, 0)} {t}"></span>' for t in TIERS if counts.get(t))
    return f'<div class="bar">{parts}</div>'


def table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    th = "".join(f'<th class="{"n" if i in numeric else ""}">{e(h)}</th>' for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td class="{"n" if i in numeric else ""}">{c}</td>'
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<table class="t"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


def split_path(path: str) -> tuple[str, str]:
    p = path.replace("\\", "/")
    return (p.rsplit("/", 1)[-1], p.rsplit("/", 1)[0] if "/" in p else "")


def short_folder(path: str, keep: int = 2) -> str:
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
        return f"{max(s, 0)}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return dt.strftime("%d %b %H:%M")


def header(title: str, sub: str) -> None:
    st.title(title)
    st.markdown(f'<p class="sub">{e(sub)}</p>', unsafe_allow_html=True)


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)
