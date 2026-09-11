"""Herdr Phalanx AgentBus Core.

A deep module that hides the SQLite persistence, lease state machine, artifact
filesystem, and callback subprocess adapter behind a small set of business-action
methods. Callers (CLI, Herdr Adapter, future bridges) cross one seam.

The interface deliberately does not leak:
  * sqlite3 connections or transactions
  * SQL serialisation
  * lease_id generation
  * atomic artifact copy + SHA-256
  * prompt construction details
  * subprocess invocation details
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import random
import shutil
import sqlite3
import string
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Module-level configuration (kept inside the seam: callers pass overrides in)
# ---------------------------------------------------------------------------

BUSY_RETRY_DELAYS_MS = (50, 100, 200, 400, 800)
CALLBACK_BACKOFF_SECONDS = (5, 15, 60, 300)
CALLBACK_TIMEOUT_SECONDS = 60
RESULT_RAW_REPORT_LIMIT = 2_000
LEASE_ID_ALPHABET = string.ascii_letters + string.digits
LEASE_ID_LENGTH = 32
MESSAGE_ID_HEX_LEN = 10
DEFAULT_LEASE_SECONDS = 300

STATUS_VALUES = ("pending", "leased", "succeeded", "dead", "cancelled", "archived")
CALLBACK_STATUS_VALUES = ("pending", "delivered", "failed", "dead")
ROUTE_ENABLED_VALUES = (0, 1)
TERMINAL_STATUSES = ("succeeded", "dead", "cancelled")
RESULT_BACKOFF_SECONDS = (5, 15, 60, 300)


# ---------------------------------------------------------------------------
# Domain results (returned by every Core action)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class DomainResult:
    """Light wrapper so callers can treat `None` from claim distinctly from
    an empty result list."""

    payload: Any


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AgentBusError(RuntimeError):
    pass


class ValidationError(AgentBusError):
    pass


class NotFoundError(AgentBusError):
    pass


class ConflictError(AgentBusError):
    pass


class PermissionError_(AgentBusError):  # intentionally avoid shadowing builtin
    pass


class EncodingError_(AgentBusError):
    pass


class MigrationError(AgentBusError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_plus(seconds: int) -> str:
    return (datetime.now() + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:MESSAGE_ID_HEX_LEN]}"


def _gen_lease_id() -> str:
    return "lease_" + "".join(random.SystemRandom().choice(LEASE_ID_ALPHABET)
                             for _ in range(LEASE_ID_LENGTH))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _require(cond: bool, exc: type[Exception], msg: str) -> None:
    if not cond:
        raise exc(msg)


def _validate_envelope(payload: Any) -> None:
    _require(isinstance(payload, dict), ValidationError, "payload must be a JSON object")
    instruction = payload.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValidationError("payload.instruction must be a non-empty string")
    workspace = payload.get("workspace")
    if workspace is not None:
        _require(isinstance(workspace, str), ValidationError, "payload.workspace must be a string")
        p = Path(workspace)
        _require(p.is_absolute(), ValidationError, "payload.workspace must be an absolute path")
        _require(p.exists(), ValidationError,
                 f"payload.workspace path does not exist: {workspace}")
    for field in ("context", "constraints", "metadata"):
        v = payload.get(field)
        if v is not None:
            _require(isinstance(v, dict), ValidationError,
                     f"payload.{field} must be an object")
    files = payload.get("files")
    if files is not None:
        _require(isinstance(files, list) and all(isinstance(x, str) for x in files),
                 ValidationError, "payload.files must be a list of strings")


def _validate_callback(executable: Any, arguments: Any) -> tuple[str, list[str]]:
    if not isinstance(executable, str) or not executable:
        raise ValidationError("callback.executable must be a non-empty string")
    exe_path = Path(executable)
    _require(exe_path.is_absolute(), ValidationError,
             f"callback.executable must be an absolute path: {executable}")
    _require(exe_path.exists(), ValidationError,
             f"callback.executable path does not exist: {executable}")
    _require(exe_path.is_file(), ValidationError,
             f"callback.executable must be a file: {executable}")
    _require(isinstance(arguments, list), ValidationError,
             "callback.arguments must be a list")
    cleaned: list[str] = []
    for i, arg in enumerate(arguments):
        _require(isinstance(arg, str), ValidationError,
                 f"callback.arguments[{i}] must be a string")
        cleaned.append(arg)
    return str(exe_path), cleaned


def _safe_template(s: str) -> str:
    return s.replace("{", "{{").replace("}", "}}")


def _render_template(template: str, substitutions: dict[str, str]) -> str:
    """Render a `{key}` placeholder template. Substituted values must already be
    escaped by the caller (these are user-controlled strings)."""

    out = template
    for key, value in substitutions.items():
        out = out.replace("{" + key + "}", value)
    return out


# ---------------------------------------------------------------------------
# Core module
# ---------------------------------------------------------------------------


class AgentBus:
    """The AgentBus core. Persistence, lease state machine, artifact handling,
    callback subprocess adapter, and prompt construction are all hidden behind
    this interface."""

    def __init__(
        self,
        database_path: Path,
        artifacts_root: Path,
        db_open: Optional[Callable[[], sqlite3.Connection]] = None,
    ) -> None:
        self._database_path = Path(database_path)
        self._artifacts_root = Path(artifacts_root)
        self._db_open = db_open or self._default_db_open
        self._clock = _now

    # ---------- read-only accessors ----------

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def artifacts_root(self) -> Path:
        return self._artifacts_root

    # ---------- configuration / setup ----------

    @staticmethod
    def default_database_path() -> Path:
        env = os.environ.get("AGENT_BUS_DB")
        if env:
            return Path(env)
        return Path.home() / ".herdr-phalanx" / "agent-bus.db"

    @staticmethod
    def default_artifacts_root() -> Path:
        env = os.environ.get("AGENT_BUS_ARTIFACTS")
        if env:
            return Path(env)
        return Path.home() / ".herdr-phalanx" / "runs" / "agent-bus"

    @classmethod
    def from_environment(cls) -> "AgentBus":
        return cls(cls.default_database_path(), cls.default_artifacts_root())

    def init_database(self, schema_sql: str) -> dict:
        """Idempotently create the schema, migrating callback registry columns
        forward if a legacy `command_template` exists. Returns a small dict
        describing the migration applied."""

        path = self._database_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._db_open()
        try:
            conn.executescript(schema_sql)
            cb_migration = self._migrate_callback_registry(conn)
            worker_migration = self._migrate_bus_workers(conn)
            conn.commit()
        finally:
            conn.close()
        return {
            "database": str(path),
            "migration": {
                "callbacks": cb_migration,
                "workers": worker_migration,
            },
        }

    def _migrate_bus_workers(self, conn: sqlite3.Connection) -> dict:
        """Add new columns to `bus_workers` if missing from legacy databases."""

        cols = {row["name"] for row in conn.execute("PRAGMA table_info(bus_workers)")}
        applied: list[str] = []
        new_columns = [
            ("workspace_id", "TEXT"),
            ("session_id", "TEXT"),
            ("roles", "TEXT"),
            ("launch_args", "TEXT"),
            ("permission_mode", "TEXT"),
            ("cwd", "TEXT"),
            ("registered_by", "TEXT DEFAULT 'operator'"),
            ("model", "TEXT"),
            ("intensity", "TEXT"),
            ("agent_version", "TEXT"),
            ("herdr_version", "TEXT"),
            ("messages_claimed", "INTEGER NOT NULL DEFAULT 0"),
            ("messages_completed", "INTEGER NOT NULL DEFAULT 0"),
            ("messages_failed", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for col_name, col_type in new_columns:
            if col_name not in cols:
                conn.execute(
                    f"ALTER TABLE bus_workers ADD COLUMN {col_name} {col_type}"
                )
                applied.append(f"add_{col_name}")
        return {"applied": applied}

    def _migrate_callback_registry(self, conn: sqlite3.Connection) -> dict:
        """Add `executable` / `arguments_json` columns to `bus_callbacks` if
        missing, and disable any callback that only has the legacy
        `command_template`. Migration never interprets the legacy string."""

        cols = {row["name"] for row in conn.execute("PRAGMA table_info(bus_callbacks)")}
        applied: list[str] = []
        if "executable" not in cols:
            conn.execute("ALTER TABLE bus_callbacks ADD COLUMN executable TEXT")
            applied.append("add_executable")
        if "arguments_json" not in cols:
            conn.execute("ALTER TABLE bus_callbacks ADD COLUMN arguments_json TEXT")
            applied.append("add_arguments_json")
        # Disable legacy shell-only callbacks so a migrated DB does not silently
        # keep executing shell templates.
        try:
            conn.execute(
                """UPDATE bus_callbacks
                   SET enabled = 0
                   WHERE executable IS NULL OR executable = ''"""
            )
            applied.append("disable_legacy_templates")
        except sqlite3.Error:
            pass
        return {"applied": applied, "callback_disabled": True}

    # ---------- connection helpers ----------

    def _default_db_open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._database_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _writer(self, action):
        """Run a state-changing action inside a `BEGIN IMMEDIATE` transaction
        with bounded busy retry. Returns the action's result."""

        import time as _time
        delay_index = 0
        while True:
            conn = self._db_open()
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = action(conn)
                conn.commit()
                return result
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise
                if delay_index >= len(BUSY_RETRY_DELAYS_MS):
                    raise
                delay_s = BUSY_RETRY_DELAYS_MS[delay_index] / 1000
                delay_index += 1
                _time.sleep(delay_s)
                continue
            finally:
                conn.close()

    def _reader(self, action):
        conn = self._db_open()
        try:
            return action(conn)
        finally:
            conn.close()

    # ---------- internal helpers ----------

    @staticmethod
    def _route_locked(conn, agent_kind: str, profile: Optional[str]):
        return conn.execute(
            "SELECT * FROM bus_routes WHERE agent_kind=? AND profile IS ?",
            (agent_kind, profile),
        ).fetchone()

    @staticmethod
    def _log(conn, event_type, message_id=None, actor_id=None, payload=None) -> None:
        conn.execute(
            "INSERT INTO bus_events (message_id, event_type, actor_id, payload) VALUES (?,?,?,?)",
            (message_id, event_type, actor_id,
             _to_json(payload) if payload is not None else None),
        )

    @staticmethod
    def _row_to_message(row) -> dict:
        d = dict(row)
        for key in ("payload", "result", "callback_payload", "roles"):
            if key in d and isinstance(d[key], str) and d[key]:
                try:
                    d[key] = json.loads(d[key])
                except json.JSONDecodeError:
                    pass
        return d

    def _copy_artifact(self, message_id: str, attempts: int, source_path: Optional[str],
                        suffix: str = "raw.txt") -> tuple[Path, str]:
        if not source_path:
            raise ValidationError("artifact source path is required")
        src = Path(source_path)
        if not src.exists():
            raise ValidationError(f"artifact source not found: {source_path}")
        target_dir = self._artifacts_root / message_id / f"attempt-{attempts}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / suffix
        shutil.copy2(src, target)
        return target, _sha256_file(target)

    def _resolve_callback(self, conn, name: str):
        cb = conn.execute(
            "SELECT * FROM bus_callbacks WHERE name=?", (name,),
        ).fetchone()
        return cb

    # ---------- route actions ----------

    def set_route(
        self,
        agent_kind: str,
        max_in_flight: int,
        default_lease_seconds: int,
        profile: Optional[str] = None,
        enabled: int = 1,
        actor_id: str = "operator",
    ) -> dict:
        _require(max_in_flight > 0, ValidationError, "max-in-flight must be positive")
        _require(default_lease_seconds > 0, ValidationError,
                 "default-lease-seconds must be positive")
        _require(enabled in ROUTE_ENABLED_VALUES, ValidationError, "enabled must be 0 or 1")

        def action(conn):
            existing = conn.execute(
                "SELECT 1 FROM bus_routes WHERE agent_kind=? AND profile IS ?",
                (agent_kind, profile),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE bus_routes
                       SET max_in_flight=?, default_lease_s=?, enabled=?, updated_at=?
                       WHERE agent_kind=? AND profile IS ?""",
                    (max_in_flight, default_lease_seconds, enabled, self._clock(),
                     agent_kind, profile),
                )
                self._log(conn, "route_updated", actor_id=actor_id,
                          payload={"agent_kind": agent_kind, "profile": profile})
            else:
                conn.execute(
                    """INSERT INTO bus_routes (agent_kind, profile, max_in_flight, default_lease_s, enabled)
                       VALUES (?,?,?,?,?)""",
                    (agent_kind, profile, max_in_flight, default_lease_seconds, enabled),
                )
                self._log(conn, "route_created", actor_id=actor_id,
                          payload={"agent_kind": agent_kind, "profile": profile})
            row = self._route_locked(conn, agent_kind, profile)
            return self._row_to_message(row)

        return self._writer(action)

    def list_routes(self) -> list[dict]:
        def action(conn):
            return [self._row_to_message(r)
                    for r in conn.execute(
                        "SELECT * FROM bus_route_status ORDER BY agent_kind, profile"
                    )]
        return self._reader(action)

    def delete_route(self, agent_kind: str, profile: Optional[str] = None,
                     actor_id: str = "operator") -> dict:
        def action(conn):
            row = self._route_locked(conn, agent_kind, profile)
            if not row:
                raise NotFoundError(f"route not found: {agent_kind}/{profile}")
            blocking = conn.execute(
                """SELECT COUNT(*) AS c FROM bus_messages
                   WHERE agent_kind=? AND profile IS ? AND status IN ('pending','leased')""",
                (agent_kind, profile),
            ).fetchone()
            if blocking["c"] > 0:
                raise ConflictError("route has pending or leased messages; cannot delete")
            conn.execute(
                "DELETE FROM bus_routes WHERE agent_kind=? AND profile IS ?",
                (agent_kind, profile),
            )
            self._log(conn, "route_deleted", actor_id=actor_id,
                      payload={"agent_kind": agent_kind, "profile": profile})
            return {"deleted": {"agent_kind": agent_kind, "profile": profile}}

        return self._writer(action)

    # ---------- worker actions ----------

    def register_worker(
        self,
        worker_id: str,
        agent_kind: str,
        profile: Optional[str] = None,
        agent_name: Optional[str] = None,
        workspace_id: Optional[str] = None,
        pane_id: Optional[str] = None,
        tab_id: Optional[str] = None,
        session_id: Optional[str] = None,
        roles: Optional[list[str]] = None,
        launch_args: Optional[str] = None,
        permission_mode: Optional[str] = None,
        cwd: Optional[str] = None,
        registered_by: Optional[str] = None,
        model: Optional[str] = None,
        intensity: Optional[str] = None,
        agent_version: Optional[str] = None,
        herdr_version: Optional[str] = None,
        status: str = "ready",
        metadata: Optional[dict] = None,
        actor_id: Optional[str] = None,
    ) -> dict:
        metadata_json = _to_json(metadata or {})
        roles_json = _to_json(roles or [])

        def action(conn):
            conn.execute(
                """INSERT INTO bus_workers
                   (worker_id, agent_kind, profile, agent_name, workspace_id,
                    pane_id, tab_id, session_id, roles, launch_args, permission_mode,
                    cwd, registered_by, model, intensity, agent_version, herdr_version,
                    status, metadata, last_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(worker_id) DO UPDATE SET
                     agent_kind=excluded.agent_kind,
                     profile=excluded.profile,
                     agent_name=excluded.agent_name,
                     workspace_id=excluded.workspace_id,
                     pane_id=excluded.pane_id,
                     tab_id=excluded.tab_id,
                     session_id=excluded.session_id,
                     roles=excluded.roles,
                     launch_args=excluded.launch_args,
                     permission_mode=excluded.permission_mode,
                     cwd=excluded.cwd,
                     registered_by=excluded.registered_by,
                     model=excluded.model,
                     intensity=excluded.intensity,
                     agent_version=excluded.agent_version,
                     herdr_version=excluded.herdr_version,
                     status=excluded.status,
                     metadata=excluded.metadata,
                     last_seen_at=excluded.last_seen_at""",
                (worker_id, agent_kind, profile, agent_name, workspace_id,
                 pane_id, tab_id, session_id, roles_json, launch_args, permission_mode,
                 cwd, registered_by or "operator", model, intensity, agent_version,
                 herdr_version, status, metadata_json, self._clock()),
            )
            self._log(conn, "worker_registered", actor_id=actor_id or worker_id,
                      payload={"agent_kind": agent_kind, "profile": profile})
            row = conn.execute("SELECT * FROM bus_workers WHERE worker_id=?",
                               (worker_id,)).fetchone()
            return self._row_to_message(row)

        return self._writer(action)

    def heartbeat_worker(self, worker_id: str) -> dict:
        def action(conn):
            row = conn.execute("SELECT 1 FROM bus_workers WHERE worker_id=?",
                               (worker_id,)).fetchone()
            if not row:
                raise NotFoundError(f"unknown worker: {worker_id}")
            conn.execute("UPDATE bus_workers SET last_seen_at=? WHERE worker_id=?",
                         (self._clock(), worker_id))
            self._log(conn, "worker_heartbeat", actor_id=worker_id)
            return {"worker_id": worker_id, "last_seen_at": self._clock()}

        return self._writer(action)

    def list_workers(self) -> list[dict]:
        def action(conn):
            return [self._row_to_message(r)
                    for r in conn.execute(
                        "SELECT * FROM bus_workers ORDER BY last_seen_at DESC"
                    )]
        return self._reader(action)

    def reap_stale_workers(self, stale_seconds: int = 300) -> dict:
        """Mark workers whose last_seen_at is older than stale_seconds as
        'dead'.  Pure marker — does not delete rows (preserves audit trail).
        Returns {"reaped": N, "worker_ids": [...]}."""

        _require(stale_seconds >= 0, ValidationError,
                 "stale-seconds must be non-negative")
        cutoff = (datetime.now() - timedelta(seconds=stale_seconds)).isoformat(
            timespec="seconds")

        def action(conn):
            rows = conn.execute(
                """SELECT worker_id FROM bus_workers
                   WHERE last_seen_at < ? AND status != 'dead'""",
                (cutoff,),
            ).fetchall()
            worker_ids = [row["worker_id"] for row in rows]
            if worker_ids:
                placeholders = ",".join("?" * len(worker_ids))
                conn.execute(
                    f"""UPDATE bus_workers SET status='dead'
                        WHERE worker_id IN ({placeholders})""",
                    worker_ids,
                )
                for wid in worker_ids:
                    self._log(conn, "worker_stale_reaped", actor_id="reaper",
                              payload={"worker_id": wid,
                                       "stale_seconds": stale_seconds,
                                       "cutoff": cutoff})
            return {"reaped": len(worker_ids), "worker_ids": worker_ids}

        return self._writer(action)

    # ---------- message actions ----------

    def enqueue(
        self,
        caller_id: str,
        agent_kind: str,
        payload: dict,
        profile: Optional[str] = None,
        priority: int = 5,
        max_attempts: int = 3,
        callback_name: Optional[str] = None,
    ) -> dict:
        _validate_envelope(payload)
        _require(priority >= 0, ValidationError, "priority must be non-negative")
        _require(max_attempts > 0, ValidationError, "max-attempts must be positive")
        if not caller_id:
            raise ValidationError("caller is required")
        if not agent_kind:
            raise ValidationError("agent-kind is required")

        def action(conn):
            route = self._route_locked(conn, agent_kind, profile)
            if not route:
                raise NotFoundError(f"route not registered: {agent_kind}/{profile}")
            if not route["enabled"]:
                raise ConflictError("route is disabled")
            if callback_name:
                cb = conn.execute(
                    "SELECT 1 FROM bus_callbacks WHERE name=? AND enabled=1",
                    (callback_name,),
                ).fetchone()
                if not cb:
                    raise NotFoundError(
                        f"callback not registered or disabled: {callback_name}")
            message_id = _gen_id("msg")
            conn.execute(
                """INSERT INTO bus_messages
                   (id, caller_id, agent_kind, profile, status, priority, payload,
                    attempts, max_attempts, callback_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (message_id, caller_id, agent_kind, profile, "pending", priority,
                 _to_json(payload), 0, max_attempts, callback_name),
            )
            self._log(conn, "message_enqueued", message_id=message_id, actor_id=caller_id,
                      payload={"agent_kind": agent_kind, "profile": profile,
                               "priority": priority})
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?", (message_id,)).fetchone()
            return self._row_to_message(row)

        return self._writer(action)

    def claim(
        self,
        worker_id: str,
        agent_kind: str,
        profile: Optional[str] = None,
        lease_seconds: Optional[int] = None,
    ) -> Optional[dict]:
        lease_seconds = lease_seconds or DEFAULT_LEASE_SECONDS
        _require(lease_seconds > 0, ValidationError, "lease-seconds must be positive")

        def action(conn):
            route = self._route_locked(conn, agent_kind, profile)
            if not route:
                raise NotFoundError(f"route not registered: {agent_kind}/{profile}")
            now = self._clock()
            active = conn.execute(
                """SELECT COUNT(*) AS c FROM bus_messages
                   WHERE agent_kind=? AND profile IS ? AND status='leased'
                     AND lease_until > ?""",
                (agent_kind, profile, now),
            ).fetchone()
            if active["c"] >= route["max_in_flight"]:
                return None
            row = conn.execute(
                """SELECT id FROM bus_messages
                   WHERE agent_kind=? AND profile IS ?
                     AND status='pending'
                     AND cancel_requested=0
                   ORDER BY priority DESC, created_at ASC
                   LIMIT 1""",
                (agent_kind, profile),
            ).fetchone()
            if not row:
                return None
            lease_id = _gen_lease_id()
            lease_until = _now_plus(lease_seconds)
            cur = conn.execute(
                """UPDATE bus_messages
                   SET status='leased',
                       worker_id=?,
                       lease_id=?,
                       lease_until=?,
                       attempts=attempts+1,
                       updated_at=?
                   WHERE id=? AND status='pending'""",
                (worker_id, lease_id, lease_until, now, row["id"]),
            )
            if cur.rowcount == 0:
                return None
            self._log(conn, "message_claimed", message_id=row["id"], actor_id=worker_id,
                      payload={"lease_id": lease_id, "lease_until": lease_until})
            updated = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                                   (row["id"],)).fetchone()
            return self._row_to_message(updated)

        return self._writer(action)

    def heartbeat_message(
        self,
        message_id: str,
        worker_id: str,
        lease_id: str,
        lease_seconds: Optional[int] = None,
    ) -> dict:
        lease_seconds = lease_seconds or DEFAULT_LEASE_SECONDS
        _require(lease_seconds > 0, ValidationError, "lease-seconds must be positive")

        def action(conn):
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                               (message_id,)).fetchone()
            if not row:
                raise NotFoundError(f"message not found: {message_id}")
            if row["status"] != "leased":
                raise ConflictError(f"message status must be leased, got: {row['status']}")
            if row["worker_id"] != worker_id or row["lease_id"] != lease_id:
                raise PermissionError_("worker_id or lease_id does not match active lease")
            if row["cancel_requested"]:
                raise ConflictError("cancel_requested: please finalise via cancelled")
            new_until = _now_plus(lease_seconds)
            conn.execute(
                "UPDATE bus_messages SET lease_until=?, updated_at=? WHERE id=?",
                (new_until, self._clock(), message_id),
            )
            self._log(conn, "message_heartbeat", message_id=message_id,
                      actor_id=worker_id, payload={"lease_until": new_until})
            return {"id": message_id, "lease_until": new_until}

        return self._writer(action)

    def complete(
        self,
        message_id: str,
        worker_id: str,
        lease_id: str,
        result_payload: dict,
        raw_report_path: str,
    ) -> dict:
        """Finalise a leased Message as `succeeded`. Enqueues a callback
        delivery if the Message carries a registered `callback_name`."""

        return self._finalize(
            message_id=message_id,
            worker_id=worker_id,
            lease_id=lease_id,
            outcome="succeeded",
            result_payload=result_payload,
            raw_report_path=raw_report_path,
            require_cancel_requested=False,
        )

    def fail(
        self,
        message_id: str,
        worker_id: str,
        lease_id: str,
        reason: str,
        raw_report_path: str,
    ) -> dict:
        """Finalise a leased Message. Requeues when under max_attempts;
        moves to `dead` once attempts reach the limit."""

        def writer(conn):
            row = self._load_leased_locked(conn, message_id, worker_id, lease_id)
            attempts = row["attempts"]
            target_path, sha = self._copy_artifact(message_id, attempts, raw_report_path)
            result_payload = {"outcome": "failed", "reason": reason or ""}
            next_status = "pending" if attempts < row["max_attempts"] else "dead"
            result = self._write_terminal(
                conn, row, next_status, result_payload,
                target_path, sha, event_type="message_failed",
                payload_extra={"reason": reason},
            )
            self._increment_worker_stat(conn, worker_id, "messages_failed")
            return result

        return self._writer(writer)

    def cancelled(
        self,
        message_id: str,
        worker_id: str,
        lease_id: str,
        reason: str,
        raw_report_path: str,
    ) -> dict:
        return self._finalize(
            message_id=message_id,
            worker_id=worker_id,
            lease_id=lease_id,
            outcome="cancelled",
            result_payload={"outcome": "cancelled", "reason": reason or ""},
            raw_report_path=raw_report_path,
            require_cancel_requested=True,
        )

    def cancel(
        self,
        caller_id: str,
        message_id: str,
    ) -> dict:
        def action(conn):
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                               (message_id,)).fetchone()
            if not row:
                raise NotFoundError(f"message not found: {message_id}")
            if row["caller_id"] != caller_id:
                raise PermissionError_("only the originating caller can cancel this message")
            if row["status"] in TERMINAL_STATUSES or row["status"] == "archived":
                raise ConflictError(f"cannot cancel terminal message in status: {row['status']}")
            if row["status"] == "pending":
                conn.execute(
                    """UPDATE bus_messages SET status='cancelled', completed_at=?, updated_at=?
                       WHERE id=?""",
                    (self._clock(), self._clock(), message_id),
                )
                self._log(conn, "message_cancelled_by_producer",
                          message_id=message_id, actor_id=caller_id)
            elif row["status"] == "leased":
                conn.execute(
                    "UPDATE bus_messages SET cancel_requested=1, updated_at=? WHERE id=?",
                    (self._clock(), message_id),
                )
                self._log(conn, "message_cancel_requested",
                          message_id=message_id, actor_id=caller_id)
            updated = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                                   (message_id,)).fetchone()
            return self._row_to_message(updated)

        return self._writer(action)

    def requeue(self, caller_id: str, message_id: str) -> dict:
        def action(conn):
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                               (message_id,)).fetchone()
            if not row:
                raise NotFoundError(f"message not found: {message_id}")
            if row["caller_id"] != caller_id:
                raise PermissionError_("only the originating caller can requeue this message")
            if row["status"] != "dead":
                raise ConflictError(f"requeue requires status dead, got: {row['status']}")
            route = self._route_locked(conn, row["agent_kind"], row["profile"])
            if not route:
                raise ConflictError("route no longer registered; cannot requeue")
            conn.execute(
                """UPDATE bus_messages
                   SET status='pending', worker_id=NULL, lease_id=NULL, lease_until=NULL,
                       cancel_requested=0, attempts=0, updated_at=?
                   WHERE id=?""",
                (self._clock(), message_id),
            )
            self._log(conn, "message_requeued", message_id=message_id, actor_id=caller_id)
            updated = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                                   (message_id,)).fetchone()
            return self._row_to_message(updated)

        return self._writer(action)

    def reap_once(self) -> Optional[dict]:
        """Return one expired-leased Message to `pending` (or `dead` if
        attempts already at max). Returns `None` when nothing is due."""

        def action(conn):
            row = conn.execute(
                """SELECT * FROM bus_messages
                   WHERE status='leased' AND lease_until IS NOT NULL AND lease_until < ?
                   ORDER BY lease_until ASC LIMIT 1""",
                (self._clock(),),
            ).fetchone()
            if not row:
                return None
            new_status = "pending" if row["attempts"] < row["max_attempts"] else "dead"
            conn.execute(
                """UPDATE bus_messages
                   SET status=?, worker_id=NULL, lease_id=NULL, lease_until=NULL, updated_at=?
                   WHERE id=? AND status='leased'""",
                (new_status, self._clock(), row["id"]),
            )
            self._log(conn, "lease_expired", message_id=row["id"],
                      actor_id="reaper",
                      payload={"attempts": row["attempts"],
                               "max_attempts": row["max_attempts"],
                               "new_status": new_status,
                               "previous_worker": row["worker_id"]})
            return {"id": row["id"], "new_status": new_status}

        return self._writer(action)

    # ---------- result pull ----------

    def list_results(self, caller_id: str, after_event: int = 0) -> dict:
        def action(conn):
            rows = conn.execute(
                """SELECT e.* FROM bus_events e
                   JOIN bus_messages m ON m.id = e.message_id
                   WHERE m.caller_id = ?
                     AND e.id > ?
                     AND e.event_type IN ('message_completed','message_cancelled',
                                          'message_failed','lease_expired',
                                          'message_requeued','message_cancelled_by_producer',
                                          'late_result')
                   ORDER BY e.id ASC""",
                (caller_id, after_event),
            ).fetchall()
            events = [dict(r) for r in rows]
            next_cursor = events[-1]["id"] if events else after_event
            seen = set()
            ids = []
            by_id: dict[str, str] = {}
            for e in events:
                mid = e["message_id"]
                if mid in seen:
                    continue
                seen.add(mid)
                by_id[mid] = e["event_type"]
                ids.append(mid)
            messages = []
            if ids:
                placeholders = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"SELECT * FROM bus_messages WHERE id IN ({placeholders}) AND result_acked_at IS NULL",
                    ids,
                ).fetchall()
                for r in rows:
                    d = self._row_to_message(r)
                    d["last_event"] = by_id.get(r["id"])
                    messages.append(d)
            return {"events": events, "messages": messages, "next_cursor": next_cursor}

        return self._reader(action)

    def get_result(self, caller_id: str, message_id: str) -> dict:
        def action(conn):
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                               (message_id,)).fetchone()
            if not row:
                raise NotFoundError(f"message not found: {message_id}")
            if row["caller_id"] != caller_id:
                raise PermissionError_("only the originating caller can read this result")
            return self._row_to_message(row)

        return self._reader(action)

    def ack_result(self, caller_id: str, message_id: str) -> dict:
        def action(conn):
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                               (message_id,)).fetchone()
            if not row:
                raise NotFoundError(f"message not found: {message_id}")
            if row["caller_id"] != caller_id:
                raise PermissionError_("only the originating caller can ack this message")
            if not row["result_acked_at"]:
                conn.execute(
                    "UPDATE bus_messages SET result_acked_at=?, updated_at=? WHERE id=?",
                    (self._clock(), self._clock(), message_id),
                )
                self._log(conn, "result_acked", message_id=message_id, actor_id=caller_id)
            updated = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                                   (message_id,)).fetchone()
            return self._row_to_message(updated)

        return self._writer(action)

    def result_history(self, caller_id: str, message_id: str) -> dict:
        def action(conn):
            row = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                               (message_id,)).fetchone()
            if not row:
                raise NotFoundError(f"message not found: {message_id}")
            if row["caller_id"] != caller_id:
                raise PermissionError_("only the originating caller can read this history")
            events = conn.execute(
                "SELECT * FROM bus_events WHERE message_id=? ORDER BY id ASC",
                (message_id,),
            ).fetchall()
            deliveries = conn.execute(
                "SELECT * FROM callback_deliveries WHERE message_id=? ORDER BY created_at ASC",
                (message_id,),
            ).fetchall()
            return {
                "message": self._row_to_message(row),
                "events": [dict(e) for e in events],
                "callback_deliveries": [dict(d) for d in deliveries],
            }

        return self._reader(action)

    # ---------- callback actions ----------

    def register_callback(
        self,
        name: str,
        executable: str,
        arguments: Iterable[str],
        actor_id: str = "operator",
    ) -> dict:
        exe_path, args = _validate_callback(executable, list(arguments))

        def action(conn):
            conn.execute(
                """INSERT INTO bus_callbacks
                   (name, kind, command_template, executable, arguments_json, enabled)
                   VALUES (?, ?, ?, ?, ?, 1)
                   ON CONFLICT(name) DO UPDATE SET
                     kind=excluded.kind,
                     command_template=excluded.command_template,
                     executable=excluded.executable,
                     arguments_json=excluded.arguments_json,
                     enabled=excluded.enabled,
                     updated_at=excluded.updated_at""",
                (name, "executable", "", exe_path, _to_json(args)),
            )
            self._log(conn, "callback_registered", actor_id=actor_id,
                      payload={"name": name, "kind": "executable"})
            row = conn.execute("SELECT * FROM bus_callbacks WHERE name=?",
                               (name,)).fetchone()
            return self._row_to_message(row)

        return self._writer(action)

    def register_legacy_template_callback(
        self,
        name: str,
        kind: str,
        command_template: str,
        actor_id: str = "operator",
    ) -> dict:
        """Register the legacy shell-template callback. The Core preserves
        the previous behaviour: this callback remains enabled and is
        executed with `shell=True` by `ShellTemplateCommandRunner`.

        New callbacks should register through `register_callback` instead."""

        if not isinstance(name, str) or not name:
            raise ValidationError("callback name must be a non-empty string")
        if not isinstance(command_template, str) or not command_template:
            raise ValidationError("legacy command_template must be a non-empty string")
        kind = kind or "command"

        def action(conn):
            conn.execute(
                """INSERT INTO bus_callbacks
                   (name, kind, command_template, executable, arguments_json, enabled)
                   VALUES (?, ?, ?, NULL, NULL, 1)
                   ON CONFLICT(name) DO UPDATE SET
                     kind=excluded.kind,
                     command_template=excluded.command_template,
                     executable=NULL,
                     arguments_json=NULL,
                     enabled=1,
                     updated_at=excluded.updated_at""",
                (name, kind, command_template),
            )
            self._log(conn, "callback_registered", actor_id=actor_id,
                      payload={"name": name, "kind": kind})
            row = conn.execute("SELECT * FROM bus_callbacks WHERE name=?",
                               (name,)).fetchone()
            return self._row_to_message(row)

        return self._writer(action)

    def list_callbacks(self) -> list[dict]:
        def action(conn):
            return [self._row_to_message(r)
                    for r in conn.execute("SELECT * FROM bus_callbacks ORDER BY name")]
        return self._reader(action)

    def deliver_one_callback(self, runner: Optional[Any] = None) -> Optional[dict]:
        """Attempt delivery of the next due callback. `runner` is an optional
        `CommandRunner`; defaults to `subprocess.CommandRunner`. Returns a small
        dict describing the outcome, or `None` if nothing is due."""

        runner_obj = runner or self._default_callback_runner()

        def action(conn):
            row = conn.execute(
                """SELECT * FROM callback_deliveries
                   WHERE status='pending' AND next_attempt_at <= ?
                   ORDER BY next_attempt_at ASC LIMIT 1""",
                (self._clock(),),
            ).fetchone()
            if not row:
                return None
            message = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                                   (row["message_id"],)).fetchone()
            callback = conn.execute("SELECT * FROM bus_callbacks WHERE name=?",
                                   (row["callback_name"],)).fetchone()
            return self._execute_callback_delivery(conn, runner_obj, row, message, callback)

        return self._writer(action)

    def deliver_due_callbacks(self, runner: Optional[Any] = None) -> list[dict]:
        runner_obj = runner or self._default_callback_runner()
        results: list[dict] = []
        while True:
            result = self.deliver_one_callback(runner=runner_obj)
            if result is None:
                break
            results.append(result)
        return results

    def _execute_callback_delivery(self, conn, runner, delivery_row,
                                   message_row, callback_row):
        if not callback_row or not callback_row["enabled"]:
            self._mark_callback(conn, delivery_row["id"], "dead",
                                last_error="callback disabled or missing",
                                next_attempt_at=None, attempts=delivery_row["attempts"])
            return {"id": delivery_row["id"], "result": "dead",
                    "reason": "callback disabled or missing"}

        substitutions = self._callback_substitutions(delivery_row, message_row, callback_row)
        exe_path = callback_row["executable"] or ""
        raw_args = callback_row["arguments_json"]
        try:
            arg_list = json.loads(raw_args) if raw_args else []
        except json.JSONDecodeError:
            arg_list = []
        if not isinstance(arg_list, list):
            arg_list = []
        rendered_args = [_render_template(str(a), substitutions) for a in arg_list]
        command_template = callback_row["command_template"] or ""

        # Branch by row shape: registered `executable` runs argv + shell=False;
        # `command_template` falls through to legacy shell-string execution so
        # existing scripts keep working. Either way the runner is injected.
        if exe_path and Path(exe_path).exists():
            cmd = [exe_path]
            if command_template:
                cmd.append(_render_template(command_template, substitutions))
            cmd.extend(rendered_args)
            completed = runner.run(cmd, timeout=CALLBACK_TIMEOUT_SECONDS)
        elif command_template:
            shell_runner = _shell_runner_from(runner)
            cmd = [_render_template(command_template, substitutions), *rendered_args]
            completed = shell_runner.run(cmd, timeout=CALLBACK_TIMEOUT_SECONDS)
        else:
            self._mark_callback(conn, delivery_row["id"], "dead",
                                last_error="executable missing or invalid",
                                next_attempt_at=None, attempts=delivery_row["attempts"])
            return {"id": delivery_row["id"], "result": "dead",
                    "reason": "executable missing or invalid"}

        # Persist stdout/stderr as artifacts for audit.
        artifact_stdout, artifact_stderr = self._save_callback_artifacts(
            delivery_row, message_row, completed.stdout, completed.stderr,
        )
        payload_extra = {
            "executable": exe_path or None,
            "command_template": command_template or None,
            "exit_code": completed.returncode,
            "stdout_artifact": artifact_stdout,
            "stderr_artifact": artifact_stderr,
        }

        if completed.returncode == 0:
            conn.execute(
                """UPDATE callback_deliveries
                   SET status='delivered', delivered_at=?, attempts=attempts+1,
                       last_error=NULL
                   WHERE id=?""",
                (self._clock(), delivery_row["id"]),
            )
            self._log(conn, "callback_delivered",
                      message_id=delivery_row["message_id"],
                      actor_id=delivery_row["id"], payload=payload_extra)
            return {"id": delivery_row["id"], "result": "delivered",
                    "exit_code": completed.returncode}

        attempts = delivery_row["attempts"] + 1
        next_delay = RESULT_BACKOFF_SECONDS[min(attempts - 1, len(RESULT_BACKOFF_SECONDS) - 1)]
        next_attempt_at = _now_plus(next_delay)
        new_status = "dead" if attempts >= len(RESULT_BACKOFF_SECONDS) else "pending"
        stderr_tail = (completed.stderr or completed.stdout or "")[:RESULT_RAW_REPORT_LIMIT]
        self._mark_callback(conn, delivery_row["id"], new_status,
                            last_error=stderr_tail,
                            next_attempt_at=next_attempt_at, attempts=attempts)
        self._log(conn, "callback_failed", message_id=delivery_row["message_id"],
                  actor_id=delivery_row["id"],
                  payload={**payload_extra, "attempts": attempts, "next_status": new_status})
        return {"id": delivery_row["id"], "result": new_status,
                "exit_code": completed.returncode, "error": stderr_tail}

    def _record_callback_failure(self, conn, delivery_row, attempts, error):
        next_delay = RESULT_BACKOFF_SECONDS[min(attempts - 1, len(RESULT_BACKOFF_SECONDS) - 1)]
        new_status = "dead" if attempts >= len(RESULT_BACKOFF_SECONDS) else "pending"
        self._mark_callback(conn, delivery_row["id"], new_status,
                            last_error=error,
                            next_attempt_at=_now_plus(next_delay), attempts=attempts)
        return {"id": delivery_row["id"], "result": new_status, "error": error}

    def _mark_callback(self, conn, delivery_id, status, *,
                      last_error=None, next_attempt_at=None, attempts=None) -> None:
        if attempts is None:
            attempts_row = conn.execute(
                "SELECT attempts FROM callback_deliveries WHERE id=?",
                (delivery_id,),
            ).fetchone()
            attempts = attempts_row["attempts"] if attempts_row else 0
        conn.execute(
            """UPDATE callback_deliveries
               SET status=?, attempts=?, last_error=?, next_attempt_at=COALESCE(?, next_attempt_at)
               WHERE id=?""",
            (status, attempts, last_error, next_attempt_at, delivery_id),
        )

    def _save_callback_artifacts(self, delivery_row, message_row, stdout, stderr):
        target_dir = (self._artifacts_root
                      / message_row["id"]
                      / f"callback-{delivery_row['id']}")
        target_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = target_dir / "stdout.txt"
        stderr_path = target_dir / "stderr.txt"
        if stdout:
            stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
        else:
            stdout_path.write_text("", encoding="utf-8")
        if stderr:
            stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
        else:
            stderr_path.write_text("", encoding="utf-8")
        return str(stdout_path), str(stderr_path)

    @staticmethod
    def _callback_substitutions(delivery_row, message_row, callback_row):
        return {
            "message_id": str(message_row["id"]),
            "caller_id": str(message_row["caller_id"]),
            "callback_name": str(delivery_row["callback_name"]),
            "agent_kind": str(message_row["agent_kind"]),
            "profile": "" if message_row["profile"] is None else str(message_row["profile"]),
            "status": str(message_row["status"]),
            "delivery_id": str(delivery_row["id"]),
        }

    # ---------- Herdr Adapter ----------

    def worker_loop_once(
        self,
        worker_id: str,
        agent_kind: str,
        profile: Optional[str],
        agent_name: str,
        commander_runner,
        prompt_builder,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict:
        """Claim one Message for the given Route, deliver it to a waiting TUI
        Worker via the injected `commander_runner`, then exit. The runner is
        expected to deliver the prompt and return a `CommandResult`.

        `prompt_builder` is a callable that receives the claimed Message
        dictionary and returns the prompt text. Core handles heartbeat once
        after a successful delivery."""

        claim = self.claim(worker_id=worker_id, agent_kind=agent_kind,
                           profile=profile, lease_seconds=lease_seconds)
        if not claim:
            return {"claimed": False, "reason": "no pending message or route full"}
        # Count as claimed even if delivery subsequently fails.
        def _bump_claimed(conn):
            self._increment_worker_stat(conn, worker_id, "messages_claimed")
        self._writer(_bump_claimed)
        prompt = prompt_builder(dict(claim))
        run_result = commander_runner.run_agent_prompt(
            agent_name=agent_name,
            prompt=prompt,
            timeout=lease_seconds,
        )
        ok = getattr(run_result, "ok", False)
        if not ok:
            error = getattr(run_result, "error", None) or "agent prompt failed"
            return {
                "claimed": True,
                "message_id": claim["id"],
                "lease_id": claim["lease_id"],
                "worker_id": worker_id,
                "agent_name": agent_name,
                "attempt": claim["attempts"],
                "delivered": False,
                "delivery_error": error,
            }
        # Immediate single heartbeat to extend the lease past the prompt lag.
        self.heartbeat_message(message_id=claim["id"], worker_id=worker_id,
                               lease_id=claim["lease_id"])
        return {
            "claimed": True,
            "delivered": True,
            "message_id": claim["id"],
            "lease_id": claim["lease_id"],
            "worker_id": worker_id,
            "agent_name": agent_name,
            "attempt": claim["attempts"],
        }

    # ---------- internal: finalize helpers ----------

    def _load_leased_locked(self, conn, message_id, worker_id, lease_id):
        row = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                           (message_id,)).fetchone()
        if not row:
            raise NotFoundError(f"message not found: {message_id}")
        if row["status"] != "leased":
            # Either not leased (reaped/dead/etc) or the lease has already been
            # replaced. Either way: late_result, never overwrite state.
            raise _LateResultError(conn, message_id, worker_id, row)
        if row["worker_id"] != worker_id or row["lease_id"] != lease_id:
            # Different worker now holds the active lease. The caller's
            # claim is stale.
            raise _LateResultError(conn, message_id, worker_id, row)
        return row

    def _finalize(
        self,
        message_id: str,
        worker_id: str,
        lease_id: str,
        outcome: str,
        result_payload: dict,
        raw_report_path: Optional[str],
        require_cancel_requested: bool,
    ) -> dict:
        def writer(conn):
            try:
                row = self._load_leased_locked(conn, message_id, worker_id, lease_id)
            except _LateResultError as late:
                observed = late.row["status"]
                # Commit the audit event on the open writer transaction
                # BEFORE raising, otherwise `_writer` will roll it back on
                # connection close.
                late.record_on(conn)
                conn.commit()
                raise ConflictError(
                    f"late result: active lease is no longer held by this worker; "
                    f"observed message status: {observed}")
            if require_cancel_requested and not row["cancel_requested"]:
                raise ConflictError("cancel_requested not set: cannot finalise as cancelled")
            attempts = row["attempts"]
            target_path, sha = self._copy_artifact(message_id, attempts, raw_report_path or "")
            event_type = "message_completed" if outcome == "succeeded" else (
                "message_cancelled" if outcome == "cancelled" else "message_failed")
            result = self._write_terminal(
                conn, row, outcome, result_payload, target_path, sha,
                event_type=event_type,
            )
            if outcome == "succeeded":
                self._increment_worker_stat(conn, worker_id, "messages_completed")
            return result

        return self._writer(writer)

    def _write_terminal(self, conn, row, next_status, result_payload, target_path,
                       sha, *, event_type, payload_extra=None):
        attempts = row["attempts"]
        conn.execute(
            """UPDATE bus_messages
               SET status=?, result=?, raw_report_path=?, raw_report_sha256=?,
                   worker_id=NULL, lease_id=NULL, lease_until=NULL,
                   completed_at=?, updated_at=?
               WHERE id=? AND status='leased'""",
            (next_status, _to_json(result_payload), str(target_path), sha,
             self._clock(), self._clock(), row["id"]),
        )
        callback_id = None
        if next_status == "succeeded" and row["callback_name"]:
            callback_row = self._resolve_callback(conn, row["callback_name"])
            # Enqueue a delivery whenever the callback is enabled and has any
            # execution surface (executable for argv mode, command_template
            # for the legacy shell mode). The runner choice happens at
            # delivery time inside `_execute_callback_delivery`.
            if callback_row and callback_row["enabled"] and (
                callback_row["executable"] or callback_row["command_template"]
            ):
                callback_id = _gen_id("cb")
                conn.execute(
                    "INSERT INTO callback_deliveries (id, message_id, callback_name) VALUES (?,?,?)",
                    (callback_id, row["id"], row["callback_name"]),
                )
        payload = {"attempts": attempts, "raw_report_sha256": sha,
                   "callback_id": callback_id, "terminal_status": next_status}
        if payload_extra:
            payload.update(payload_extra)
        self._log(conn, event_type, message_id=row["id"], actor_id=row["worker_id"], payload=payload)
        updated = conn.execute("SELECT * FROM bus_messages WHERE id=?",
                               (row["id"],)).fetchone()
        return self._row_to_message(updated)

    def _increment_worker_stat(self, conn, worker_id, column):
        """Atomically increment a worker statistic column by 1.

        Uses ``SET col = col + 1`` so concurrent increments never clobber
        each other.  Silent no-op when the worker does not exist (UPDATE
        affects 0 rows).
        """
        if column not in ("messages_claimed", "messages_completed", "messages_failed"):
            raise ValueError(f"invalid stat column: {column}")
        conn.execute(
            f"UPDATE bus_workers SET {column} = {column} + 1 WHERE worker_id = ?",
            (worker_id,),
        )

    def _default_callback_runner(self):
        import callback_runner
        return callback_runner.SubprocessCommandRunner()


