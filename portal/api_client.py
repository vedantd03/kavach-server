"""HTTP client for the Kavach Console. The console reads data only over the server's API
(never the DB), so it behaves the same locally and when deployed.

Env: PORTAL_API_URL (default http://127.0.0.1:8000), ADMIN_TOKEN.
Findings are validated against the `Finding` contract, so the console cannot display a field
the contract does not define (and so cannot show anything but masked values).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from detect_core.contracts import DeviceView, Finding, ScanView


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class Api:
    def __init__(self, base_url: Optional[str] = None, admin_token: Optional[str] = None,
                 timeout: float = 15.0):
        self.base_url = (base_url or os.environ.get("PORTAL_API_URL") or "http://127.0.0.1:8000").rstrip("/")
        token = admin_token if admin_token is not None else os.environ.get("ADMIN_TOKEN", "")
        headers = {"X-Admin-Token": token} if token else {}
        self._http = httpx.Client(base_url=self.base_url, headers=headers, timeout=timeout)

    def _get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v not in (None, "", [])}
        try:
            r = self._http.get(path, params=clean)
        except httpx.HTTPError as exc:
            raise ApiError(0, "SERVER_UNREACHABLE", f"{self.base_url} did not respond ({type(exc).__name__})")
        if r.status_code != 200:
            detail = {}
            try:
                detail = r.json().get("detail") or {}
            except ValueError:
                pass
            if isinstance(detail, dict):
                raise ApiError(r.status_code, detail.get("code", "HTTP_ERROR"), detail.get("message", r.text[:200]))
            raise ApiError(r.status_code, "HTTP_ERROR", str(detail)[:200])
        return r.json()

    # ------------------------------------------------------------ endpoints (API.md §4)
    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def summary(self) -> dict[str, Any]:
        return self._get("/admin/summary")

    def devices(self) -> list[DeviceView]:
        return [DeviceView.model_validate(d) for d in self._get("/admin/devices")]

    def findings(self, device_id: Optional[str] = None, scan_id: Optional[str] = None,
                 tier: Optional[str] = None, category: Optional[str] = None,
                 type: Optional[str] = None, folder: Optional[str] = None,
                 kind: Optional[str] = None, limit: int = 1000, offset: int = 0) -> list[Finding]:
        rows = self._get("/admin/findings", device_id=device_id, scan_id=scan_id, tier=tier,
                         category=category, type=type, folder=folder, kind=kind,
                         limit=limit, offset=offset)
        return [Finding.model_validate(r) for r in rows]

    def scans(self, device_id: Optional[str] = None, limit: int = 20) -> list[ScanView]:
        return [ScanView.model_validate(s) for s in self._get("/admin/scans", device_id=device_id, limit=limit)]

    def audit(self, action_id: Optional[str] = None, event: Optional[str] = None,
              limit: int = 5000) -> list[dict[str, Any]]:
        return self._get("/admin/audit", action_id=action_id, event=event, limit=limit)

    def pipeline_stats(self) -> dict[str, Any]:
        return self._get("/admin/pipeline-stats")

    def eval(self) -> dict[str, Any]:
        return self._get("/admin/eval")
