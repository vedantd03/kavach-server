"""Evaluate the pipeline on the synthetic corpus: rules_only baseline vs server mode.

    python eval/run_eval.py            # writes eval/results.json and eval/report.md

Matches predictions to corpus/labels.jsonl by (file, value_hash, type). Output holds
aggregates only: no raw or masked values.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "detect_core"))
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from detect_core.contracts import DetectRequest  # noqa: E402
from detect_core.llm import GeminiClient  # noqa: E402
from detect_core.masking import value_hash  # noqa: E402
from detect_core.pipeline import detect  # noqa: E402
from detect_core.policy import combine_policy_text, load_policy  # noqa: E402
from scripts.mini_extract import extract  # noqa: E402

FILES = ROOT / "corpus" / "files"
LABELS = ROOT / "corpus" / "labels.jsonl"
OUT_JSON = ROOT / "eval" / "results.json"
OUT_MD = ROOT / "eval" / "report.md"
MINUTES_PER_ALERT = float(os.environ.get("MINUTES_PER_ALERT", "2"))
BATCH = 20


def _rel(p: Path) -> str:
    return p.relative_to(FILES).as_posix()


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3), "tp": tp, "fp": fp, "fn": fn}


def run_mode(mode: str, extracted, policy, client: GeminiClient | None):
    findings, per_file_s = [], []
    pending = 0
    for i in range(0, len(extracted), BATCH):
        batch = extracted[i:i + BATCH]
        req = DetectRequest(device_id="EVAL", scan_id=f"eval-{mode}",
                            files=[m for m, _, _ in batch], chunks=[c for _, ch, _ in batch for c in ch])
        t0 = time.monotonic()
        if mode == "server":
            resp = detect(req, policy, "server", verifier=client.verify_batch,
                          doc_classifier=client.classify_document)
        else:
            resp = detect(req, policy, "rules_only")
        dt = time.monotonic() - t0
        per_file_s.extend([dt / len(batch) + ex_s for _, _, ex_s in batch])
        findings.extend(resp.findings)
        pending += resp.pending
    return findings, pending, per_file_s


def score_mode(findings, labels, files, policy) -> dict:
    pos = {(l["file"], value_hash(l["value"]), l["type"]) for l in labels if l["is_sensitive"]}
    neg = {(l["file"], value_hash(l["value"]), l["type"]) for l in labels if not l["is_sensitive"]}
    items = [f for f in findings if f.finding_kind == "item"]
    pred = {(f.file_path, f.value_hash, f.pii_type) for f in items}

    types = sorted({k[2] for k in pos} | {k[2] for k in pred})
    per_type = {}
    for t in types:
        tp = len({k for k in pred if k[2] == t} & {k for k in pos if k[2] == t})
        fp = len({k for k in pred if k[2] == t} - {k for k in pos if k[2] == t})
        fn = len({k for k in pos if k[2] == t} - {k for k in pred if k[2] == t})
        per_type[t] = prf(tp, fp, fn)
    overall = prf(len(pred & pos), len(pred - pos), len(pos - pred))

    # hard negatives: flagged under the look-alike type or any type with the same value
    pred_vals = {(f, vh) for f, vh, _ in pred}
    hn_flagged = sum(1 for (f, vh, _) in neg if (f, vh) in pred_vals)

    # file tier: document finding tier, else public
    doc_tier = {f.file_path: f.sensitivity_tier for f in findings if f.finding_kind == "document"}
    expected = {}
    for l in labels:
        expected.setdefault(l["file"], l["expected_tier_of_file"])
    confusion: dict[str, Counter] = defaultdict(Counter)
    under = over = exact = 0
    under_files = []
    for fpath in files:
        exp = expected.get(fpath, "public")
        got = doc_tier.get(fpath, "public")
        confusion[exp][got] += 1
        if policy.rank(got) < policy.rank(exp):
            under += 1
            under_files.append(fpath)
        elif policy.rank(got) > policy.rank(exp):
            over += 1
        else:
            exact += 1

    return {
        "overall": overall,
        "per_type": per_type,
        "hard_negatives": {"total": len(neg), "flagged": hn_flagged,
                           "rejection_rate": round(1 - hn_flagged / len(neg), 3) if neg else None},
        "tier": {"files": len(files), "exact": exact, "over_labelled": over, "under_labelled": under,
                 "under_labelling_rate": round(under / len(files), 3),
                 "under_labelled_files": under_files,
                 "confusion": {e: dict(c) for e, c in confusion.items()}},
        "decided_by": dict(Counter(f.decided_by for f in items)),
        "findings": len(items),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    policy = load_policy(combine_policy_text((ROOT / "policy" / "policy.yaml").read_text(encoding="utf-8"),
                                             (ROOT / "policy" / "lexicon.yaml").read_text(encoding="utf-8")))
    labels = [json.loads(l) for l in LABELS.read_text(encoding="utf-8").splitlines() if l.strip()]
    traces: list[dict] = []
    cache = os.environ.get("LLM_CACHE_DIR") or str(ROOT / "data" / "cache")
    client = GeminiClient(cache_dir=cache if Path(cache).is_absolute() else ROOT / cache,
                          trace=traces.append, policy=policy)

    paths = sorted(p for p in FILES.rglob("*") if p.is_file())
    extracted = []
    for p in paths:  # extract once (OCR once) and reuse for both modes
        t0 = time.monotonic()
        meta, chunks = extract(p, client.ocr, display_path=_rel(p))
        extracted.append((meta, chunks, time.monotonic() - t0))
    files = [_rel(p) for p in paths]
    ocr_calls = sum(1 for t in traces if t["endpoint"] == "ocr")

    results: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "policy_version": policy.version, "model": client.model,
                     "corpus": {"files": len(files), "labels": len(labels),
                                "sensitive": sum(l["is_sensitive"] for l in labels),
                                "hard_negatives": sum(not l["is_sensitive"] for l in labels)},
                     "minutes_per_alert": MINUTES_PER_ALERT, "modes": {}}
    for mode in ("rules_only", "server"):
        before = len(traces)
        findings, pending, per_file_s = run_mode(mode, extracted, policy, client)
        r = score_mode(findings, labels, files, policy)
        mt = traces[before:]
        r["pending"] = pending
        r["llm_calls"] = dict(Counter(t["endpoint"] for t in mt if t["status"] != "cached"))
        r["llm_cache_hits"] = sum(1 for t in mt if t["status"] == "cached")
        r["key_rotations"] = sum(max(0, t.get("attempts", 1) - 1) for t in mt)
        r["key_indexes_used"] = sorted({t["key_index"] for t in mt if t.get("key_index") is not None})
        r["p50_seconds_per_file"] = round(statistics.median(per_file_s), 3)
        results["modes"][mode] = r
    results["ocr_calls"] = ocr_calls

    base, pipe = results["modes"]["rules_only"], results["modes"]["server"]
    fp_b, fp_p = base["overall"]["fp"], pipe["overall"]["fp"]
    per_1000 = 1000 / len(files)
    results["impact"] = {
        "false_positives_baseline": fp_b, "false_positives_pipeline": fp_p,
        "false_positives_avoided": fp_b - fp_p,
        "triage_hours_saved_per_1000_files": round((fp_b - fp_p) * MINUTES_PER_ALERT / 60 * per_1000, 1),
        "assumption": f"{MINUTES_PER_ALERT:g} analyst minutes per false alert; scaled linearly from {len(files)} files",
    }
    OUT_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    OUT_MD.write_text(report(results), encoding="utf-8")
    print(report(results))


def report(r: dict) -> str:
    b, p = r["modes"]["rules_only"], r["modes"]["server"]
    lines = [
        "# Kavach evaluation", "",
        f"Corpus: {r['corpus']['files']} files, {r['corpus']['labels']} labels "
        f"({r['corpus']['sensitive']} sensitive, {r['corpus']['hard_negatives']} hard negatives). "
        f"Policy {r['policy_version']}, model `{r['model']}`. Synthetic data only.", "",
        "| Metric | Rules only (baseline) | Pipeline (rules + context + LLM) |",
        "| --- | --- | --- |",
        f"| Precision | {b['overall']['precision']:.3f} | {p['overall']['precision']:.3f} |",
        f"| Recall | {b['overall']['recall']:.3f} | {p['overall']['recall']:.3f} |",
        f"| F1 | {b['overall']['f1']:.3f} | {p['overall']['f1']:.3f} |",
        f"| False positives | {b['overall']['fp']} | {p['overall']['fp']} |",
        f"| Hard-negative rejection | {b['hard_negatives']['rejection_rate']} | {p['hard_negatives']['rejection_rate']} |",
        f"| Under-labelling rate (files) | {b['tier']['under_labelling_rate']} | {p['tier']['under_labelling_rate']} |",
        f"| Exact file tier | {b['tier']['exact']}/{b['tier']['files']} | {p['tier']['exact']}/{p['tier']['files']} |",
        f"| p50 s/file | {b['p50_seconds_per_file']} | {p['p50_seconds_per_file']} |", "",
        f"Triage hours saved per 1,000 files: **{r['impact']['triage_hours_saved_per_1000_files']}** "
        f"({r['impact']['false_positives_avoided']} false alerts avoided; {r['impact']['assumption']}).", "",
        f"Pipeline decided_by: {p['decided_by']}. LLM calls: {p['llm_calls']} (+{p['llm_cache_hits']} cache hits), "
        f"OCR calls: {r['ocr_calls']}, key rotations: {p['key_rotations']}, keys used: {p['key_indexes_used']}.", "",
        "## Per type (pipeline)", "", "| Type | P | R | F1 | TP | FP | FN |", "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t, m in p["per_type"].items():
        lines.append(f"| {t} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | {m['tp']} | {m['fp']} | {m['fn']} |")
    lines += ["", "## Tier confusion (pipeline; rows = expected, cols = predicted)", ""]
    tiers = ["restricted", "confidential", "internal", "public"]
    lines.append("| expected \\ predicted | " + " | ".join(tiers) + " |")
    lines.append("| --- |" + " --- |" * len(tiers))
    for e in tiers:
        row = p["tier"]["confusion"].get(e, {})
        lines.append(f"| {e} | " + " | ".join(str(row.get(t, 0)) for t in tiers) + " |")
    if p["tier"]["under_labelled_files"]:
        lines += ["", "Under-labelled files: " + ", ".join(p["tier"]["under_labelled_files"])]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
