#!/usr/bin/env python3
"""Herdr Phalanx Agent Bus CLI.

Independent local N:N message queue. Producers enqueue Messages, TUI Workers
compete for them under lease, results route back by `caller_id` and a stable
`correlation_id` (Message id). See PRD #22 and `docs/adr/0002-lease-based-multi-writer-safety.md`.

Environment:
  AGENT_BUS_DB          override database path (default ~/.herdr-phalanx/agent-bus.db)
  AGENT_BUS_ARTIFACTS   override raw report directory
                        (default ~/.herdr-phalanx/runs/agent-bus)
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import sqlite3
import string
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DB = Path.home() / ".herdr-phalanx" / "agent-bus.db"
DEFAULT_ARTIFACTS = Path.home() / ".herdr-phalanx" / "runs" / "agent-bus"
BUSY_RETRY_DELAYS_MS = (50, 100, 200, 400, 800)
CALLBACK_BACKOFF_SECONDS = (5, 15, 60, 300)
STATUS_VALUES = ("pending", "leased", "succeeded", "dead", "cancelled", "archived")
CALLBACK_STATUS_VALUES = ("pending", "delivered", "failed", "dead")
ROUTE_ENABLED_VALUES = (0, 1)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_plus(seconds: int) -> str:
    return (datetime.now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _gen_lease_id() -> str:
    # 32-char URL-safe token: large enough to be unguessable for our local trust model.
    alphabet = string.ascii_letters + string.digits
    return "lease_" + "".join(random.SystemRandom().choice(alphabet) for _ in range(32))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _db_path() -> Path:
    env = os.environ.get("AGENT_BUS_DB")
    if env:
        return Path(env)
    return DEFAULT_DB


def _artifacts_root() -> Path:
    env = os.environ.get("AGENT_BUS_ARTIFACTS")
    if env:
        return Path(env)
    return DEFAULT_ARTIFACTS


def _schema_path() -> Path:
    return Path(__file__).parent / "agent_bus_schema.sql"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _with_busy_retry(func):
    """Run a writer function with bounded SQLite-busy retry."""
    import time as _time
    delay_index = 0
    while True:
        try:
            return func()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            if delay_index >= len(BUSY_RETRY_DELAYS_MS):
                raise
            delay_s = BUSY_RETRY_DELAYS_MS[delay_index] / 1000
            delay_index += 1
            _time.sleep(delay_s)


def _out(data, table: bool = False):
    if table and isinstance(data, list) and data and isinstance(data[0], dict):
        keys = list(data[0].keys())
        widths = {}
        for k in keys:
            lens = [len(k)] + [len(str(r.get(k, ""))) for r in data]
            widths[k] = max(lens) if lens else 0
        print(" | ".join(k.ljust(widths[k]) for k in keys))
        print("-+-".join("-" * widths[k] for k in keys))
        for r in data:
            print(" | ".join(str(r.get(k, "")).ljust(widths[k]) for k in keys))
        return
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _row_to_dict(row: sqlite3.Row | None) -> dict:
    if row is None:
        return {}
    d = dict(row)
    for key in ("payload", "result", "metadata", "payload", "callback_payload", "command_template"):
        if key in d and isinstance(d[key], str) and d[key]:
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _log_event(conn, event_type, message_id=None, actor_id=None, payload=None):
    conn.execute(
        "INSERT INTO bus_events (message_id, event_type, actor_id, payload) VALUES (?,?,?,?)",
        (message_id, event_type, actor_id,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None),
    )


def _resolve_route_locked(conn, agent_kind: str, profile: str | None) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM bus_routes WHERE agent_kind=? AND profile IS ?",
        (agent_kind, profile),
    ).fetchone()


def _validate_envelope(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    instruction = payload.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("payload.instruction must be a non-empty string")
    workspace = payload.get("workspace")
    if workspace is not None:
        if not isinstance(workspace, str):
            raise ValueError("payload.workspace must be a string")
        p = Path(workspace)
        if not p.is_absolute():
            raise ValueError("payload.workspace must be an absolute path")
        if not p.exists():
            raise ValueError(f"payload.workspace path does not exist: {workspace}")
    for field in ("context", "constraints", "metadata"):
        value = payload.get(field)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"payload.{field} must be an object")
    files = payload.get("files")
    if files is not None:
        if not isinstance(files, list) or not all(isinstance(x, str) for x in files):
            raise ValueError("payload.files must be a list of strings")


def _read_payload_file(value: str) -> dict:
    path = Path(value)
    if not path.exists():
        raise ValueError(f"payload file not found: {value}")
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_raw_report(message_id: str, attempts: int, source: str | None) -> tuple[Path, str]:
    if not source:
        raise ValueError("--raw-report-file is required")
    src = Path(source)
    if not src.exists():
        raise ValueError(f"raw report not found: {source}")
    target_dir = _artifacts_root() / message_id / f"attempt-{attempts}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "raw.txt"
    shutil.copy2(src, target)
    return target, _sha256_file(target)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init_db(args):
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    schema_sql = _schema_path().read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()
    _out({"status": "ok", "db_path": str(path), "schema": str(_schema_path())})


def _now_iso(seconds: int = 0) -> str:
    return _now_plus(seconds)


def cmd_route_set(args):
    max_in_flight = int(args.max_in_flight)
    default_lease_s = int(args.default_lease_seconds)
    if max_in_flight <= 0 or default_lease_s <= 0:
        raise ValueError("max-in-flight and default-lease-seconds must be positive")

    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT agent_kind FROM bus_routes WHERE agent_kind=? AND profile IS ?",
                (args.agent_kind, args.profile),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE bus_routes
                       SET max_in_flight=?, default_lease_s=?, enabled=?, updated_at=?
                       WHERE agent_kind=? AND profile IS ?""",
                    (max_in_flight, default_lease_s, int(args.enabled), _now(),
                     args.agent_kind, args.profile),
                )
                _log_event(conn, "route_updated", actor_id=args.actor,
                           payload={"agent_kind": args.agent_kind, "profile": args.profile})
            else:
                conn.execute(
                    """INSERT INTO bus_routes (agent_kind, profile, max_in_flight, default_lease_s, enabled)
                       VALUES (?,?,?,?,?)""",
                    (args.agent_kind, args.profile, max_in_flight, default_lease_s, int(args.enabled)),
                )
                _log_event(conn, "route_created", actor_id=args.actor,
                           payload={"agent_kind": args.agent_kind, "profile": args.profile})
            conn.commit()
            row = conn.execute(
                "SELECT * FROM bus_routes WHERE agent_kind=? AND profile IS ?",
                (args.agent_kind, args.profile),
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_route_status(args):
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM bus_route_status ORDER BY agent_kind, profile").fetchall()
        _out([_row_to_dict(r) for r in rows], table=args.table)
    finally:
        conn.close()


def cmd_route_delete(args):
    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM bus_routes WHERE agent_kind=? AND profile IS ?",
                (args.agent_kind, args.profile),
            ).fetchone()
            if not row:
                raise ValueError(f"route not found: {args.agent_kind}/{args.profile}")
            blocking = conn.execute(
                """SELECT COUNT(*) AS c FROM bus_messages
                   WHERE agent_kind=? AND profile IS ? AND status IN ('pending','leased')""",
                (args.agent_kind, args.profile),
            ).fetchone()
            if blocking["c"] > 0:
                raise ValueError("route has pending or leased messages; cannot delete")
            conn.execute(
                "DELETE FROM bus_routes WHERE agent_kind=? AND profile IS ?",
                (args.agent_kind, args.profile),
            )
            _log_event(conn, "route_deleted", actor_id=args.actor,
                       payload={"agent_kind": args.agent_kind, "profile": args.profile})
            conn.commit()
            return {"deleted": {"agent_kind": args.agent_kind, "profile": args.profile}}
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_worker_register(args):
    metadata = json.loads(args.metadata or "{}")

    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO bus_workers (worker_id, agent_kind, profile, agent_name, pane_id, tab_id, status, metadata, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(worker_id) DO UPDATE SET
                     agent_kind=excluded.agent_kind,
                     profile=excluded.profile,
                     agent_name=excluded.agent_name,
                     pane_id=excluded.pane_id,
                     tab_id=excluded.tab_id,
                     status=excluded.status,
                     metadata=excluded.metadata,
                     last_seen_at=excluded.last_seen_at""",
                (args.worker_id, args.agent_kind, args.profile, args.agent_name, args.pane, args.tab,
                 args.status, json.dumps(metadata, ensure_ascii=False), _now()),
            )
            _log_event(conn, "worker_registered", actor_id=args.worker_id,
                       payload={"agent_kind": args.agent_kind, "profile": args.profile})
            conn.commit()
            row = conn.execute("SELECT * FROM bus_workers WHERE worker_id=?", (args.worker_id,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_worker_heartbeat(args):
    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM bus_workers WHERE worker_id=?", (args.worker_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"unknown worker: {args.worker_id}")
            conn.execute(
                "UPDATE bus_workers SET last_seen_at=? WHERE worker_id=?",
                (_now(), args.worker_id),
            )
            _log_event(conn, "worker_heartbeat", actor_id=args.worker_id)
            conn.commit()
            return {"worker_id": args.worker_id, "last_seen_at": _now()}
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_worker_list(args):
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM bus_workers ORDER BY last_seen_at DESC").fetchall()
        _out([_row_to_dict(r) for r in rows], table=args.table)
    finally:
        conn.close()


def cmd_enqueue(args):
    payload = _read_payload_file(args.payload_file) if args.payload_file else json.loads(args.payload)
    _validate_envelope(payload)
    priority = int(args.priority) if args.priority is not None else 5
    max_attempts = int(args.max_attempts) if args.max_attempts is not None else 3
    callback_payload = json.loads(args.callback_payload) if args.callback_payload else None
    if args.callback_name and not callback_payload:
        callback_payload = {}

    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            route = _resolve_route_locked(conn, args.agent_kind, args.profile)
            if not route:
                raise ValueError(f"route not registered: {args.agent_kind}/{args.profile}")
            if not route["enabled"]:
                raise ValueError("route is disabled")
            if args.callback_name:
                cb = conn.execute(
                    "SELECT 1 FROM bus_callbacks WHERE name=? AND enabled=1",
                    (args.callback_name,),
                ).fetchone()
                if not cb:
                    raise ValueError(f"callback not registered or disabled: {args.callback_name}")
            message_id = _gen_id("msg")
            conn.execute(
                """INSERT INTO bus_messages
                   (id, caller_id, agent_kind, profile, status, priority, payload,
                    attempts, max_attempts, callback_name, callback_payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (message_id, args.caller, args.agent_kind, args.profile, "pending", priority,
                 json.dumps(payload, ensure_ascii=False), 0, max_attempts,
                 args.callback_name,
                 json.dumps(callback_payload, ensure_ascii=False) if callback_payload is not None else None),
            )
            _log_event(conn, "message_enqueued", message_id=message_id, actor_id=args.caller,
                       payload={"agent_kind": args.agent_kind, "profile": args.profile, "priority": priority})
            conn.commit()
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (message_id,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_claim(args):
    lease_seconds = int(args.lease_seconds) if args.lease_seconds is not None else 300

    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            route = _resolve_route_locked(conn, args.agent_kind, args.profile)
            if not route:
                raise ValueError(f"route not registered: {args.agent_kind}/{args.profile}")
            active = conn.execute(
                """SELECT COUNT(*) AS c FROM bus_messages
                   WHERE agent_kind=? AND profile IS ? AND status='leased' AND lease_until > ?""",
                (args.agent_kind, args.profile, _now()),
            ).fetchone()
            if active["c"] >= route["max_in_flight"]:
                return None
            candidate = conn.execute(
                """SELECT id FROM bus_messages
                   WHERE agent_kind=? AND profile IS ?
                     AND status='pending'
                     AND cancel_requested=0
                   ORDER BY priority DESC, created_at ASC
                   LIMIT 1""",
                (args.agent_kind, args.profile),
            ).fetchone()
            if not candidate:
                return None
            lease_id = _gen_lease_id()
            lease_until = _now_plus(lease_seconds)
            conn.execute(
                """UPDATE bus_messages
                   SET status='leased',
                       worker_id=?,
                       lease_id=?,
                       lease_until=?,
                       attempts=attempts+1,
                       updated_at=?
                   WHERE id=? AND status='pending'""",
                (args.worker_id, lease_id, lease_until, _now(), candidate["id"]),
            )
            if conn.total_changes == 0:
                return None
            _log_event(conn, "message_claimed", message_id=candidate["id"], actor_id=args.worker_id,
                       payload={"lease_id": lease_id, "lease_until": lease_until})
            conn.commit()
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (candidate["id"],)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    result = _with_busy_retry(writer)
    _out(result)


def cmd_heartbeat(args):
    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM bus_messages WHERE id=?", (args.id,),
            ).fetchone()
            if not row:
                raise ValueError(f"message not found: {args.id}")
            if row["status"] != "leased":
                raise ValueError(f"message status must be leased, got: {row['status']}")
            if row["worker_id"] != args.worker_id or row["lease_id"] != args.lease:
                raise ValueError("worker_id or lease_id does not match active lease")
            if row["cancel_requested"]:
                raise ValueError("cancel_requested: please finalise via cancelled")
            new_until = _now_plus(int(args.lease_seconds) if args.lease_seconds else 300)
            conn.execute(
                "UPDATE bus_messages SET lease_until=?, updated_at=? WHERE id=?",
                (new_until, _now(), args.id),
            )
            _log_event(conn, "message_heartbeat", message_id=args.id, actor_id=args.worker_id,
                       payload={"lease_until": new_until})
            conn.commit()
            return {"id": args.id, "lease_until": new_until}
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def _finalize_attempt(args, *, terminal_status: str, event_type: str, reason: str | None,
                         result_required: bool, require_cancel_requested: bool = False) -> dict:
    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
            if not row:
                raise ValueError(f"message not found: {args.id}")
            if row["status"] != "leased":
                # Either not leased (reaped/dead/etc) or the lease has already
                # been replaced. Either way: late_result, never overwrite state.
                target_path, sha = _copy_raw_report(row["id"], row["attempts"], args.raw_report_file)
                _log_event(conn, "late_result", message_id=row["id"], actor_id=args.worker_id,
                           payload={"attempted_event_type": event_type,
                                    "observed_status": row["status"],
                                    "raw_report_sha256": sha,
                                    "raw_report_path": str(target_path)})
                conn.commit()
                raise ValueError(f"late result: message status is {row['status']}, not leased")
            if row["worker_id"] != args.worker_id or row["lease_id"] != args.lease:
                # Different worker now holds the active lease. The caller's
                # claim is stale. Record the attempt and refuse.
                target_path, sha = _copy_raw_report(row["id"], row["attempts"], args.raw_report_file)
                _log_event(conn, "late_result", message_id=row["id"], actor_id=args.worker_id,
                           payload={"attempted_event_type": event_type,
                                    "observed_worker": row["worker_id"],
                                    "raw_report_sha256": sha,
                                    "raw_report_path": str(target_path)})
                conn.commit()
                raise ValueError("late result: active lease is held by another worker")
            if require_cancel_requested and not row["cancel_requested"]:
                raise ValueError("cancel_requested not set: cannot finalise as cancelled")
            attempts = row["attempts"]
            target_path, sha = _copy_raw_report(row["id"], attempts, args.raw_report_file)
            if result_required:
                if not getattr(args, "result_file", None):
                    raise ValueError("--result-file is required for complete")
                result_payload = json.loads(Path(args.result_file).read_text(encoding="utf-8"))
            else:
                result_payload = {"outcome": terminal_status, "reason": reason or ""}
            next_status = terminal_status
            if event_type == "message_failed":
                next_status = "pending" if attempts < row["max_attempts"] else "dead"
            conn.execute(
                """UPDATE bus_messages
                   SET status=?,
                       result=?,
                       raw_report_path=?,
                       raw_report_sha256=?,
                       worker_id=NULL,
                       lease_id=NULL,
                       lease_until=NULL,
                       completed_at=?,
                       updated_at=?
                   WHERE id=? AND status='leased'""",
                (next_status,
                 json.dumps(result_payload, ensure_ascii=False),
                 str(target_path),
                 sha,
                 _now(), _now(),
                 args.id),
            )
            callback_id = None
            if next_status == "succeeded" and row["callback_name"]:
                callback_id = _gen_id("cb")
                conn.execute(
                    """INSERT INTO callback_deliveries (id, message_id, callback_name)
                       VALUES (?,?,?)""",
                    (callback_id, row["id"], row["callback_name"]),
                )
            _log_event(conn, event_type, message_id=row["id"], actor_id=args.worker_id,
                       payload={"reason": reason, "attempts": attempts,
                               "raw_report_sha256": sha,
                               "callback_id": callback_id,
                               "terminal_status": next_status})
            conn.commit()
            updated = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
            return _row_to_dict(updated)
        finally:
            conn.close()

    return _with_busy_retry(writer)


def cmd_complete(args):
    if not args.result_file:
        raise ValueError("--result-file is required for complete")
    _out(_finalize_attempt(args, terminal_status="succeeded", event_type="message_completed",
                            reason=None, result_required=True))


def cmd_fail(args):
    _out(_finalize_attempt(args, terminal_status="dead", event_type="message_failed",
                            reason=args.reason, result_required=False))


def cmd_cancelled(args):
    _out(_finalize_attempt(args, terminal_status="cancelled", event_type="message_cancelled",
                            reason=args.reason, result_required=False,
                            require_cancel_requested=True))


def cmd_cancel(args):
    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
            if not row:
                raise ValueError(f"message not found: {args.id}")
            if row["caller_id"] != args.caller:
                raise ValueError("only the originating caller can cancel this message")
            if row["status"] in ("succeeded", "dead", "cancelled", "archived"):
                raise ValueError(f"cannot cancel terminal message in status: {row['status']}")
            if row["status"] == "pending":
                conn.execute(
                    """UPDATE bus_messages SET status='cancelled', completed_at=?, updated_at=?
                       WHERE id=?""",
                    (_now(), _now(), args.id),
                )
                _log_event(conn, "message_cancelled_by_producer", message_id=args.id, actor_id=args.caller)
            elif row["status"] == "leased":
                conn.execute(
                    "UPDATE bus_messages SET cancel_requested=1, updated_at=? WHERE id=?",
                    (_now(), args.id),
                )
                _log_event(conn, "message_cancel_requested", message_id=args.id, actor_id=args.caller)
            conn.commit()
            updated = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
            return _row_to_dict(updated)
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_requeue(args):
    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
            if not row:
                raise ValueError(f"message not found: {args.id}")
            if row["caller_id"] != args.caller:
                raise ValueError("only the originating caller can requeue this message")
            if row["status"] != "dead":
                raise ValueError(f"requeue requires status dead, got: {row['status']}")
            route = _resolve_route_locked(conn, row["agent_kind"], row["profile"])
            if not route:
                raise ValueError("route no longer registered; cannot requeue")
            conn.execute(
                """UPDATE bus_messages
                   SET status='pending', worker_id=NULL, lease_id=NULL, lease_until=NULL,
                       cancel_requested=0, attempts=0, updated_at=?
                   WHERE id=?""",
                (_now(), args.id),
            )
            _log_event(conn, "message_requeued", message_id=args.id, actor_id=args.caller)
            conn.commit()
            updated = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
            return _row_to_dict(updated)
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_reap(args):
    once = args.once
    drained = False
    total = 0
    while True:
        def writer():
            conn = _connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """SELECT * FROM bus_messages
                       WHERE status='leased' AND lease_until IS NOT NULL AND lease_until < ?
                       ORDER BY lease_until ASC LIMIT 1""",
                    (_now(),),
                ).fetchone()
                if not row:
                    return None
                if row["attempts"] >= row["max_attempts"]:
                    new_status = "dead"
                else:
                    new_status = "pending"
                conn.execute(
                    """UPDATE bus_messages
                       SET status=?, worker_id=NULL, lease_id=NULL, lease_until=NULL, updated_at=?
                       WHERE id=? AND status='leased'""",
                    (new_status, _now(), row["id"]),
                )
                _log_event(conn, "lease_expired", message_id=row["id"], actor_id=args.actor,
                           payload={"attempts": row["attempts"], "max_attempts": row["max_attempts"],
                                    "new_status": new_status, "previous_worker": row["worker_id"]})
                conn.commit()
                return {"id": row["id"], "new_status": new_status}
            finally:
                conn.close()

        result = _with_busy_retry(writer)
        if result is None:
            break
        total += 1
        if once:
            break
    _out({"reaped": total})


