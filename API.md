# api.md: pii-server HTTP contract, Phase 1A (v1A.1)

**Source of truth** for the agent ↔ server interface. Data models live in `detect_core/contracts.py`; this file specifies the HTTP behaviour.
**Owners:** Server = Person B (`pii-server/`). Agent = Person A (`pii-agent/`).
**Frozen at 13:50 IST.** Changes after that go under "Contract changes" in `BUILD_PLAN.md`, agreed by both people.

---

## 0. Scope of Phase 1A

- **Agent:** polls for commands, crawls, extracts text, and splits it into chunks. It sends images and scanned pages to `/ocr` and chunks to `/detect`. It reports progress and stores the returned findings (metadata only). **No regex, ML or OCR on the laptop.**
- **Server:** queues commands, runs OCR through Gemini, runs `pipeline.detect(mode="server")`, stores findings, and serves the admin read APIs.
- **Out of scope for 1A:** actions (1B), agent-local detection (Phase 2), portal (Phase 3). Their endpoints are listed in §5 as reserved.

**Changes from BUILD_PLAN T0 (also recorded in `CONTRACT_CHANGES.md`):**
1. New command-polling endpoints: `GET /devices/{id}/commands/next`, `POST /commands/{id}/ack`, `POST /scans/{id}/progress`, `POST /scans/{id}/complete`.
2. New admin endpoints: `POST /admin/scans`, `GET /admin/scans`, `GET /admin/scans/{id}`, and a `scan_id` filter on `/admin/findings`.
3. `scan_id` (optional) added to `DetectRequest` and `Finding`. New `commands` and `scans` tables.
4. **Auth is off by default in 1A** (`AUTH_ENABLED=false`). Headers are defined but not enforced.

---

## 1. Conventions

| Topic | Rule |
|---|---|
| Base URL | `SERVER_URL` (e.g. `http://192.168.1.20:8000`). No path prefix. |
| Encoding | JSON, UTF-8. `/ocr` is `multipart/form-data`. |
| Timestamps | ISO-8601 UTC, e.g. `2026-09-26T09:30:00Z` |
| IDs | Server-generated: `scan_<12 hex>`, `cmd_<12 hex>`, `fnd_<24 hex>` (`finding_id_for`). Agent: `device_id` from env (`LAPTOP-01`); CLI scans use `local-<uuid4>`. |
| Auth | When `AUTH_ENABLED=true`: device endpoints need `X-Device-Token` ∈ `DEVICE_TOKENS`; admin endpoints need `X-Admin-Token` = `ADMIN_TOKEN`. **1A default: `false`.** The agent always sends `X-Device-Token` if `DEVICE_TOKEN` is set. |
| Errors | `{"detail": {"code": "SCAN_NOT_FOUND", "message": "..."}}`. `422` uses FastAPI's native validation body. |
| Unknown fields | Rejected (`extra="forbid"`) → `422`. Contract drift fails loudly. |
| Agent retries | Retry network errors and `429/502/503/504` with backoff 1, 2, 4, 8, 16 s (5 tries). Don't retry any other `4xx`. |
| Agent timeouts | Poll 15 s · `/ocr` 60 s · `/detect` 180 s · others 15 s |
| Privacy on the wire | Raw text travels **only** in `Chunk.text` (→ `/detect`) and image bytes (→ `/ocr`). No response ever contains a raw value. The server never stores chunk text or images and never logs request bodies. The agent never logs chunk or OCR text. |

---

## 2. End-to-end flow

```
Admin/dashboard            Server                                   Agent (laptop)
POST /admin/scans ───────▶ scans(queued) + commands(pending)
                                                    ◀── GET /devices/LAPTOP-01/commands/next  (every POLL_INTERVAL_SEC)
                           command → delivered ────▶ 200 Command{SCAN}
                                                    ◀── POST /commands/{id}/ack {accepted}
                           scan → running
                                                         Crawler → files(discovered)
                                                         Processor: extract → chunks
                                                    ◀── POST /ocr (image / scanned page)   → OcrResult
                                                    ◀── POST /detect (≤20 files)           → DetectResponse
                                                         store findings (metadata only)
                                                    ◀── POST /scans/{id}/progress  (every 5 s)
                                                    ◀── POST /heartbeat            (every 30 s)
                                                    ◀── POST /scans/{id}/complete
                           scan → completed
GET /admin/scans/{id}, /admin/findings?scan_id=…, /admin/summary
```

