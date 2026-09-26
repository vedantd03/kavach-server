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