def _terminal_event_ids_for_caller(conn, caller_id: str, after_event: int) -> tuple[list[sqlite3.Row], int]:
    rows = conn.execute(
        """SELECT e.* FROM bus_events e
           JOIN bus_messages m ON m.id = e.message_id
           WHERE m.caller_id = ?
             AND e.id > ?
             AND e.event_type IN ('message_completed','message_cancelled','message_failed','lease_expired','message_requeued','message_cancelled_by_producer','late_result')
           ORDER BY e.id ASC""",
        (caller_id, after_event),
    ).fetchall()
    next_cursor = rows[-1]["id"] if rows else after_event
    return rows, next_cursor


def cmd_result_list(args):
    conn = _connect()
    try:
        events, next_cursor = _terminal_event_ids_for_caller(conn, args.caller, int(args.after_event))
        message_ids = []
        by_id = {}
        for e in events:
            mid = e["message_id"]
            if mid in by_id:
                continue
            by_id[mid] = e["event_type"]
            message_ids.append(mid)
        if not message_ids:
            _out({"events": [], "messages": [], "next_cursor": next_cursor})
            return
        placeholders = ",".join("?" * len(message_ids))
        rows = conn.execute(
            f"SELECT * FROM bus_messages WHERE id IN ({placeholders}) AND result_acked_at IS NULL",
            message_ids,
        ).fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            d["last_event"] = by_id.get(r["id"])
            out.append(d)
        _out({"events": [dict(e) for e in events], "messages": out, "next_cursor": next_cursor})
    finally:
        conn.close()