---

## 3. Agent-facing endpoints (Phase 1A)

### 3.1 `GET /health`
→ `200 {"status": "ok", "contract_version": "1A.1", "policy_version": "2026-09-26.1"}`

### 3.2 `GET /devices/{device_id}/commands/next`  *(new)*
Also works as a heartbeat: it upserts `devices(device_id)` and sets `last_seen`, so a device appears on the dashboard after its first poll.
- `204`: nothing pending.
- `200`: body is `Command`:
```json
{
  "command_id": "cmd_3f9a1c2b7d4e",
  "type": "SCAN",
  "created_at": "2026-09-26T09:30:00Z",
  "payload": {
    "scan_id": "scan_8b21e0d4a911",
    "roots": ["/Users/demo/demo_folder"],
    "force": false,
    "include_types": ["txt","md","log","json","env","ini","yaml","yml","xml","eml","csv","tsv","xlsx","docx","pdf","png","jpg","jpeg","tiff"],
    "exclude_dirs": [".git","node_modules",".venv","venv","__pycache__",".cache","Library","AppData",".Trash",".pii_vault"],
    "max_file_mb": 50
  }
}
```
Server rules:
- Returns the **oldest** command with status `pending`, or `delivered` with `delivered_at` older than 60 s (`COMMAND_REDELIVERY_SEC`).
- Sets `status=delivered`, `delivered_at=now`, and moves the scan to `delivered`.
- At most one command per poll.

Agent rules:
- **Dedupe by `command_id`.** If the id is already in local `commands`, re-send the stored ack and do nothing else.

### 3.3 `POST /commands/{command_id}/ack`  *(new)*
Body `CommandAck`: `{"status": "accepted"}` or `{"status": "rejected", "reason": "PATH_NOT_FOUND"}`

Agent rules:
- Reject with `PATH_NOT_FOUND` if **no** root exists. If some roots exist, accept and scan only those.
- Reject with `UNSUPPORTED_COMMAND` for any type other than `SCAN`.
- Accepted scans queue locally and run **one at a time, FIFO**.

Server: sets the command to `accepted` or `rejected`. `accepted` → scan `running` with `started_at=now`; `rejected` → scan `rejected` with `reject_reason`. Acking the same command again is a no-op.
→ `200 {"ok": true}` · `404 COMMAND_NOT_FOUND`

### 3.4 `POST /ocr`
`multipart/form-data`:

| Part | Required | Notes |
|---|---|---|
| `image` | ✅ | `image/png` or `image/jpeg`, ≤ 10 MB. The agent converts TIFF (first frame) to PNG and renders scanned PDF pages at 150 dpi to PNG. |
| `device_id` | ✅ | form field |
| `file_hash` | ✅ | form field (traces only) |
| `page` | – | form field, 1-based |

→ `200 OcrResult`: `{"text": "...", "confidence": 0.82, "pages": 1}`

Errors: `413 IMAGE_TOO_LARGE` · `415 UNSUPPORTED_MEDIA` · `503 OCR_UNAVAILABLE` (all Gemini keys exhausted; the agent retries per §1).

Server: never stores the image or the text; writes one `traces` row.

Agent:
- Empty `text` → no chunks for that page.
- If OCR fails after retries: an image file → `FileMeta.status="unscannable"`, `status_reason="ocr_failed"`. For a PDF, keep the pages that succeeded. If every scanned page failed and there's no text at all → `ocr_failed`.

### 3.5 `POST /detect`
Body `DetectRequest`. Limits:
- ≤ 20 files, ≤ 2,000 chunks, ≤ 8 MB. **A file is never split across requests.**
- `files` lists **every** file in the batch, including unscannable and zero-chunk files. `chunks` only reference files in `files` with `status="ok"`.

