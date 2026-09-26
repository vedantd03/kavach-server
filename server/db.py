"""SQLite access. Stores metadata only: no extracted text, snippets or raw values."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from detect_core.contracts import Finding, utc_now

SCHEMA = Path(__file__).with_name("schema.sql")
FINDING_COLS = list(Finding.model_fields)
SCAN_COUNTS = ("files_discovered", "files_done", "files_unscannable", "files_skipped",
               "files_failed", "findings")


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(seconds: float) -> str:
    return _ts(datetime.now(timezone.utc) - timedelta(seconds=seconds))


class DB:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA.read_text(encoding="utf-8"))

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, check_same_thread=False, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout = 30000")
        return con

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._local.con = self._connect()
        yield con

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised write transaction."""
        with self._write_lock, self.conn() as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        with self.conn() as con:
            return [dict(r) for r in con.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> Optional[dict[str, Any]]:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    # ------------------------------------------------------------ devices
    def touch_device(self, device_id: str, con: Optional[sqlite3.Connection] = None) -> None:
        sql = ("INSERT INTO devices(device_id, last_seen, files_scanned) VALUES (?, ?, 0) "
               "ON CONFLICT(device_id) DO UPDATE SET last_seen = excluded.last_seen")
        if con is not None:
            con.execute(sql, (device_id, utc_now()))
        else:
            with self.tx() as c:
                c.execute(sql, (device_id, utc_now()))

    def heartbeat(self, device_id: str, files_scanned: int, agent_version: Optional[str]) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO devices(device_id, last_seen, files_scanned, agent_version) VALUES (?,?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET last_seen = excluded.last_seen, "
                "agent_version = COALESCE(excluded.agent_version, devices.agent_version), "
                "files_scanned = MAX(devices.files_scanned, excluded.files_scanned)",
                (device_id, utc_now(), files_scanned, agent_version))

    def add_files_scanned(self, device_id: str, n: int) -> None:
        with self.tx() as c:
            self.touch_device(device_id, c)
            c.execute("UPDATE devices SET files_scanned = files_scanned + ? WHERE device_id = ?",
                      (n, device_id))

    # ------------------------------------------------------------ findings and actions
    def upsert_findings(self, findings: list[Finding], actions_for: Optional[Any] = None) -> int:
        """Upsert by finding_id. `actions_for(finding) -> list[str]` inserts suggested actions."""
        cols = ", ".join(FINDING_COLS)
        marks = ", ".join("?" for _ in FINDING_COLS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in FINDING_COLS if c != "finding_id")
        sql = (f"INSERT INTO findings({cols}) VALUES ({marks}) "
               f"ON CONFLICT(finding_id) DO UPDATE SET {updates}")
        now = utc_now()
        with self.tx() as c:
            for f in findings:
                d = f.model_dump()
                d["masked_in_source"] = int(d["masked_in_source"])
                c.execute(sql, [d[k] for k in FINDING_COLS])
                for action_type in (actions_for(f) if actions_for else []):
                    c.execute(
                        "INSERT OR IGNORE INTO actions(action_id, device_id, finding_id, file_path, "
                        "action_type, status, approver, created_at, updated_at) "
                        "VALUES (?,?,?,?,?, 'suggested', NULL, ?, ?)",
                        ("act_" + uuid.uuid4().hex[:16], f.device_id, f.finding_id, f.file_path,
                         action_type, now, now))
        return len(findings)

    def findings(self, where: dict[str, Any], limit: int, offset: int) -> list[dict[str, Any]]:
        clauses, args = [], []
        for col, val in where.items():
            if val is not None and val != "":
                clauses.append(f"{col} = ?")
                args.append(val)
        sql = "SELECT * FROM findings"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY risk_score DESC, detected_at DESC LIMIT ? OFFSET ?"
        rows = self.q(sql, args + [limit, offset])
        for r in rows:
            r["masked_in_source"] = bool(r["masked_in_source"])
        return rows

    # ------------------------------------------------------------ traces
    def insert_trace(self, row: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO traces(trace_id, ts, endpoint, model, key_index, latency_ms, tokens_in, "
                "tokens_out, items, decisions_json, device_id, status, attempts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("trc_" + uuid.uuid4().hex[:16], utc_now(), row.get("endpoint"), row.get("model"),
                 row.get("key_index"), row.get("latency_ms"), row.get("tokens_in"),
                 row.get("tokens_out"), row.get("items"), json.dumps(row.get("decisions") or {}),
                 row.get("device_id"), row.get("status"), row.get("attempts", 1)))

    # ------------------------------------------------------------ scans and commands
    def create_scan(self, device_id: str, payload: dict[str, Any]) -> tuple[str, str]:
        scan_id = "scan_" + uuid.uuid4().hex[:12]
        command_id = "cmd_" + uuid.uuid4().hex[:12]
        payload = {"scan_id": scan_id, **payload}
        now = utc_now()
        with self.tx() as c:
            c.execute("INSERT INTO scans(scan_id, device_id, command_id, status, roots_json, force, "
                      "created_at) VALUES (?,?,?,'queued',?,?,?)",
                      (scan_id, device_id, command_id, json.dumps(payload["roots"]),
                       int(payload.get("force", False)), now))
            c.execute("INSERT INTO commands(command_id, device_id, type, payload_json, status, "
                      "created_at) VALUES (?,?,'SCAN',?,'pending',?)",
                      (command_id, device_id, json.dumps(payload), now))
        return scan_id, command_id

    def next_command(self, device_id: str, redelivery_sec: float) -> Optional[dict[str, Any]]:
        now = utc_now()
        cutoff = ago(redelivery_sec)
        with self.tx() as c:
            self.touch_device(device_id, c)
            row = c.execute(
                "UPDATE commands SET status = 'delivered', delivered_at = ? "
                "WHERE command_id = (SELECT command_id FROM commands WHERE device_id = ? AND "
                "(status = 'pending' OR (status = 'delivered' AND delivered_at < ?)) "
                "ORDER BY created_at, rowid LIMIT 1) RETURNING *",
                (now, device_id, cutoff)).fetchone()
            if row is None:
                return None
            row = dict(row)
            payload = json.loads(row["payload_json"])
            c.execute("UPDATE scans SET status = 'delivered', delivered_at = ? "
                      "WHERE scan_id = ? AND status IN ('queued', 'delivered')",
                      (now, payload.get("scan_id")))
        return {"command_id": row["command_id"], "type": row["type"],
                "created_at": row["created_at"], "payload": payload}

    def ack_command(self, command_id: str, status: str, reason: Optional[str]) -> bool:
        now = utc_now()
        with self.tx() as c:
            row = c.execute("SELECT * FROM commands WHERE command_id = ?", (command_id,)).fetchone()
            if row is None:
                return False
            if row["status"] in ("accepted", "rejected"):
                return True  # no-op
            c.execute("UPDATE commands SET status = ?, acked_at = ?, reject_reason = ? "
                      "WHERE command_id = ?", (status, now, reason, command_id))
            scan_id = json.loads(row["payload_json"]).get("scan_id")
            if status == "accepted":
                c.execute("UPDATE scans SET status = 'running', started_at = ? WHERE scan_id = ? "
                          "AND status IN ('queued', 'delivered')", (now, scan_id))
            else:
                c.execute("UPDATE scans SET status = 'rejected', reject_reason = ?, completed_at = ? "
                          "WHERE scan_id = ? AND status IN ('queued', 'delivered')",
                          (reason, now, scan_id))
        return True

    def scan_progress(self, scan_id: str, counts: dict[str, Any]) -> str:
        """Returns 'ok', 'not_found' or 'closed'."""
        with self.tx() as c:
            row = c.execute("SELECT status FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
            if row is None:
                return "not_found"
            if row["status"] in ("completed", "failed", "rejected"):
                return "closed"
            sets = ", ".join(f"{k} = ?" for k in SCAN_COUNTS) + ", crawl_complete = ?"
            vals = [int(counts.get(k, 0)) for k in SCAN_COUNTS] + [int(bool(counts.get("crawl_complete")))]
            extra = ", status = 'running', started_at = COALESCE(started_at, ?)" \
                if row["status"] in ("queued", "delivered") else ""
            args = vals + ([utc_now()] if extra else []) + [scan_id]
            c.execute(f"UPDATE scans SET {sets}{extra} WHERE scan_id = ?", args)
        return "ok"

    def scan_complete(self, scan_id: str, body: dict[str, Any]) -> bool:
        with self.tx() as c:
            row = c.execute("SELECT status FROM scans WHERE scan_id = ?", (scan_id,)).fetchone()
            if row is None:
                return False
            if row["status"] in ("completed", "failed"):
                return True  # idempotent
            sets = ", ".join(f"{k} = ?" for k in SCAN_COUNTS)
            c.execute(f"UPDATE scans SET {sets}, status = ?, error = ?, duration_ms = ?, "
                      "crawl_complete = 1, completed_at = ? WHERE scan_id = ?",
                      [int(body.get(k, 0)) for k in SCAN_COUNTS] +
                      [body["status"], body.get("error"), body.get("duration_ms"), utc_now(), scan_id])
        return True

    def scan_view(self, row: dict[str, Any]) -> dict[str, Any]:
        tiers = {r["sensitivity_tier"]: r["n"] for r in self.q(
            "SELECT sensitivity_tier, count(*) AS n FROM findings WHERE scan_id = ? "
            "AND finding_kind = 'item' GROUP BY 1", (row["scan_id"],))}
        out = {k: row[k] for k in ("scan_id", "device_id", "command_id", "status", "created_at",
                                   "delivered_at", "started_at", "completed_at", "reject_reason",
                                   "error", "duration_ms", *SCAN_COUNTS)}
        out["roots"] = json.loads(row["roots_json"])
        out["force"] = bool(row["force"])
        out["crawl_complete"] = bool(row["crawl_complete"])
        out["findings_by_tier"] = tiers
        return out
