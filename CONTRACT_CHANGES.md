# Contract changes (server side, v1A.1)

Additions only; nothing in API.md was renamed or removed.

1. **Response shapes API.md names but does not spell out** (defined in `detect_core/contracts.py`):
   - `CreateScanResponse`: `{scan_id, command_id, status}` (`status="queued"`).
   - `ScanView`: `scan_id, device_id, command_id, status, roots, force, created_at, delivered_at,
     started_at, completed_at, reject_reason, error, files_discovered, files_done,
     files_unscannable, files_skipped, files_failed, findings, crawl_complete, duration_ms,
     findings_by_tier`.
   - `DeviceView`: `device_id, last_seen, files_scanned, agent_version, online`.
2. **`GET /policy`** `yaml_text` is `policy.yaml` with the context lexicon nested under a
   top-level `lexicon:` key, so an agent running `pipeline.detect(mode="local")` in Phase 2 gets
   the cues too. `detect_core.policy.load_policy(yaml_text)` reads both.
3. **`/admin/pipeline-stats`** adds `cache_hits`, `llm_errors` and `tokens` next to the
   documented fields.
4. **`traces`** table adds `status` (`ok|cached|error`) and `attempts`;
   `key_rotations = sum(attempts - 1)`.
5. **Suggested actions** are inserted against the per-file `document` finding (actions are
   file-level), not against each item finding.
6. **Keys**: the server reads `GEMINI_API_KEYS` (comma-separated) and falls back to
   `GEMINI_API_KEY_1..N`.
7. **Phase 3 console additions** (additive):
   - `GET /admin/audit?action_id=&event=&limit=500&offset=0` (reserved for 1B in API.md; built now
     for the audit trail). `event` is a prefix (`scan.`, `findings.`, `action.`). Rows:
     `{audit_id, ts, actor, event, action_id, finding_id, details}`; details hold ids, counts, tiers
     and file paths only.
   - Audit events written today: `scan.requested|delivered|accepted|rejected|completed|failed`,
     `findings.recorded` (per `/detect` batch, counts only), `action.suggested` (actor `policy`).
     1B adds `action.approved|rejected|done|failed`.
   - `/admin/summary` adds `devices_online`, `files_by_tier` (from document findings),
     `high_risk_files` (files with max risk >= `risk_bands.high`) and `pending_approvals`
     (actions with status `suggested`).
   - `/admin/findings` adds a `kind` filter (`item` | `document`).
8. **`GET /admin/privacy-check`** (additive, console Health page): live checks on stored data:
   masked values show at most 4 characters, no text/raw-value columns, traces hold counts only,
   no key column, LangSmith scrubbing on. Returns `{ok, checks: [{name, ok, detail, offending_ids}]}`
   with counts and ids only.
