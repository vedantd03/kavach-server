"""Smoke-test a running kavach-server before the agent exists.

    python scripts/smoke_detect.py --url http://localhost:8000 --token dev-token-1 <files...>

Extracts text locally (tiny extractor), sends images / scanned pages to POST /ocr, posts one
DetectRequest per <=20 files, and prints findings by tier. Prints masked values only.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "detect_core"))
sys.path.insert(0, str(ROOT))

from detect_core.contracts import DetectRequest, DetectResponse, OcrResult  # noqa: E402
from scripts.mini_extract import extract  # noqa: E402


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("SERVER_URL", "http://localhost:8000"))
    ap.add_argument("--token", default=os.environ.get("DEVICE_TOKEN", ""))
    ap.add_argument("--device", default=os.environ.get("DEVICE_ID", "SMOKE-01"))
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()

    headers = {"X-Device-Token": a.token} if a.token else {}
    client = httpx.Client(base_url=a.url.rstrip("/"), headers=headers, timeout=180)
    print("health:", client.get("/health").json())

    paths: list[Path] = []
    for f in a.files:
        p = Path(f)
        paths.extend(sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else [p])

    t0 = time.time()
    all_findings = []
    pending = 0
    for i in range(0, len(paths), 20):
        batch = paths[i:i + 20]
        metas, chunks = [], []
        for p in batch:
            def ocr_fn(data: bytes, mime: str, _p=p) -> OcrResult:
                r = client.post("/ocr", files={"image": (_p.name, data, mime)},
                                data={"device_id": a.device, "file_hash": "smoke", "page": "1"}, timeout=60)
                r.raise_for_status()
                return OcrResult.model_validate(r.json())
            meta, ch = extract(p, ocr_fn, display_path=str(p.resolve()))
            metas.append(meta)
            chunks.extend(ch)
            print(f"  extracted {p.name}: {meta.status} {len(ch)} chunks")
        req = DetectRequest(device_id=a.device, scan_id=None, files=metas, chunks=chunks)
        r = client.post("/detect", content=req.model_dump_json(), headers={"Content-Type": "application/json"})
        if r.status_code != 200:
            print("detect failed:", r.status_code, r.text[:300])
            return 1
        resp = DetectResponse.model_validate(r.json())
        all_findings.extend(resp.findings)
        pending += resp.pending
        print("  stats:", resp.stats)

    items = [f for f in all_findings if f.finding_kind == "item"]
    print(f"\n{len(paths)} files, {len(items)} item findings, pending={pending}, {time.time() - t0:.1f}s")
    print("by tier:", dict(Counter(f.sensitivity_tier for f in items)))
    for f in sorted(all_findings, key=lambda f: -f.risk_score):
        name = Path(f.file_path).name
        what = f"{f.pii_type} {f.masked_value}" if f.finding_kind == "item" else f"DOCUMENT {f.doc_type}"
        print(f"  [{f.sensitivity_tier:12}] {f.risk_score:5.1f} {name:32} {what:28} {f.decided_by:7} {f.reason[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