```json
{
  "device_id": "LAPTOP-01",
  "scan_id": "scan_8b21e0d4a911",
  "files": [
    {"file_path": "/Users/demo/demo_folder/Documents/whatsapp_chat_support.txt",
     "file_hash": "9f2c…e1", "file_type": "txt", "folder_class": "documents",
     "size_bytes": 4812, "modified_at": "2026-09-20T11:02:00Z", "status": "ok", "status_reason": null},
    {"file_path": "/Users/demo/demo_folder/Downloads/locked.pdf",
     "file_hash": "77ab…90", "file_type": "pdf", "folder_class": "downloads",
     "size_bytes": 99120, "modified_at": "2026-09-19T08:00:00Z", "status": "unscannable", "status_reason": "encrypted"}
  ],
  "chunks": [
    {"chunk_id": "9f2c4b1a0e7d3c55:0",
     "file_path": "/Users/demo/demo_folder/Documents/whatsapp_chat_support.txt",
     "file_hash": "9f2c…e1", "file_type": "txt", "folder_class": "documents",
     "page": null, "row": null, "column": null, "column_header": null, "ocr_confidence": null,
     "text": "[12/09/26, 10:14] Ravi: bhai mera aadhar no hai …"}
  ]
}
```
→ `200 DetectResponse`:
```json
{
  "findings": [
    {"finding_id": "fnd_1c0e…", "device_id": "LAPTOP-01", "scan_id": "scan_8b21e0d4a911",
     "file_path": "/Users/demo/demo_folder/Documents/whatsapp_chat_support.txt",
     "file_hash": "9f2c…e1", "folder_class": "documents", "file_type": "txt",
     "location": "chunk=0;char=41", "finding_kind": "item", "category": "personal",
     "pii_type": "AADHAAR", "doc_type": "chat_export", "masked_value": "XXXXXXXX4821",
     "value_hash": "b7e1…", "holder": "individual", "masked_in_source": false,
     "confidence": 0.93, "decided_by": "server",
     "reason": "Hinglish cue 'mera aadhar' next to a checksum-valid 12-digit number",
     "llm_suggested_tier": "confidential", "sensitivity_tier": "confidential",
     "tier_reason": "AADHAAR item tier (1 in file, bulk threshold 10)",
     "policy_version": "2026-09-26.1", "risk_score": 48.0, "risk_band": "medium",
     "detected_at": "2026-09-26T09:31:12Z"}
  ],
  "pending": 0,
  "stats": {"files": 2, "chunks": 1, "candidates": 3, "confirmed_by_rules": 0,
            "verified_by_server": 1, "dropped": 2, "pending": 0, "llm_calls": 2, "latency_ms": 2140}
}
```
Server rules:
- Runs `pipeline.detect(mode="server")`, **never stores chunk text**, and upserts findings by `finding_id`, so resending a batch is safe.
- Copies `scan_id` onto each finding.
- Adds the batch's `status="ok"` file count to `devices.files_scanned`.
- Inserts `actions` rows (`suggested`) from `suggested_actions(tier)`, to be used in 1B.
- If the LLM is unavailable, uncertain candidates are counted in `pending` and still get `200`.

Errors: `413 BATCH_TOO_LARGE` · `422` validation.

Agent: stores every returned finding in local `findings`, marks the batch's files `done` or `unscannable`, and adds the findings count to the scan.

### 3.6 `POST /scans/{scan_id}/progress`  *(new)*
Body `ScanProgress` (**cumulative** counts; the server overwrites):
```json
{"files_discovered": 312, "files_done": 140, "files_unscannable": 3,
 "files_skipped": 20, "files_failed": 1, "findings": 57, "crawl_complete": false}
```
→ `200 {"ok": true}` · `404 SCAN_NOT_FOUND` · `409 SCAN_CLOSED` (already completed, failed or rejected; the agent stops reporting)

The agent sends it every 5 s while a server-issued scan is `running`. It is never sent for `local-*` scans.

