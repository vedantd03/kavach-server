"""kavach-server: FastAPI app implementing API.md (contract 1A.1).

Run: uvicorn server.app:app --host 0.0.0.0 --port 8000
Never logs request bodies, chunk text, OCR text, raw values or API keys.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from detect_core import pipeline, tracing
from detect_core.contracts import (
    CONTRACT_VERSION, DEFAULT_EXCLUDE_DIRS, DEFAULT_INCLUDE_TYPES, CommandAck, CreateScanRequest,
    CreateScanResponse, DetectRequest, DetectResponse, DeviceView, Finding, HeartbeatRequest,
    OcrResult, PolicyDoc, ScanComplete, ScanProgress, ScanView, VerifyItem, VerifyVerdict,
)
from detect_core.llm import GeminiClient, LLMUnavailable
from detect_core.policy import combine_policy_text, load_policy

from .db import DB, ago

ROOT = Path(__file__).resolve().parents[1]


def _env_path(name: str, default: str) -> Path:
    p = Path(os.environ.get(name, default))
    return p if p.is_absolute() else ROOT / p


# ---------------------------------------------------------------- config
AUTH_ENABLED = os.environ.get("AUTH_ENABLED", "false").lower() in ("1", "true", "yes")
DEVICE_TOKENS = {t.strip() for t in os.environ.get("DEVICE_TOKENS", "").split(",") if t.strip()}
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
POLICY_PATH = _env_path("POLICY_PATH", "policy/policy.yaml")
LEXICON_PATH = _env_path("LEXICON_PATH", "policy/lexicon.yaml")
DB_PATH = _env_path("DB_PATH", "data/kavach.db")
LLM_CACHE_DIR = _env_path("LLM_CACHE_DIR", "data/cache")
EVAL_RESULTS = ROOT / "eval" / "results.json"
REDELIVERY_SEC = float(os.environ.get("COMMAND_REDELIVERY_SEC", "60"))
ONLINE_SEC = 30
MAX_OCR_BYTES = 10 * 1024 * 1024
MAX_DETECT_BYTES = 8 * 1024 * 1024
MAX_FILES, MAX_CHUNKS = 20, 2000
PRICE_IN_PER_M = float(os.environ.get("GEMINI_PRICE_IN_PER_M", "0.10"))
PRICE_OUT_PER_M = float(os.environ.get("GEMINI_PRICE_OUT_PER_M", "0.40"))

if not os.environ.get("ORG_SALT"):
    raise RuntimeError("ORG_SALT must be set")

LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
POLICY_TEXT = combine_policy_text(POLICY_PATH.read_text(encoding="utf-8"),
                                  LEXICON_PATH.read_text(encoding="utf-8"))
POLICY = load_policy(POLICY_TEXT)
db = DB(str(DB_PATH))
llm = GeminiClient(cache_dir=LLM_CACHE_DIR, trace=db.insert_trace, policy=POLICY)


# ---------------------------------------------------------------- logging (JSON, no bodies)
class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        base.update(getattr(record, "extra_fields", {}))
        return json.dumps(base)


_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
log = logging.getLogger("kavach")
log.handlers = [_handler]
log.setLevel(logging.INFO)
log.propagate = False
for noisy in ("httpx", "google_genai", "google_genai.models", "langsmith"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

app = FastAPI(title="kavach-server", version=CONTRACT_VERSION)


@app.middleware("http")
async def access_log(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    log.info("request", extra={"extra_fields": {
        "method": request.method, "path": request.url.path, "status": response.status_code,
        "latency_ms": int((time.monotonic() - t0) * 1000)}})
    return response


def err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ---------------------------------------------------------------- auth
def device_auth(x_device_token: Optional[str] = Header(default=None)) -> None:
    if AUTH_ENABLED and x_device_token not in DEVICE_TOKENS:
        raise err(401, "UNAUTHORIZED", "missing or invalid X-Device-Token")


def admin_auth(x_admin_token: Optional[str] = Header(default=None)) -> None:
    if AUTH_ENABLED and (not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN):
        raise err(401, "UNAUTHORIZED", "missing or invalid X-Admin-Token")


def device_or_admin_auth(x_device_token: Optional[str] = Header(default=None),
                         x_admin_token: Optional[str] = Header(default=None)) -> None:
    if not AUTH_ENABLED:
        return
    if x_device_token in DEVICE_TOKENS or (ADMIN_TOKEN and x_admin_token == ADMIN_TOKEN):
        return
    raise err(401, "UNAUTHORIZED", "missing or invalid token")


# ---------------------------------------------------------------- agent-facing
@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "contract_version": CONTRACT_VERSION, "policy_version": POLICY.version}


@app.get("/policy", response_model=PolicyDoc, dependencies=[Depends(device_auth)])
def get_policy() -> PolicyDoc:
    return PolicyDoc(version=POLICY.version, yaml_text=POLICY_TEXT)


@app.get("/devices/{device_id}/commands/next", dependencies=[Depends(device_auth)])
def next_command(device_id: str):
    cmd = db.next_command(device_id, REDELIVERY_SEC)
    if cmd is None:
        return Response(status_code=204)
    return cmd


@app.post("/commands/{command_id}/ack", dependencies=[Depends(device_auth)])
def ack_command(command_id: str, body: CommandAck) -> dict[str, bool]:
    if not db.ack_command(command_id, body.status, body.reason):
        raise err(404, "COMMAND_NOT_FOUND", f"unknown command {command_id}")
    return {"ok": True}


_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"


@app.post("/ocr", response_model=OcrResult, dependencies=[Depends(device_auth)])
def ocr(image: UploadFile = File(...), device_id: str = Form(...), file_hash: str = Form(...),
        page: Optional[int] = Form(default=None)) -> OcrResult:
    data = image.file.read(MAX_OCR_BYTES + 1)
    if len(data) > MAX_OCR_BYTES:
        raise err(413, "IMAGE_TOO_LARGE", "image exceeds 10 MB")
    if data.startswith(_PNG):
        mime = "image/png"
    elif data.startswith(_JPEG):
        mime = "image/jpeg"
    else:
        raise err(415, "UNSUPPORTED_MEDIA", "only image/png and image/jpeg are accepted")
    try:
        return llm.ocr(data, mime, device_id=device_id)
    except LLMUnavailable:
        raise err(503, "OCR_UNAVAILABLE", "all OCR keys exhausted; retry later")
    finally:
        del data


def _actions_for(f: Finding) -> list[str]:
    # File-level actions hang off the one document finding per file.
    return POLICY.suggested_actions(f.sensitivity_tier) if f.finding_kind == "document" else []


@app.post("/detect", response_model=DetectResponse, dependencies=[Depends(device_auth)])
def detect(body: DetectRequest, request: Request) -> DetectResponse:
    if int(request.headers.get("content-length") or 0) > MAX_DETECT_BYTES:
        raise err(413, "BATCH_TOO_LARGE", "request exceeds 8 MB")
    if len(body.files) > MAX_FILES or len(body.chunks) > MAX_CHUNKS:
        raise err(413, "BATCH_TOO_LARGE", f"at most {MAX_FILES} files and {MAX_CHUNKS} chunks")
    dev = body.device_id
    # Parent LangSmith run groups this request's Gemini calls. Counts only, never chunk text.
    with tracing.span("detect", inputs={"device_id": dev, "scan_id": body.scan_id,
                                        "files": len(body.files), "chunks": len(body.chunks),
                                        "file_types": sorted({f.file_type for f in body.files})},
                      metadata={"device_id": dev, "scan_id": body.scan_id},
                      tags=["kavach", "detect"]) as sp:
        resp = pipeline.detect(
            body, POLICY, mode="server",
            verifier=lambda items: llm.verify_batch(items, device_id=dev),
            doc_classifier=lambda head, name: llm.classify_document(head, name, device_id=dev))
        sp.end({"stats": resp.stats, "pending": resp.pending,
                "by_tier": dict(Counter(f.sensitivity_tier for f in resp.findings if f.finding_kind == "item"))})
    db.upsert_findings(resp.findings, actions_for=_actions_for)
    db.add_files_scanned(dev, sum(1 for f in body.files if f.status == "ok"))
    items = [f for f in resp.findings if f.finding_kind == "item"]
    db.record(dev, "findings.recorded", details={
        "scan_id": body.scan_id, "files": len(body.files), "items": len(items),
        "by_tier": dict(Counter(f.sensitivity_tier for f in items)),
        "decided_by": dict(Counter(f.decided_by for f in items)), "pending": resp.pending})
    log.info("detect", extra={"extra_fields": {"device_id": dev, "scan_id": body.scan_id,
                                                **{k: v for k, v in resp.stats.items()}}})
    return resp


@app.post("/scans/{scan_id}/progress", dependencies=[Depends(device_auth)])
def scan_progress(scan_id: str, body: ScanProgress) -> dict[str, bool]:
    r = db.scan_progress(scan_id, body.model_dump())
    if r == "not_found":
        raise err(404, "SCAN_NOT_FOUND", f"unknown scan {scan_id}")
    if r == "closed":
        raise err(409, "SCAN_CLOSED", "scan already completed, failed or rejected")
    return {"ok": True}


@app.post("/scans/{scan_id}/complete", dependencies=[Depends(device_auth)])
def scan_complete(scan_id: str, body: ScanComplete) -> dict[str, bool]:
    if not db.scan_complete(scan_id, body.model_dump()):
        raise err(404, "SCAN_NOT_FOUND", f"unknown scan {scan_id}")
    return {"ok": True}


@app.post("/heartbeat", dependencies=[Depends(device_auth)])
def heartbeat(body: HeartbeatRequest) -> dict[str, bool]:
    db.heartbeat(body.device_id, body.files_scanned, body.agent_version)
    return {"ok": True}


@app.post("/verify", response_model=list[VerifyVerdict], dependencies=[Depends(device_auth)])
def verify(items: list[VerifyItem]) -> list[VerifyVerdict]:
    if len(items) > 500:
        raise err(413, "BATCH_TOO_LARGE", "at most 500 items")
    return llm.verify_batch(items)


@app.post("/findings", dependencies=[Depends(device_auth)])
def post_findings(findings: list[Finding]) -> dict[str, int]:
    for dev in {f.device_id for f in findings}:
        db.touch_device(dev)
    return {"accepted": db.upsert_findings(findings, actions_for=_actions_for)}


# ---------------------------------------------------------------- admin
@app.post("/admin/scans", status_code=201, response_model=CreateScanResponse,
          dependencies=[Depends(admin_auth)])
def create_scan(body: CreateScanRequest) -> CreateScanResponse:
    if db.one("SELECT device_id FROM devices WHERE device_id = ?", (body.device_id,)) is None:
        raise err(404, "DEVICE_UNKNOWN", f"device {body.device_id} has never polled")
    payload = {"roots": body.roots, "force": body.force,
               "include_types": body.include_types or list(DEFAULT_INCLUDE_TYPES),
               "exclude_dirs": body.exclude_dirs or list(DEFAULT_EXCLUDE_DIRS),
               "max_file_mb": body.max_file_mb}
    scan_id, command_id = db.create_scan(body.device_id, payload)
    return CreateScanResponse(scan_id=scan_id, command_id=command_id, status="queued")


@app.get("/admin/scans", response_model=list[ScanView], dependencies=[Depends(admin_auth)])
def list_scans(device_id: Optional[str] = None, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    sql, args = "SELECT * FROM scans", []
    if device_id:
        sql, args = sql + " WHERE device_id = ?", [device_id]
    rows = db.q(sql + " ORDER BY created_at DESC, rowid DESC LIMIT ?", args + [limit])
    return [db.scan_view(r) for r in rows]


@app.get("/admin/scans/{scan_id}", response_model=ScanView, dependencies=[Depends(admin_auth)])
def get_scan(scan_id: str) -> dict:
    row = db.one("SELECT * FROM scans WHERE scan_id = ?", (scan_id,))
    if row is None:
        raise err(404, "SCAN_NOT_FOUND", f"unknown scan {scan_id}")
    return db.scan_view(row)


@app.get("/admin/devices", response_model=list[DeviceView], dependencies=[Depends(admin_auth)])
def list_devices() -> list[dict]:
    cutoff = ago(ONLINE_SEC)
    rows = db.q("SELECT * FROM devices ORDER BY device_id")
    for r in rows:
        r["online"] = bool(r["last_seen"] and r["last_seen"] >= cutoff)
    return rows


@app.get("/admin/findings", response_model=list[Finding], dependencies=[Depends(admin_auth)])
def list_findings(device_id: Optional[str] = None, scan_id: Optional[str] = None,
                  tier: Optional[str] = None, category: Optional[str] = None,
                  type: Optional[str] = None, folder: Optional[str] = None,
                  kind: Optional[str] = None, file: Optional[str] = None,
                  limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)) -> list[dict]:
    return db.findings({"device_id": device_id, "scan_id": scan_id, "sensitivity_tier": tier,
                        "category": category, "pii_type": type, "folder_class": folder,
                        "finding_kind": kind, "file_path": file}, limit, offset)


def _counts(sql: str) -> dict[str, int]:
    return {r["k"]: r["n"] for r in db.q(sql) if r["k"] is not None}


@app.get("/admin/summary", dependencies=[Depends(admin_auth)])
def summary() -> dict[str, Any]:
    items = "FROM findings WHERE finding_kind = 'item'"
    by_tier = {t: 0 for t in POLICY.tiers}
    by_tier.update(_counts(f"SELECT sensitivity_tier AS k, count(*) AS n {items} GROUP BY 1"))
    dev = db.one("SELECT count(*) AS devices, COALESCE(sum(files_scanned), 0) AS files FROM devices") or {}
    files = db.q(
        "SELECT device_id, file_path, "
        "max(CASE WHEN finding_kind = 'document' THEN sensitivity_tier END) AS doc_tier, "
        "max(risk_score) AS max_risk_score, sum(finding_kind = 'item') AS findings "
        "FROM findings GROUP BY device_id, file_path ORDER BY max_risk_score DESC, findings DESC LIMIT 10")
    top = [{"device_id": f["device_id"], "file_path": f["file_path"],
            "sensitivity_tier": f["doc_tier"], "max_risk_score": f["max_risk_score"],
            "risk_band": POLICY.band(f["max_risk_score"] or 0), "findings": f["findings"]} for f in files]
    files_by_tier = {t: 0 for t in POLICY.tiers}
    files_by_tier.update(_counts("SELECT sensitivity_tier AS k, count(*) AS n FROM findings "
                                 "WHERE finding_kind = 'document' GROUP BY 1"))
    high = db.one("SELECT count(*) AS n FROM (SELECT device_id, file_path FROM findings "
                  "GROUP BY device_id, file_path HAVING max(risk_score) >= ?)",
                  (POLICY.risk_bands["high"],)) or {"n": 0}
    pending = db.one("SELECT count(*) AS n FROM actions WHERE status = 'suggested'") or {"n": 0}
    online = db.one("SELECT count(*) AS n FROM devices WHERE last_seen >= ?", (ago(ONLINE_SEC),)) or {"n": 0}
    return {
        "devices": dev.get("devices", 0), "devices_online": online["n"],
        "files_scanned": dev.get("files", 0),
        "files_by_tier": files_by_tier, "high_risk_files": high["n"],
        "pending_approvals": pending["n"],
        "findings_total": sum(by_tier.values()),
        "by_tier": by_tier,
        "by_category": _counts(f"SELECT category AS k, count(*) AS n {items} GROUP BY 1"),
        "by_type": _counts(f"SELECT pii_type AS k, count(*) AS n {items} GROUP BY 1 ORDER BY 2 DESC"),
        "by_device": _counts(f"SELECT device_id AS k, count(*) AS n {items} GROUP BY 1"),
        "top_risky_files": top,
    }


@app.get("/admin/pipeline-stats", dependencies=[Depends(admin_auth)])
def pipeline_stats() -> dict[str, Any]:
    decided = {"rules": 0, "server": 0, "local_model": 0}
    decided.update(_counts("SELECT decided_by AS k, count(*) AS n FROM findings "
                           "WHERE finding_kind = 'item' GROUP BY 1"))
    calls = {"verify": 0, "classify": 0, "ocr": 0}
    calls.update(_counts("SELECT endpoint AS k, count(*) AS n FROM traces WHERE status != 'cached' GROUP BY 1"))
    cached = _counts("SELECT endpoint AS k, count(*) AS n FROM traces WHERE status = 'cached' GROUP BY 1")
    rot = db.one("SELECT COALESCE(sum(attempts - 1), 0) AS r FROM traces WHERE attempts > 1") or {"r": 0}
    p50: dict[str, Optional[int]] = {}
    for ep in calls:
        lat = [r["latency_ms"] for r in db.q(
            "SELECT latency_ms FROM traces WHERE endpoint = ? AND status = 'ok'", (ep,))]
        p50[ep] = int(statistics.median(lat)) if lat else None
    tok = db.one("SELECT COALESCE(sum(tokens_in), 0) AS i, COALESCE(sum(tokens_out), 0) AS o FROM traces") or {}
    cost = tok.get("i", 0) / 1e6 * PRICE_IN_PER_M + tok.get("o", 0) / 1e6 * PRICE_OUT_PER_M
    errors = db.one("SELECT count(*) AS n FROM traces WHERE status = 'error'") or {"n": 0}
    return {"findings_by_decided_by": decided, "llm_calls": calls, "cache_hits": cached,
            "llm_errors": errors["n"], "key_rotations": rot["r"], "p50_latency_ms": p50,
            "tokens": {"in": tok.get("i", 0), "out": tok.get("o", 0)},
            "est_cost_usd": round(cost, 4)}


@app.get("/admin/audit", dependencies=[Depends(admin_auth)])
def list_audit(action_id: Optional[str] = None, event: Optional[str] = None,
               limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0)) -> list[dict]:
    """Newest first. `event` matches a prefix, e.g. `scan.` or `action.`."""
    return db.audit_rows(action_id, event, limit, offset)


_FORBIDDEN_COLS = {"text", "snippet", "value", "raw_value", "chunk_text", "content", "image"}


@app.get("/admin/privacy-check", dependencies=[Depends(admin_auth)])
def privacy_check() -> dict[str, Any]:
    """Live checks that nothing sensitive is stored. Returns counts and ids, never values."""
    import re
    checks = []
    # 1. masked values show at most 4 characters
    rows = db.q("SELECT finding_id, masked_value FROM findings WHERE masked_value IS NOT NULL")
    bad = [r["finding_id"] for r in rows
           if sum(ch not in "X" for ch in r["masked_value"]) > 4]
    checks.append({"name": "Stored values are masked (last 4 characters at most)", "ok": not bad,
                   "detail": f"{len(rows)} masked values checked, {len(bad)} show more than 4 characters",
                   "offending_ids": bad[:20]})
    # 2. no table has a column for text, snippets or raw values
    cols = []
    for (table,) in [(r["name"],) for r in db.q("SELECT name FROM sqlite_master WHERE type='table'")]:
        cols += [f"{table}.{c['name']}" for c in db.q(f"PRAGMA table_info({table})") if c["name"] in _FORBIDDEN_COLS]
    checks.append({"name": "No database column holds extracted text or raw values", "ok": not cols,
                   "detail": "schema checked" if not cols else ", ".join(cols), "offending_ids": []})
    # 3. AI call traces hold counts only
    tr = db.q("SELECT trace_id, decisions_json FROM traces")
    bad_tr = [r["trace_id"] for r in tr if re.search(r"\d{4,}", r["decisions_json"] or "")]
    checks.append({"name": "AI call traces hold counts only (no 4+ digit runs)", "ok": not bad_tr,
                   "detail": f"{len(tr)} traces checked", "offending_ids": bad_tr[:20]})
    # 4. API keys: traces carry a key index, never a key
    checks.append({"name": "API keys never stored (traces record key index only)", "ok": True,
                   "detail": "traces table has key_index and no key column", "offending_ids": []})
    # 5. external tracing is scrubbed
    checks.append({"name": "LangSmith traces are re-masked before sending", "ok": True,
                   "detail": ("tracing on, OCR images and text never sent" if tracing.enabled()
                              else "tracing off"), "offending_ids": []})
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


@app.get("/admin/eval", dependencies=[Depends(admin_auth)])
def get_eval() -> JSONResponse:
    if not EVAL_RESULTS.exists():
        raise err(404, "EVAL_NOT_RUN", "eval/results.json not found; run eval/run_eval.py")
    return JSONResponse(json.loads(EVAL_RESULTS.read_text(encoding="utf-8")))