def cmd_result_get(args):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
        if not row:
            raise ValueError(f"message not found: {args.id}")
        if row["caller_id"] != args.caller:
            raise ValueError("only the originating caller can read this result")
        _out(_row_to_dict(row))
    finally:
        conn.close()


def cmd_result_ack(args):
    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
            if not row:
                raise ValueError(f"message not found: {args.id}")
            if row["caller_id"] != args.caller:
                raise ValueError("only the originating caller can ack this message")
            if row["result_acked_at"]:
                return _row_to_dict(row)
            conn.execute(
                "UPDATE bus_messages SET result_acked_at=?, updated_at=? WHERE id=?",
                (_now(), _now(), args.id),
            )
            _log_event(conn, "result_acked", message_id=args.id, actor_id=args.caller)
            conn.commit()
            updated = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
            return _row_to_dict(updated)
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_result_history(args):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (args.id,)).fetchone()
        if not row:
            raise ValueError(f"message not found: {args.id}")
        if row["caller_id"] != args.caller:
            raise ValueError("only the originating caller can read this history")
        events = conn.execute(
            "SELECT * FROM bus_events WHERE message_id=? ORDER BY id ASC",
            (args.id,),
        ).fetchall()
        deliveries = conn.execute(
            "SELECT * FROM callback_deliveries WHERE message_id=? ORDER BY created_at ASC",
            (args.id,),
        ).fetchall()
        _out({
            "message": _row_to_dict(row),
            "events": [_row_to_dict(e) for e in events],
            "callback_deliveries": [_row_to_dict(d) for d in deliveries],
        })
    finally:
        conn.close()