### 3.7 `POST /scans/{scan_id}/complete`  *(new)*
Sent once `crawl_complete` is true and no files are left in `discovered` or `processing`. Body `ScanComplete`:
```json
{"status": "completed", "files_discovered": 312, "files_done": 285, "files_unscannable": 5,
 "files_skipped": 20, "files_failed": 2, "findings": 131, "error": null, "duration_ms": 94210}
```
→ `200 {"ok": true}` (idempotent) · `404 SCAN_NOT_FOUND`

Use `status="failed"` only if the crawl itself failed. Individual failed files still mean `completed`.

### 3.8 `POST /heartbeat`
Body `HeartbeatRequest`: `{"device_id": "LAPTOP-01", "files_scanned": 305, "agent_version": "0.1.0"}` → `200 {"ok": true}`

The agent sends it every 30 s and once when a scan finishes. The server upserts the device, sets `last_seen`, `agent_version`, and `files_scanned = max(current, reported)`.

---

## 4. Admin endpoints (Phase 1A)

| Method & path | Request → Response | Notes |
|---|---|---|
| `POST /admin/scans` *(new)* | `CreateScanRequest` → `201 CreateScanResponse` | Creates a scan plus a SCAN command. `404 DEVICE_UNKNOWN` if the device has never polled. |
| `GET /admin/scans?device_id=&limit=50` *(new)* | → `list[ScanView]` | Newest first |
| `GET /admin/scans/{scan_id}` *(new)* | → `ScanView` | `findings_by_tier` computed from `findings` |
| `GET /admin/devices` | → `list[DeviceView]` | `online` = last_seen within 30 s |
| `GET /admin/findings?device_id=&scan_id=&tier=&category=&type=&folder=&limit=100&offset=0` | → `list[Finding]` | Sorted by `risk_score` desc. `scan_id` filter is new. |
| `GET /admin/summary` | → object below | |
| `GET /admin/pipeline-stats` | → object below | From `traces` + `findings` |
| `GET /admin/eval` | → contents of `eval/results.json` | T9. `404 EVAL_NOT_RUN` before then. |

`/admin/summary`:
```json
{"devices": 1, "files_scanned": 305, "findings_total": 131,
 "by_tier": {"restricted": 4, "confidential": 61, "internal": 66, "public": 0},
 "by_category": {"personal": 118, "business": 11, "secret": 2},
 "by_type": {"AADHAAR": 44, "PAN_INDIVIDUAL": 40, "MOBILE_IN": 30},
 "by_device": {"LAPTOP-01": 131},
 "top_risky_files": [{"device_id": "LAPTOP-01", "file_path": ".../customer_export_aug.csv",
                      "sensitivity_tier": "restricted", "max_risk_score": 100.0,
                      "risk_band": "high", "findings": 120}]}
```
`/admin/pipeline-stats`:
```json
{"findings_by_decided_by": {"rules": 90, "server": 41, "local_model": 0},
 "llm_calls": {"verify": 12, "classify": 30, "ocr": 4}, "key_rotations": 3,
 "p50_latency_ms": {"verify": 900, "classify": 700, "ocr": 2100}, "est_cost_usd": 0.04}
```

---

## 5. Implemented by the server in 1A but not called by the agent

| Endpoint | Body → Response | Used in |
|---|---|---|
| `GET /policy` | → `PolicyDoc` | Phase 2 |
| `POST /verify` | `list[VerifyItem]` → `list[VerifyVerdict]` | Phase 2 |
| `POST /findings` | `list[Finding]` → `{"accepted": n}` (idempotent upsert) | Phase 2 |

