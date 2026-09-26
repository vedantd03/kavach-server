# CLAUDE.md — kavach-server

You are building **kavach-server**, the server half of **Kavach**. This session's goal:
**Phase 1A (detection) on the server, then deploy it to Railway.** Hard code freeze
16:30 IST, 26 Sep 2026; demo at 17:30. Work in the order below and commit after each task.

## What Kavach is (60 seconds)
Kavach finds Indian personal and confidential data sitting in files on employee laptops.
The **kavach-agent** (separate repo, not in this session) discovers files on a laptop,
extracts text and sends it here. **kavach-server** decides what is sensitive:
- Rules and checksums find candidates (Aadhaar, PAN, GSTIN, UPI ID, IFSC + bank account,
  Indian mobile, email, secrets).
- A context scorer and then Gemini judge the uncertain ones: an Aadhaar vs a look-alike
  invoice number; a personal PAN vs a company PAN; Hindi/Hinglish context.
- Code assigns a **sensitivity tier** (restricted / confidential / internal / public) from
  `policy/policy.yaml`, plus a risk score and suggested actions.
Today's scope is PII + secrets. The design must extend to business documents (POs,
payroll, board papers) through policy and prompt changes only.

## Source of truth for the API
**`API.md` in this repo is the contract the agent is built against.** Implement it
exactly. If `../BUILD_PLAN.md` or anything below disagrees with `API.md`, `API.md` wins.
- Make `detect_core/detect_core/contracts.py` match `API.md`'s request/response shapes.
- If you find something `API.md` needs that it doesn't define, add it in the smallest way,
  update `API.md` in the same commit, and note it under "Contract changes" in
  `../BUILD_PLAN.md` (or `CHANGES.md` here if that file isn't present).
- Never rename or remove a field or endpoint that `API.md` defines.

## In scope for this session
T0 (this repo only) → T1 → T2 → T3 → T4 → T5 → T6 → smoke script → D1 (Railway).
Then T9 (evaluation) if time allows. Full task detail is in `../BUILD_PLAN.md` if present;
the essentials are below.

**Out of scope now**: `kavach-agent`, the portal (`portal/` stays empty except a README),
action endpoints beyond what `API.md` requires for 1A, Phase 2 local models.

## Non-negotiable rules
1. **Never persist or log a raw identifier value**: not in SQLite, traces, logs,
   exceptions, cache or eval output. Store only `masked_value` (last 4 visible) and
   `value_hash = HMAC-SHA256(ORG_SALT, normalised_value)`.
2. **Never store extracted text.** `/detect` processes chunks in memory and drops them.
   `/ocr` never stores images. No DB column holds text, snippets or raw values.
3. **Never log or persist API keys.** Traces record `key_index` only.
4. **Deterministic maths stays in code** with unit tests: Verhoeff, GSTIN mod-36, PAN
   holder type (4th char), IFSC format, thresholds, tier and risk.
5. **The LLM never sees candidate digits.** Send `[CANDIDATE type=AADHAAR len=12
   checksum=pass]` plus a ~150-char window with every other ≥4-digit run → `X` and
   emails → `[EMAIL]`.
6. **Final tier is computed by code** (`policy.py`). Store the LLM's suggested tier
   alongside for comparison.
7. **Rules before the LLM.** Only candidates in the uncertain band (0.20 < score < 0.85)
   go to Gemini, batched ≤ 15 per call. Keeps us inside free-tier limits.
8. **No hardcoded outputs.** Never special-case corpus file names or values.
9. Config via env vars only; `.env.example` committed, `.env` never.
10. `pytest -q` green before every commit. Commit message: `T<id>: <what>`.

## Layout
```
kavach-server/
  API.md                  contract (given; source of truth)
  CLAUDE.md               this file
  detect_core/            installable package "detect-core"
    pyproject.toml        deps: pydantic, pyyaml; extra llm = ["google-genai"]
    detect_core/
      contracts.py  normalise.py  recognizers.py  validators.py  masking.py
      context.py    policy.py     pipeline.py     llm.py (lazy google-genai import)
  server/
    app.py  db.py  schema.sql
  policy/  policy.yaml  lexicon.yaml
  corpus/  generate.py  files/  labels.jsonl
  eval/    run_eval.py  (T9)
  scripts/ smoke_detect.py
  portal/  README.md      ("Phase 3 — not built yet")
  deploy/  Dockerfile  start.sh
  railway.toml
  requirements.txt  .env.example  tests/
```
`detect_core` must import and run **without** `google-genai` installed (the agent uses it
that way later). Import `google.genai` only inside `llm.py` functions.

## Tasks

**T0 — Scaffold (10 min).** `git init`; layout above; `detect_core/pyproject.toml`;
`contracts.py` matching `API.md` (pydantic v2); `server/schema.sql` with tables `devices`,
`findings`, `traces`, `actions`, `audit` (no text columns); `policy/policy.yaml` and
`policy/lexicon.yaml` from `../BUILD_PLAN.md` T0 items 7–8; `.env.example`;
`tests/test_privacy.py` skeleton. Done when `pip install -r requirements.txt` and
`pip install -e ./detect_core` work and `sqlite3 :memory: < server/schema.sql` succeeds.

**T1 — Normalise, recognizers, validators, masking (18 min).** Devanagari digits → ASCII;
rejoin split digit groups; keep an offset map. Recognizers for all types listed above
(secrets: AWS `AKIA…`, GitHub `ghp_…`, PEM private keys, high-entropy values after
`key|secret|token|password`). Validators and masking per the rules. Tests: Verhoeff
pass/fail; ~8% (±1%) of 10,000 random 12-digit strings pass first-digit 2–9 + Verhoeff;
PAN type mapping; GSTIN check char; masked snippets contain no ≥4-digit run.

**T2 — Context scorer, policy, risk (12 min).** Score in [0,1] from validator prior plus
positive/negative cues within ±60 chars (English, Hindi, Hinglish from `lexicon.yaml`)
and column headers. `policy.py`: item tier with bulk rule, marking raise, final tier =
highest, `risk = min(100, 100 × weight × exposure × state × volume)`, bands, suggested
actions. Tests: 11 Aadhaar in one file → restricted; 1 → confidential; marking raises
never lowers; a negative cue drops an invoice number below 0.20.

**T3 — Gemini client (15 min).** `llm.py` with a **KeyPool** over `GEMINI_API_KEYS`
(3 keys, comma-separated): round-robin; on 429/`RESOURCE_EXHAUSTED` cool that key for 60 s
and retry on the next; if all are cooling, back off 2/4/8 s. Model from `GEMINI_MODEL`
(`gemini-3.5-flash-lite`), JSON output with pydantic schemas. Functions:
`verify_batch`, `classify_document`, `ocr`. Disk cache (`LLM_CACHE_DIR`) for
verify/classify only, never OCR. On final failure return `confidence=0,
reason="llm_unavailable"` so items become pending. Trace callback per call. Done when
`python -m detect_core.llm --selftest` sensibly judges a Hinglish Aadhaar mention, an
invoice number and a company PAN, and `key_index` rotates.

**T4 — Pipeline (8 min).** `detect(request, policy, mode, verifier, doc_classifier)` with
modes `server`, `local`, `rules_only` (baseline, no LLM). Route by thresholds from
policy; one `classify_document` per file on the first ~1,500 masked chars in `server`
mode; build findings with masked values and hashes; bulk counts per file; tiers; risk.
No file I/O inside; the policy is passed in.

**T5 — Synthetic corpus (17 min).** `corpus/generate.py`, fixed seed, valid-format
**fake** identifiers only. ~30 files, ~150 labelled items, ~40 hard negatives: customer
CSV/XLSX export (40 rows), KYC PDF, a clean and a blurry KYC PNG, Hinglish WhatsApp export
(split number, Devanagari digits), ticket CSV with UPI IDs, 5 invoice PDFs whose invoice/
order/UTR numbers pass Verhoeff, vendor bill with GSTIN + company PAN, a signature with a
business mobile, `config_backup.env` with fake AWS/GitHub keys, a "STRICTLY CONFIDENTIAL"
doc, a doc with `XXXX XXXX 4821`, 5 clean files. `labels.jsonl`: `{file, type, value,
is_sensitive, holder, expected_tier_of_file}`. Also `corpus/demo_folder.zip`.

**T6 — Server (15 min).** FastAPI implementing every endpoint `API.md` defines for Phase
1A (at minimum: `/health`, `/policy`, `/detect`, `/ocr`, `/verify`, `/findings`,
`/heartbeat`, and the `/admin/*` reads if listed). Device auth via `X-Device-Token` ∈
`DEVICE_TOKENS`; admin via `X-Admin-Token` = `ADMIN_TOKEN`. `/detect` upserts findings and
inserts `actions` rows with `status="suggested"` from the policy. `/ocr` accepts one image
≤ 5 MB. Apply `schema.sql` at startup; create parent folders for `DB_PATH` and
`LLM_CACHE_DIR`. JSON logs without bodies.

**Smoke script (5 min).** `scripts/smoke_detect.py --url $SERVER_URL --token $DEVICE_TOKEN
<files…>`: extracts text from txt/csv/pdf locally (tiny extractor, no OCR), posts one
`DetectRequest`, posts images to `/ocr` then `/detect`, and prints findings by tier. This is
how we test the deployed server before the agent exists.

**D1 — Deploy to Railway (15 min).** See below.

**T9 — Evaluation (if time).** `eval/run_eval.py`: run `rules_only` vs `server` on the
corpus; per-type precision/recall/F1, hard-negative rejection, tier confusion and
under-labelling rate, share by `decided_by`, triage hours saved per 1,000 files =
(FP_baseline − FP_pipeline) × `MINUTES_PER_ALERT` / 60, scaled. Write `eval/results.json`.

## Railway deployment (D1)
- **Build from the Dockerfile.** `railway.toml` at the repo root:
  ```toml
  [build]
  builder = "DOCKERFILE"
  dockerfilePath = "deploy/Dockerfile"

  [deploy]
  healthcheckPath = "/health"
  healthcheckTimeout = 60
  restartPolicyType = "ON_FAILURE"
  ```
- **Dockerfile**: `python:3.11-slim`; copy the repo; `pip install -r requirements.txt`
  and `pip install ./detect_core[llm]`; `CMD ["sh", "deploy/start.sh"]`.
- **start.sh**: `exec uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}"`.
  Railway injects `PORT`; never hardcode it. Leave a commented line for the Phase 3 portal.
- **Persistent storage**: attach a Railway volume mounted at `/data`. Set
  `DB_PATH=/data/kavach.db` and `LLM_CACHE_DIR=/data/cache`. The volume is mounted at
  runtime, not build time, so create folders at startup, never in the Dockerfile.
- **Variables** (set in the Railway service, never committed): `GEMINI_API_KEYS`,
  `GEMINI_MODEL=gemini-3.5-flash-lite`, `DEVICE_TOKENS`, `ADMIN_TOKEN`, `ORG_SALT`,
  `POLICY_PATH=policy/policy.yaml`, `DB_PATH`, `LLM_CACHE_DIR`, `MINUTES_PER_ALERT=2`.
- **Networking**: generate a public Railway domain; the agent's `SERVER_URL` is that
  HTTPS URL.
- **Verify**: `curl https://<domain>/health`; then
  `python scripts/smoke_detect.py --url https://<domain> --token <device-token>
  corpus/files/Documents/whatsapp_chat_support.txt corpus/files/Desktop/kyc_scan.png`.
- **If Railway blocks you past 16:15**, stop and demo against a local server. Don't burn
  build time on deployment.
- **What to say**: the Railway host stands in for a deployment in the company's own cloud;
  all demo data is synthetic.

## Env (`.env.example`)
```
GEMINI_API_KEYS=key1,key2,key3
GEMINI_MODEL=gemini-3.5-flash-lite
DEVICE_TOKENS=dev-token-1
ADMIN_TOKEN=admin-token
ORG_SALT=change-me
POLICY_PATH=policy/policy.yaml
DB_PATH=./data/kavach.db
LLM_CACHE_DIR=./data/cache
MINUTES_PER_ALERT=2
```

## Done for this session
- `pytest -q` green, including `tests/test_privacy.py` (no raw corpus value and no API key
  in the DB, cache or logs after a `/detect` run on the corpus).
- Deployed on Railway: `/health` OK; the smoke script returns tiered findings for the
  Hinglish chat and the scanned KYC image; an invoice number is correctly **not** flagged.
- `API.md` still matches the running server.