def cmd_callback_register(args):
    def writer():
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO bus_callbacks (name, kind, command_template, enabled)
                   VALUES (?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                     kind=excluded.kind,
                     command_template=excluded.command_template,
                     enabled=excluded.enabled,
                     updated_at=excluded.updated_at""",
                (args.name, args.kind, args.command_template, int(args.enabled)),
            )
            _log_event(conn, "callback_registered", actor_id=args.actor,
                       payload={"name": args.name, "kind": args.kind})
            conn.commit()
            row = conn.execute("SELECT * FROM bus_callbacks WHERE name=?", (args.name,)).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    _out(_with_busy_retry(writer))


def cmd_callback_status(args):
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM bus_callbacks ORDER BY name").fetchall()
        _out([_row_to_dict(r) for r in rows], table=args.table)
    finally:
        conn.close()


def _render_callback_command(template: str, delivery_row: sqlite3.Row, message_row: sqlite3.Row) -> str:
    return template.format(
        message_id=message_row["id"],
        caller_id=message_row["caller_id"],
        callback_name=delivery_row["callback_name"],
        agent_kind=message_row["agent_kind"],
        profile=message_row["profile"] or "",
        status=message_row["status"],
        result_path=str(_artifacts_root() / message_row["id"] / "result.json"),
        raw_report_path=message_row["raw_report_path"] or "",
    )


def _attempt_callback_delivery(delivery_id: str) -> dict:
    conn = _connect()
    try:
        delivery = conn.execute(
            "SELECT * FROM callback_deliveries WHERE id=?", (delivery_id,),
        ).fetchone()
        if not delivery:
            return {"id": delivery_id, "result": "missing"}
        message = conn.execute(
            "SELECT * FROM bus_messages WHERE id=?", (delivery["message_id"],),
        ).fetchone()
        callback = conn.execute(
            "SELECT * FROM bus_callbacks WHERE name=?", (delivery["callback_name"],),
        ).fetchone()
        if not callback or not callback["enabled"]:
            conn.execute(
                "UPDATE callback_deliveries SET status='dead', last_error='callback disabled or missing' WHERE id=?",
                (delivery_id,),
            )
            conn.commit()
            return {"id": delivery_id, "result": "dead", "reason": "callback disabled or missing"}
        command = _render_callback_command(callback["command_template"], delivery, message)
        try:
            completed = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
            if completed.returncode == 0:
                conn.execute(
                    "UPDATE callback_deliveries SET status='delivered', delivered_at=?, attempts=attempts+1, last_error=NULL WHERE id=?",
                    (_now(), delivery_id),
                )
                _log_event(conn, "callback_delivered", message_id=delivery["message_id"],
                           actor_id=delivery_id)
                conn.commit()
                return {"id": delivery_id, "result": "delivered", "exit_code": completed.returncode}
            err = (completed.stderr or completed.stdout or "").strip()[:2000]
            attempts = delivery["attempts"] + 1
            new_status = "dead" if attempts >= len(CALLBACK_BACKOFF_SECONDS) else "pending"
            next_delay = CALLBACK_BACKOFF_SECONDS[min(attempts - 1, len(CALLBACK_BACKOFF_SECONDS) - 1)]
            conn.execute(
                """UPDATE callback_deliveries
                   SET status=?, attempts=?, last_error=?, next_attempt_at=?
                   WHERE id=?""",
                (new_status, attempts, err, _now_plus(next_delay), delivery_id),
            )
            _log_event(conn, "callback_failed", message_id=delivery["message_id"], actor_id=delivery_id,
                       payload={"exit_code": completed.returncode, "attempts": attempts,
                                "next_status": new_status})
            conn.commit()
            return {"id": delivery_id, "result": new_status, "exit_code": completed.returncode,
                    "error": err}
        except subprocess.TimeoutExpired:
            attempts = delivery["attempts"] + 1
            new_status = "dead" if attempts >= len(CALLBACK_BACKOFF_SECONDS) else "pending"
            conn.execute(
                """UPDATE callback_deliveries
                   SET status=?, attempts=?, last_error=?, next_attempt_at=?
                   WHERE id=?""",
                (new_status, attempts, "callback timeout", _now_plus(60), delivery_id),
            )
            conn.commit()
            return {"id": delivery_id, "result": new_status, "reason": "timeout"}
    finally:
        conn.close()


def cmd_callback_deliver(args):
    once = args.once
    total = 0
    while True:
        conn = _connect()
        try:
            row = conn.execute(
                """SELECT * FROM callback_deliveries
                   WHERE status='pending' AND next_attempt_at <= ?
                   ORDER BY next_attempt_at ASC LIMIT 1""",
                (_now(),),
            ).fetchone()
        finally:
            conn.close()
        if not row:
            break
        result = _attempt_callback_delivery(row["id"])
        total += 1
        if once:
            _out({"delivered": [result]})
            return
    _out({"delivered": total})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Herdr Phalanx Agent Bus CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)

    sp = sub.add_parser("route-set")
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--max-in-flight", required=True)
    sp.add_argument("--default-lease-seconds", required=True)
    sp.add_argument("--enabled", default=1, choices=ROUTE_ENABLED_VALUES)
    sp.add_argument("--actor", default="operator")
    sp.set_defaults(func=cmd_route_set)

    sp = sub.add_parser("route-status")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_route_status)

    sp = sub.add_parser("route-delete")
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--actor", default="operator")
    sp.set_defaults(func=cmd_route_delete)

    sp = sub.add_parser("worker-register")
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--agent-name")
    sp.add_argument("--pane")
    sp.add_argument("--tab")
    sp.add_argument("--status", default="ready")
    sp.add_argument("--metadata", default="{}")
    sp.set_defaults(func=cmd_worker_register)

    sp = sub.add_parser("worker-heartbeat")
    sp.add_argument("--worker-id", required=True)
    sp.set_defaults(func=cmd_worker_heartbeat)

    sp = sub.add_parser("worker-list")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_worker_list)

    sp = sub.add_parser("enqueue")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--priority", type=int, default=5)
    sp.add_argument("--max-attempts", type=int, default=3)
    sp.add_argument("--payload", help="Inline JSON object string")
    sp.add_argument("--payload-file", help="Path to a JSON file containing the payload")
    sp.add_argument("--callback-name")
    sp.add_argument("--callback-payload", help="Inline JSON object")
    sp.set_defaults(func=cmd_enqueue)

    sp = sub.add_parser("claim")
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--lease-seconds", type=int, default=300)
    sp.set_defaults(func=cmd_claim)

    sp = sub.add_parser("heartbeat")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--lease-seconds", type=int, default=300)
    sp.set_defaults(func=cmd_heartbeat)

    sp = sub.add_parser("complete")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--result-file", required=True)
    sp.add_argument("--raw-report-file", required=True)
    sp.set_defaults(func=cmd_complete)

    sp = sub.add_parser("fail")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--raw-report-file", required=True)
    sp.set_defaults(func=cmd_fail)

    sp = sub.add_parser("cancelled")
    sp.add_argument("--id", required=True)
    sp.add_argument("--worker-id", required=True)
    sp.add_argument("--lease", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--raw-report-file", required=True)
    sp.set_defaults(func=cmd_cancelled)

    sp = sub.add_parser("cancel")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_cancel)

    sp = sub.add_parser("requeue")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_requeue)

    sp = sub.add_parser("reap")
    sp.add_argument("--once", action="store_true", default=True)
    sp.add_argument("--actor", default="operator")
    sp.set_defaults(func=cmd_reap)

    sp = sub.add_parser("result-list")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--after-event", type=int, default=0)
    sp.set_defaults(func=cmd_result_list)

    sp = sub.add_parser("result-get")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_result_get)

    sp = sub.add_parser("result-ack")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_result_ack)

    sp = sub.add_parser("result-history")
    sp.add_argument("--caller", required=True)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_result_history)

    sp = sub.add_parser("callback-register")
    sp.add_argument("--name", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--command-template", required=True)
    sp.add_argument("--enabled", default=1, choices=ROUTE_ENABLED_VALUES)
    sp.add_argument("--actor", default="operator")
    sp.set_defaults(func=cmd_callback_register)

    sp = sub.add_parser("callback-status")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_callback_status)

    sp = sub.add_parser("callback-deliver")
    sp.add_argument("--once", action="store_true", default=True)
    sp.set_defaults(func=cmd_callback_deliver)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