def _shell_runner_from(runner):
    """Return the shell-string runner. If the caller injected a Shell runner
    already, reuse it. Otherwise return a fresh one from `callback_runner`."""
    import callback_runner
    if isinstance(runner, callback_runner.ShellTemplateCommandRunner):
        return runner
    return callback_runner.ShellTemplateCommandRunner()


# ---------------------------------------------------------------------------
# Internal: late-result marker
# ---------------------------------------------------------------------------


class _LateResultError(Exception):
    def __init__(self, conn, message_id, worker_id, row) -> None:
        self.conn = conn
        self.message_id = message_id
        self.worker_id = worker_id
        self.row = row

    def record_on(self, conn) -> None:
        """Insert the audit event into the supplied open connection. The
        caller is responsible for committing before raising so the event
        survives any rollback."""
        AgentBus._log(conn, "late_result",
                      message_id=self.message_id, actor_id=self.worker_id,
                      payload={"observed_status": self.row["status"],
                               "observed_worker": self.row["worker_id"]})

    def record(self) -> None:
        """Insert the audit event into the actual database on a fresh
        connection. Used by paths that have no open transaction."""
        audit_path_row = self.conn.execute("PRAGMA database_list").fetchone()
        if not audit_path_row:
            return
        audit_conn = sqlite3.connect(audit_path_row["file"])
        try:
            self.record_on(audit_conn)
            audit_conn.commit()
        finally:
            audit_conn.close()