**Reserved for 1B (don't implement in 1A):** `GET /actions`, `POST /actions/{id}/approve`, `POST /action-results`, `GET /admin/audit`, and command type `RUN_ACTIONS`.

---

## 6. Status machines

**Server scan status:**

```
queued ──poll──▶ delivered ──ack accepted──▶ running ──complete(completed)──▶ completed
                     │                          └──complete(failed)──────────▶ failed
                     └──ack rejected──▶ rejected
delivered with no ack for 60 s ──▶ redelivered on next poll (status stays delivered)
```

**Agent file status:**

```
discovered ──claim──▶ processing ──/detect 200──▶ done | unscannable
     │                     └──retryable error──▶ discovered (attempts+1); after 3 attempts → failed
     └──(force=false and same path+hash was done before)──▶ skipped (status_reason='unchanged')
Unscannable files still go to /detect as FileMeta with no chunks → then marked unscannable.
```

---

## 7. Field rules both sides rely on

### 7.1 `file_type`
Lowercase extension without the dot; `tif` → `tiff`. Dotfiles with no extension use the name (`.env` → `env`). See `file_type_for()`. The agent crawls only `include_types`.

### 7.2 `folder_class`
`classify_folder(path)` in `contracts.py`. First match wins:

| Class | Matches |
|---|---|
| `synced` | Path contains onedrive, dropbox, google drive, icloud, clouddocs, box sync |
| `shared` | UNC `//` paths, `/Volumes/`, or a folder named `shared` or `public` |
| `downloads` | A folder named `Downloads` |
| `desktop` | A folder named `Desktop` |
| `documents` | A folder named `Documents` |
| `other` | Anything else |

These keys match `policy.yaml → exposure`.

### 7.3 Chunking (agent)

| Source | How to chunk | `page` | `row` / `column` / `column_header` |
|---|---|---|---|
| Text types, `.eml` | Whole file as UTF-8 (`errors="replace"`), ≤ 4,000 chars per chunk, 150-char overlap | null | null |
| `.docx` | Paragraphs, then tables (cells joined with ` \| `, one row per line); chunked like text | null | null |
| `.pdf` page with text (≥ 20 chars) | Per page, chunked like text | page no. | null |
| `.pdf` page with < 20 chars | Render at 150 dpi to PNG → `/ocr` → chunk the text, set `ocr_confidence`. Max 10 OCR pages per PDF, then `status_reason="ocr_page_cap"` | page no. | null |
| `.png/.jpg/.jpeg/.tiff` | `/ocr` → chunk the text, set `ocr_confidence` | 1 | null |
| `.csv/.tsv/.xlsx` | **Column chunks.** Row 1 is the header. For each block of 50 data rows × each column: `text` = that column's cell values, **one per line** (an empty cell is an empty line). Max 5,000 data rows, then `status_reason="truncated"` | sheet index (csv/tsv = 1) | `row` = spreadsheet row number of the first line (the first block starts at 2), `column` = 1-based column index, `column_header` = header text |

- **Per-file cap:** 1,000 chunks, then `status_reason="truncated"`.
- `chunk_id = chunk_id_for(file_hash, i)`, where `i` counts from 0 across the whole file.
- **Unscannable:** encrypted, corrupt, permission-denied or over-size files → `FileMeta.status="unscannable"` with a reason and no chunks. `file_hash=""` if the file can't be read.

### 7.4 `finding_id`
`finding_id_for(device_id, file_hash, finding_kind, pii_type, value_hash)`, computed by the server.
- One **item** finding per distinct `(pii_type, value_hash)` per file, keeping the first location. Overlap duplicates collapse automatically.
- One **document** finding per file.
- Bulk counts for tiers = the number of distinct `value_hash` per `pii_type` per file.

### 7.5 `location` (server builds it; a `;`-separated `key=value` list)

| Source | Format |
|---|---|
| Text, docx | `chunk=<i>;char=<offset in chunk>` |
| PDF, image | `page=<p>;chunk=<i>;char=<offset>` (add `;ocr=1` if the text came from OCR) |
| Sheets | `sheet=<page>;row=<chunk.row + line index>;column=<c>;header=<column_header>` |
| Document finding | `document` |

### 7.6 Risk band
`high` ≥ 70 · `medium` ≥ 40 · otherwise `low` (from `policy.yaml → risk_bands`).

---

## 8. Agent internals (Person A): the architecture we agreed

One process, SQLite in WAL mode (`agent_schema.sql`), one connection per thread. **No text is ever written to SQLite.**

| Thread | Loop | Does |
|---|---|---|
| **Poller** | every `POLL_INTERVAL_SEC` (10; **2 for the demo**) | `GET commands/next` → dedupe → validate roots → ack → insert `scans(queued)` |
| **Crawler** | picks the oldest `queued` scan | `os.walk` over roots, skipping `exclude_dirs` and symlinked dirs. Filters by `include_types`, computes sha256, applies `force`/unchanged skip, and records oversize or permission-denied files as unscannable. Inserts `files(discovered)`, then sets `crawl_complete=1`. |
| **Processor** (×2) | claims ≤ 20 `discovered` files | Extracts in memory, calls `/ocr` for images and scanned pages, builds chunks, calls `POST /detect`, stores findings, marks files done/unscannable. Chunk text is dropped after the request. |
| **Reporter** | every 5 s / 30 s | `progress` for running server scans; `heartbeat` every 30 s. When a scan has `crawl_complete=1` and no `discovered`/`processing` files, it sends `complete` (skipped for `local-*`), sets the scan `completed`, and sends a heartbeat. |

Atomic claim:
```sql
UPDATE files SET status='processing', attempts=attempts+1, updated_at=?
WHERE id IN (SELECT id FROM files WHERE status='discovered' ORDER BY id LIMIT 20)
RETURNING *;
```
- **Startup recovery:** `processing` → `discovered`. A `running` scan resumes.
- **CLI:**
  - `python -m agent.main run`: the daemon (all four threads).
  - `python -m agent.main scan <roots…> [--force]`: creates a `local-<uuid>` scan and runs crawler + processor inline until done, then prints files scanned, findings by tier, pending and seconds. This is the CP1 command.
- **Env (`.env.example`):** `SERVER_URL`, `DEVICE_ID`, `DEVICE_TOKEN` (optional in 1A), `AGENT_DB=agent.db`, `POLL_INTERVAL_SEC=10`, `AGENT_VERSION=0.1.0`, plus `ORG_SALT`, `FERNET_KEY` (1B), `LOCAL_MODEL`, `LOCAL_OCR` (Phase 2).

## 9. Server internals (Person B), only what the contract depends on

- Apply `schema.sql` on startup. `uvicorn server.app:app --host 0.0.0.0 --port 8000`.
- Command delivery must be atomic: a single `UPDATE … WHERE command_id = (SELECT … LIMIT 1) RETURNING *`.
- The `ScanView` counts come from the latest progress/complete. `findings_by_tier` = `SELECT sensitivity_tier, count(*) FROM findings WHERE scan_id=? GROUP BY 1`.
- New env: `AUTH_ENABLED=false`. Existing: `GEMINI_API_KEYS`, `GEMINI_MODEL`, `DEVICE_TOKENS`, `ADMIN_TOKEN`, `ORG_SALT`, `POLICY_PATH`, `DB_PATH`, `MINUTES_PER_ALERT`.

---

## 10. curl smoke tests

```bash
S=http://localhost:8000
curl -s $S/health
# start a scan (device must have polled once)
curl -s -X POST $S/admin/scans -H 'Content-Type: application/json' \
  -d '{"device_id":"LAPTOP-01","roots":["/Users/demo/demo_folder"],"force":false}'
curl -si $S/devices/LAPTOP-01/commands/next
curl -s -X POST $S/commands/cmd_3f9a1c2b7d4e/ack -H 'Content-Type: application/json' -d '{"status":"accepted"}'
curl -s -X POST $S/ocr -F image=@kyc_scan.png -F device_id=LAPTOP-01 -F file_hash=abc -F page=1
curl -s -X POST $S/scans/scan_8b21e0d4a911/progress -H 'Content-Type: application/json' \
  -d '{"files_discovered":3,"files_done":1,"files_unscannable":0,"files_skipped":0,"files_failed":0,"findings":2,"crawl_complete":false}'
curl -s "$S/admin/findings?scan_id=scan_8b21e0d4a911&limit=5"
curl -s $S/admin/summary
```
