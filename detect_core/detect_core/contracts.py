"""Kavach data contracts (pydantic v2). Single source of truth for agent <-> server models.

HTTP behaviour is specified in API.md (contract version 1A.1). Unknown fields are rejected.
Timestamps are ISO-8601 UTC strings, e.g. "2026-09-26T09:30:00Z".
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1A.1"

Tier = Literal["restricted", "confidential", "internal", "public"]
FolderClass = Literal["synced", "shared", "downloads", "desktop", "documents", "other"]
Category = Literal["personal", "business", "secret"]
Holder = Literal["individual", "business", "unknown"]
DecidedBy = Literal["rules", "local_model", "server"]

DEFAULT_INCLUDE_TYPES: list[str] = [
    "txt", "md", "log", "json", "env", "ini", "yaml", "yml", "xml", "eml",
    "csv", "tsv", "xlsx", "docx", "pdf", "png", "jpg", "jpeg", "tiff",
]
DEFAULT_EXCLUDE_DIRS: list[str] = [
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".cache",
    "Library", "AppData", ".Trash", ".pii_vault",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- detection I/O
class FileMeta(_Model):
    file_path: str
    file_hash: str
    file_type: str
    folder_class: FolderClass
    size_bytes: int
    modified_at: str
    status: Literal["ok", "unscannable"] = "ok"
    status_reason: Optional[str] = None


class Chunk(_Model):
    chunk_id: str
    file_path: str
    file_hash: str
    file_type: str
    folder_class: FolderClass
    page: Optional[int] = None
    row: Optional[int] = None
    column: Optional[int] = None
    column_header: Optional[str] = None
    ocr_confidence: Optional[float] = None
    text: str


class DetectRequest(_Model):
    device_id: str
    scan_id: Optional[str] = None
    files: list[FileMeta]
    chunks: list[Chunk]


class Candidate(_Model):
    """Internal: a recognizer hit on normalised text. Never persisted or sent over the wire."""
    type: str
    start: int
    end: int
    normalised: str
    validator_pass: bool
    validator_notes: dict[str, Any] = Field(default_factory=dict)
    masked_in_source: bool = False


class Finding(_Model):
    finding_id: str
    device_id: str
    scan_id: Optional[str] = None
    file_path: str
    file_hash: str
    folder_class: FolderClass
    file_type: str
    location: str
    finding_kind: Literal["item", "document"]
    category: Category
    pii_type: Optional[str] = None
    doc_type: str
    masked_value: Optional[str] = None
    value_hash: Optional[str] = None
    holder: Holder
    masked_in_source: bool
    confidence: float
    decided_by: DecidedBy
    reason: str
    llm_suggested_tier: Optional[Tier] = None
    sensitivity_tier: Tier
    tier_reason: str
    policy_version: str
    risk_score: float
    risk_band: Literal["high", "medium", "low"]
    detected_at: str


class DetectResponse(_Model):
    findings: list[Finding]
    pending: int
    stats: dict[str, Any]


class VerifyItem(_Model):
    candidate_id: str
    candidate_token: str
    snippet_masked: str
    doc_type_hint: Optional[str] = None
    column_header: Optional[str] = None


class VerifyVerdict(_Model):
    candidate_id: str
    is_sensitive: bool
    type: str
    category: Category
    individual_or_business: Holder
    confidence: float
    reason: str


class DocView(_Model):
    doc_type: str
    markings: list[str] = Field(default_factory=list)
    suggested_tier: Optional[Tier] = None
    confidence: float
    reason: str


class OcrResult(_Model):
    text: str
    confidence: Optional[float] = None
    pages: int = 1


class PolicyDoc(_Model):
    version: str
    yaml_text: str


# ---------------------------------------------------------------- actions (1B, reserved)
class Action(_Model):
    action_id: str
    device_id: str
    finding_id: str
    file_path: str
    action_type: Literal["masked_copy", "quarantine", "encrypt", "suggest_delete"]
    status: Literal["suggested", "approved", "rejected", "done", "failed"]
    approver: Optional[str] = None
    created_at: str
    updated_at: str


class ActionResult(_Model):
    action_id: str
    status: Literal["done", "failed"]
    new_path: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------- commands and scans
class ScanPayload(_Model):
    scan_id: str
    roots: list[str]
    force: bool = False
    include_types: list[str] = Field(default_factory=lambda: list(DEFAULT_INCLUDE_TYPES))
    exclude_dirs: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_DIRS))
    max_file_mb: int = 50


class Command(_Model):
    command_id: str
    type: str  # "SCAN" in 1A; agent rejects anything else with UNSUPPORTED_COMMAND
    created_at: str
    payload: ScanPayload


class CommandAck(_Model):
    status: Literal["accepted", "rejected"]
    reason: Optional[str] = None


class ScanProgress(_Model):
    files_discovered: int = 0
    files_done: int = 0
    files_unscannable: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    findings: int = 0
    crawl_complete: bool = False


class ScanComplete(_Model):
    status: Literal["completed", "failed"]
    files_discovered: int = 0
    files_done: int = 0
    files_unscannable: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    findings: int = 0
    error: Optional[str] = None
    duration_ms: Optional[int] = None


class HeartbeatRequest(_Model):
    device_id: str
    files_scanned: int = 0
    agent_version: Optional[str] = None


class CreateScanRequest(_Model):
    device_id: str
    roots: list[str]
    force: bool = False
    include_types: Optional[list[str]] = None
    exclude_dirs: Optional[list[str]] = None
    max_file_mb: int = 50


class CreateScanResponse(_Model):
    scan_id: str
    command_id: str
    status: str


ScanStatus = Literal["queued", "delivered", "running", "completed", "failed", "rejected"]


class ScanView(_Model):
    scan_id: str
    device_id: str
    command_id: Optional[str] = None
    status: ScanStatus
    roots: list[str]
    force: bool
    created_at: str
    delivered_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    reject_reason: Optional[str] = None
    error: Optional[str] = None
    files_discovered: int = 0
    files_done: int = 0
    files_unscannable: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    findings: int = 0
    crawl_complete: bool = False
    duration_ms: Optional[int] = None
    findings_by_tier: dict[str, int] = Field(default_factory=dict)


class DeviceView(_Model):
    device_id: str
    last_seen: Optional[str] = None
    files_scanned: int = 0
    agent_version: Optional[str] = None
    online: bool = False


# ---------------------------------------------------------------- shared helpers (API.md §7)
_SYNCED_MARKERS = ("onedrive", "dropbox", "google drive", "icloud", "clouddocs", "box sync")


def file_type_for(path: str) -> str:
    """Lowercase extension without the dot; tif -> tiff; dotfiles use their name (.env -> env)."""
    name = PurePosixPath(path.replace("\\", "/")).name.lower()
    if name.startswith(".") and name.count(".") == 1:
        ext = name[1:]
    elif "." in name:
        ext = name.rsplit(".", 1)[1]
    else:
        ext = ""
    return "tiff" if ext == "tif" else ext


def classify_folder(path: str) -> FolderClass:
    """First match wins: synced, shared, downloads, desktop, documents, other."""
    p = path.replace("\\", "/")
    low = p.lower()
    parts = [s for s in low.split("/") if s]
    if any(m in low for m in _SYNCED_MARKERS):
        return "synced"
    if p.startswith("//") or low.startswith("/volumes/") or "shared" in parts or "public" in parts:
        return "shared"
    for name, cls in (("downloads", "downloads"), ("desktop", "desktop"), ("documents", "documents")):
        if name in parts:
            return cls  # type: ignore[return-value]
    return "other"


def chunk_id_for(file_hash: str, i: int) -> str:
    return f"{file_hash[:16]}:{i}"


def finding_id_for(device_id: str, file_hash: str, finding_kind: str,
                   pii_type: Optional[str], value_hash: Optional[str]) -> str:
    key = "|".join([device_id, file_hash, finding_kind, pii_type or "", value_hash or ""])
    return "fnd_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

