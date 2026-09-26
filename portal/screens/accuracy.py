"""Accuracy: the pipeline against rules alone, as charts."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

import ui
from api_client import ApiError

# Reference categorical slots 1-3 (validated: lightness, chroma, CVD, normal-vision all pass).
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID = "#171717", "#666666", "#EAEAEA"
PIPELINE, BASELINE = "Rules + context + AI", "Rules alone"


def base(chart: alt.Chart) -> alt.Chart:
    return (chart.configure_view(stroke=None)
            .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=GRID, domainColor=GRID, tickColor=GRID,
                            labelFont="Geist", titleFont="Geist", labelFontSize=12, titleFontSize=12,
                            titleFontWeight="normal", labelLimit=240)
            .configure_scale(bandPaddingInner=0.35)
            .configure_legend(labelColor=INK, labelFont="Geist", labelFontSize=12, orient="top", title=None,
                              symbolType="square", symbolSize=90)
            .configure_text(font="Geist"))


ui.header("Accuracy")

try:
    r = ui.api().eval()
except ApiError as e:
    if e.code == "EVAL_NOT_RUN":
        st.info("No evaluation has been run yet. Run `python eval/run_eval.py` on the server; "
                "results appear here as soon as eval/results.json exists.")
        st.stop()
    st.error(f"The server returned {e.status} {e.code}: {e.message}")
    st.stop()

b, p, imp, corpus = r["modes"]["rules_only"], r["modes"]["server"], r["impact"], r["corpus"]
bo, po = b["overall"], p["overall"]
hn = p["hard_negatives"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("False alarms avoided", imp["false_positives_avoided"])
c2.metric("Hours saved per 1,000 files", f"{imp['triage_hours_saved_per_1000_files']:g}",
          help=f"At {r['minutes_per_alert']:g} analyst minutes per false alarm.")
c3.metric("Precision", f"{po['precision']:.1%}", delta=f"{(po['precision'] - bo['precision']) * 100:+.1f} pts vs rules")
c4.metric("Look-alikes ignored", f"{hn['total'] - hn['flagged']} of {hn['total']}",
          help="Invoice, order, UTR and tracking numbers that pass the same checksums as real identifiers.")

# --------------------------------------------------------------------------- quality rates
st.subheader("Rules alone versus the full pipeline")
rates = [("Precision", bo["precision"], po["precision"]),
         ("Recall", bo["recall"], po["recall"]),
         ("F1", bo["f1"], po["f1"]),
         ("Look-alikes ignored", b["hard_negatives"]["rejection_rate"], hn["rejection_rate"]),
         ("File tier exactly right", b["tier"]["exact"] / b["tier"]["files"], p["tier"]["exact"] / p["tier"]["files"])]
rows = [[ui.e(m), f"{vb:.1%}", f"<b>{vp:.1%}</b>"] for m, vb, vp in rates]
rows.append(["False alarms", str(bo["fp"]), f"<b>{po['fp']}</b>"])
ui.html_block(ui.table(["Measure", BASELINE, PIPELINE], rows, {1, 2}))
st.write("")

# --------------------------------------------------------------------------- mistakes
m1, m2 = st.columns(2, gap="large")
with m1:
    st.subheader("False alarms")
    fa = pd.DataFrame([{"mode": BASELINE, "n": bo["fp"]}, {"mode": PIPELINE, "n": po["fp"]}])
    ch = alt.Chart(fa).encode(
        y=alt.Y("mode:N", sort=[BASELINE, PIPELINE], title=None, axis=alt.Axis(ticks=False, domain=False, labelPadding=8)),
        x=alt.X("n:Q", title="Items wrongly flagged", axis=alt.Axis(tickMinStep=1)))
    st.altair_chart(base((ch.mark_bar(cornerRadiusEnd=4).encode(
        color=alt.Color("mode:N", legend=None, scale=alt.Scale(domain=[BASELINE, PIPELINE], range=[ORANGE, BLUE])),
        tooltip=[alt.Tooltip("mode:N", title="Mode"), alt.Tooltip("n:Q", title="False alarms")])
        + ch.mark_text(align="left", dx=5, fontSize=12, color=INK).encode(text="n:Q")).properties(height=110)),
        width="stretch")
with m2:
    st.subheader("Files given too low a tier")
    ul = pd.DataFrame([{"mode": BASELINE, "v": b["tier"]["under_labelling_rate"], "n": b["tier"]["under_labelled"]},
                       {"mode": PIPELINE, "v": p["tier"]["under_labelling_rate"], "n": p["tier"]["under_labelled"]}])
    ch = alt.Chart(ul).encode(
        y=alt.Y("mode:N", sort=[BASELINE, PIPELINE], title=None, axis=alt.Axis(ticks=False, domain=False, labelPadding=8)),
        x=alt.X("v:Q", title=f"Share of {p['tier']['files']} files", axis=alt.Axis(format="%"),
                scale=alt.Scale(domainMin=0, nice=True)))
    st.altair_chart(base((ch.mark_bar(cornerRadiusEnd=4).encode(
        color=alt.Color("mode:N", legend=None, scale=alt.Scale(domain=[BASELINE, PIPELINE], range=[ORANGE, BLUE])),
        tooltip=[alt.Tooltip("mode:N", title="Mode"), alt.Tooltip("n:Q", title="Files"),
                 alt.Tooltip("v:Q", title="Share", format=".1%")])
        + ch.mark_text(align="left", dx=5, fontSize=12, color=INK).encode(
            text=alt.Text("v:Q", format=".1%"))).properties(height=110)), width="stretch")

# --------------------------------------------------------------------------- per type
st.subheader("By identifier type")
rows = []
for t, m in p["per_type"].items():
    label = ui.TYPE_LABEL.get(t, t)
    for part, n, o in (("Found", m["tp"], 0), ("False alarms", m["fp"], 1), ("Missed", m["fn"], 2)):
        if n:
            rows.append({"type": label, "part": part, "n": n, "o": o, "total": m["tp"] + m["fn"]})
pt = pd.DataFrame(rows)
st.altair_chart(base(alt.Chart(pt).mark_bar(stroke="#FFFFFF", strokeWidth=2).encode(
    y=alt.Y("type:N", sort=alt.EncodingSortField("total", order="descending"), title=None,
            axis=alt.Axis(ticks=False, domain=False, labelPadding=8)),
    x=alt.X("n:Q", title="Items", stack="zero"),
    order=alt.Order("o:Q"),
    color=alt.Color("part:N", scale=alt.Scale(domain=["Found", "False alarms", "Missed"], range=[BLUE, ORANGE, AQUA])),
    tooltip=[alt.Tooltip("type:N", title="Type"), alt.Tooltip("part:N", title=" "), alt.Tooltip("n:Q", title="Items")],
).properties(height=max(180, 34 * pt["type"].nunique()))), width="stretch")

# --------------------------------------------------------------------------- tiers + decisions
left, right = st.columns([3, 2], gap="large")
with left:
    st.subheader("File tiers: expected versus given")
    conf = p["tier"]["confusion"]
    cm = pd.DataFrame([{"expected": e.capitalize(), "given": g.capitalize(), "n": conf.get(e, {}).get(g, 0),
                        "ei": i, "gi": j}
                       for i, e in enumerate(ui.TIERS) for j, g in enumerate(ui.TIERS)])
    tiers_c = [t.capitalize() for t in ui.TIERS]
    grid = alt.Chart(cm).encode(
        x=alt.X("given:N", sort=tiers_c, title="Kavach gave", axis=alt.Axis(orient="top", ticks=False, domain=False, labelAngle=0)),
        y=alt.Y("expected:N", sort=tiers_c, title="Expected", axis=alt.Axis(ticks=False, domain=False)))
    heat = grid.mark_rect(stroke="#FFFFFF", strokeWidth=2, cornerRadius=4).encode(
        color=alt.Color("n:Q", legend=None, scale=alt.Scale(domain=[0, max(1, cm["n"].max())],
                                                            range=["#F5F5F4", "#9ec5f4", "#2a78d6", "#184f95"])),
        tooltip=[alt.Tooltip("expected:N", title="Expected"), alt.Tooltip("given:N", title="Given"),
                 alt.Tooltip("n:Q", title="Files")])
    nums = grid.mark_text(fontSize=13).encode(
        text=alt.Text("n:Q"),
        color=alt.condition(alt.datum.n >= max(1, cm["n"].max()) * 0.5, alt.value("#FFFFFF"), alt.value(INK)))
    st.altair_chart(base((heat + nums).properties(height=230)), width="stretch")
with right:
    st.subheader("Where decisions came from")
    dec = p.get("decided_by", {})
    names = {"rules": "Rules and checksums", "server": "AI on the server", "local_model": "AI on the laptop"}
    total = sum(dec.values()) or 1
    dd = pd.DataFrame([{"source": names.get(k, k), "n": v, "share": v / total, "o": i}
                       for i, (k, v) in enumerate(sorted(dec.items(), key=lambda kv: -kv[1])) if v])
    colors = {"Rules and checksums": BLUE, "AI on the server": AQUA, "AI on the laptop": ORANGE}
    ch = alt.Chart(dd).encode(
        y=alt.Y("source:N", sort=list(dd["source"]), title=None, axis=alt.Axis(ticks=False, domain=False, labelPadding=8)),
        x=alt.X("n:Q", title="Items decided", scale=alt.Scale(domain=[0, dd["n"].max() * 1.35])))
    st.altair_chart(base((ch.mark_bar(cornerRadiusEnd=4).encode(
        color=alt.Color("source:N", legend=None, scale=alt.Scale(domain=list(colors), range=list(colors.values()))),
        tooltip=[alt.Tooltip("source:N", title="Source"), alt.Tooltip("n:Q", title="Items"),
                 alt.Tooltip("share:Q", title="Share", format=".0%")])
        + ch.mark_text(align="left", dx=6, fontSize=12, color=INK).encode(
            text=alt.Text("label:N")).transform_calculate(
            label="datum.n + ' (' + format(datum.share, '.0%') + ')'")).properties(height=40 + 34 * len(dd))),
        width="stretch")
