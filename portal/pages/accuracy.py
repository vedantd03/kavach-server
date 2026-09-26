"""Accuracy: false alarms avoided versus rules alone, triage hours saved, assumptions stated."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import ui
from api_client import ApiError

ui.header("Accuracy", "How the pipeline compares with rules alone on a labelled test set.")

try:
    r = ui.api().eval()
except ApiError as e:
    if e.code == "EVAL_NOT_RUN":
        st.info("No evaluation has been run yet. Run `python eval/run_eval.py` on the server; "
                "results appear here as soon as eval/results.json exists.")
        st.stop()
    st.error(f"The server returned {e.status} {e.code}: {e.message}")
    st.stop()

base, pipe, imp, corpus = r["modes"]["rules_only"], r["modes"]["server"], r["impact"], r["corpus"]
bo, po = base["overall"], pipe["overall"]

st.markdown(
    f"### On {corpus['files']} test files, rules alone raised {bo['fp']} false alarms. "
    f"The pipeline raised {po['fp']}.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("False alarms avoided", imp["false_positives_avoided"])
c2.metric("Hours saved per 1,000 files", f"{imp['triage_hours_saved_per_1000_files']:g}")
c3.metric("Precision", f"{po['precision']:.1%}", delta=f"{(po['precision'] - bo['precision']) * 100:+.1f} pts vs rules")
c4.metric("Look-alikes ignored",
          f"{pipe['hard_negatives']['total'] - pipe['hard_negatives']['flagged']} of {pipe['hard_negatives']['total']}",
          help="Invoice, order, UTR and tracking numbers that pass the same checksums as Aadhaar or look like mobiles.")

left, right = st.columns([1, 1], gap="large")
with left:
    st.subheader("Rules alone versus the full pipeline")
    cmp = pd.DataFrame([
        ("Precision", f"{bo['precision']:.3f}", f"{po['precision']:.3f}"),
        ("Recall", f"{bo['recall']:.3f}", f"{po['recall']:.3f}"),
        ("F1", f"{bo['f1']:.3f}", f"{po['f1']:.3f}"),
        ("False alarms", bo["fp"], po["fp"]),
        ("Look-alikes ignored", f"{base['hard_negatives']['rejection_rate']:.0%}", f"{pipe['hard_negatives']['rejection_rate']:.0%}"),
        ("Files under-labelled", f"{base['tier']['under_labelling_rate']:.1%}", f"{pipe['tier']['under_labelling_rate']:.1%}"),
        ("File tier exactly right", f"{base['tier']['exact']} of {base['tier']['files']}", f"{pipe['tier']['exact']} of {pipe['tier']['files']}"),
        ("Median seconds per file", base["p50_seconds_per_file"], pipe["p50_seconds_per_file"]),
    ], columns=["Measure", "Rules alone", "Rules + context + AI"]).astype(str)
    st.dataframe(cmp, hide_index=True, width="stretch")

    st.subheader("By identifier type (pipeline)")
    pt = pd.DataFrame([{"Type": ui.TYPE_LABEL.get(t, t), "Precision": m["precision"], "Recall": m["recall"],
                        "F1": m["f1"], "Found": m["tp"], "False alarms": m["fp"], "Missed": m["fn"]}
                       for t, m in pipe["per_type"].items()])
    st.dataframe(pt, hide_index=True, width="stretch",
                 column_config={k: st.column_config.NumberColumn(format="%.3f") for k in ("Precision", "Recall", "F1")})

with right:
    st.subheader("File tiers: expected versus given")
    conf = pipe["tier"]["confusion"]
    mat = pd.DataFrame([[conf.get(e, {}).get(g, 0) for g in ui.TIERS] for e in ui.TIERS],
                       index=[t.capitalize() for t in ui.TIERS], columns=[t.capitalize() for t in ui.TIERS])
    st.dataframe(mat, width="stretch")
    st.caption("Rows are the tier a reviewer expects; columns are the tier Kavach gave. "  # noqa
               "Anything left of the diagonal is under-labelled, the costly mistake.")

    st.subheader("Where decisions came from")
    db = pipe.get("decided_by", {})
    total = sum(db.values()) or 1
    source = {"rules": "Rules and checksums", "server": "AI on the server", "local_model": "AI on the laptop"}
    st.markdown("  \n".join(f"{source.get(k, k)}: **{v}** ({v / total:.0%})" for k, v in db.items()))

    st.subheader("Assumptions")
    st.markdown(
        f"- Each false alarm costs an analyst **{r['minutes_per_alert']:g} minutes** to triage.\n"
        f"- Hours saved are scaled linearly from {corpus['files']} files to 1,000.\n"
        f"- The test set is synthetic: {corpus['labels']} labelled items ({corpus['sensitive']} sensitive, "
        f"{corpus['hard_negatives']} look-alikes), with fake but checksum-valid identifiers.\n"
        f"- `{r['model']}` stands in for the company's own LLM and OCR in a real deployment.\n"
        f"- Tiers come from an illustrative policy, version {r['policy_version']}. This is not legal advice.\n"
        f"- Evaluated {r['generated_at'].replace('T', ' ').rstrip('Z')} UTC.")
