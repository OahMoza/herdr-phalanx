"""Phalanx ↔ Agent Bus Bridge.

Projects Phalanx Task creation into Agent Bus Message enqueue, and promotes
Bus Message completion status back into Phalanx Dispatch / Task state.

Architecture
------------
- Two **independent** database connections: Phalanx DB (runs/tasks/dispatches)
  and Agent Bus DB (bus_messages/workers/routes).  No cross-DB transaction.
- The bridge owns a small ``bus_message_map`` table inside the Phalanx DB that
  records every Task → Message projection for idempotency and traceability.
- Phalanx Coordinator remains the sole writer of runs/tasks/dispatches; the
  bridge only writes the mapping table and promotes results *through* the
  existing dispatch state machine.

Execution modes
--------------
- ``managed``: envelope carries full structured context (dispatch_id, role,
  constraints, metadata).  The Bus Worker prompt includes a TASK_COMPLETE
  contract.
- ``raw-pane``: envelope carries minimal instruction + workspace context.
  No TASK_COMPLETE contract is injected; result is whatever the agent returns.

Idempotency
-----------
``bridge_task_to_message`` checks ``bus_message_map`` before enqueueing.  If
a live (non-terminal) Message already exists for the Task, it returns the
existing mapping instead of enqueueing a second time.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

BRIDGE_CALLER_ID = "phalanx-bridge"
TERMINAL_DISPATCH_STATUSES = ("completed", "failed", "blocked", "abandoned")
TERMINAL_BUS_STATUSES = ("succeeded", "dead", "cancelled")


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _phalanx_conn(phalanx_db_path: Path) -> sqlite3.Connection:
    """Open a Phalanx DB connection (row_factory = Row, foreign_keys ON)."""
    conn = sqlite3.connect(str(phalanx_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _bus_conn(bus_db_path: Path) -> sqlite3.Connection:
    """Open an Agent Bus DB connection (row_factory = Row, foreign_keys ON)."""
    conn = sqlite3.connect(str(bus_db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

BUS_MESSAGE_MAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_message_map (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id       TEXT NOT NULL REFERENCES tasks(id),
  dispatch_id   TEXT REFERENCES dispatches(id),
  message_id    TEXT NOT NULL,
  execution_mode TEXT NOT NULL DEFAULT 'managed'
    CHECK(execution_mode IN ('managed', 'raw-pane')),
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(task_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_bus_message_map_task
  ON bus_message_map(task_id);
CREATE INDEX IF NOT EXISTS idx_bus_message_map_message
  ON bus_message_map(message_id);
"""


def init_bridge(phalanx_db_path: Path) -> None:
    """Idempotently create the bridge's ``bus_message_map`` table inside the
    Phalanx DB.  Safe to call on every startup."""
    conn = _phalanx_conn(phalanx_db_path)
    try:
        conn.executescript(BUS_MESSAGE_MAP_SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Projection: Phalanx Task → Bus Message envelope
# ---------------------------------------------------------------------------


def _build_managed_envelope(task: dict, dispatch_id: str) -> dict:
    """Build a structured Bus Message payload for a managed-mode Task.

    The envelope embeds the TASK_COMPLETE contract so the Worker knows how
    to report results back through the Bridge."""
    return {
        "instruction": task["spec"],
        "bridge": {
            "source": "phalanx",
            "task_id": task["id"],
            "dispatch_id": dispatch_id,
            "run_id": task["run_id"],
            "mode": "managed",
        },
        "context": {
            "assigned_role": task.get("assigned_role"),
            "preferred_agent": task.get("preferred_agent"),
        },
        "constraints": {
            "require_task_complete": True,
        },
    }


def _build_raw_pane_envelope(task: dict) -> dict:
    """Build a minimal Bus Message payload for a raw-pane-mode Task.

    No TASK_COMPLETE contract is injected; the Worker runs unconstrained."""
    return {
        "instruction": task["spec"],
        "bridge": {
            "source": "phalanx",
            "task_id": task["id"],
            "run_id": task["run_id"],
            "mode": "raw-pane",
        },
    }


def _projection_priority(task: dict) -> int:
    """Derive Bus Message priority from Task metadata (default 5)."""
    return int(task.get("priority", 5))


# ---------------------------------------------------------------------------
# Idempotency: bus_message_map lookups
# ---------------------------------------------------------------------------


def _find_existing_mapping(
    phalanx_conn: sqlite3.Connection, task_id: str
) -> Optional[dict]:
    """Return the most recent bus_message_map row for *task_id* **if** the
    corresponding Bus Message is still live (not in a terminal status).

    Returns ``None`` when no live mapping exists (caller may safely enqueue).
    """
    row = phalanx_conn.execute(
        """SELECT * FROM bus_message_map
           WHERE task_id=?
           ORDER BY id DESC LIMIT 1""",
        (task_id,),
    ).fetchone()
    if not row:
        return None
    return dict(row)


def _is_message_live(bus_conn: sqlite3.Connection, message_id: str) -> bool:
    """A Message is live when it has not yet reached a terminal status."""
    row = bus_conn.execute(
        "SELECT status FROM bus_messages WHERE id=?", (message_id,)
    ).fetchone()
    if not row:
        # Message vanished (shouldn't happen) — treat as not-live so caller
        # can re-enqueue.
        return False
    return row["status"] not in TERMINAL_BUS_STATUSES


# ---------------------------------------------------------------------------
# Core action: bridge_task_to_message
# ---------------------------------------------------------------------------


def bridge_task_to_message(
    task_record: dict,
    dispatch_id: str,
    phalanx_db_path: Path,
    bus_db_path: Path,
    *,
    agent_bus_instance: Optional[Any] = None,
    max_attempts: int = 3,
    priority: Optional[int] = None,
) -> dict:
    """Project a Phalanx Task into an Agent Bus Message.

    Idempotent: if a live Bus Message already exists for this Task (recorded
    in ``bus_message_map``), the existing mapping is returned without a
    second enqueue.

    Parameters
    ----------
    task_record:
        Dict with keys ``id``, ``run_id``, ``spec``, ``execution_mode``, and
        optionally ``assigned_role``, ``preferred_agent``, ``priority``.
    dispatch_id:
        The Phalanx Dispatch id this projection is tied to.
    phalanx_db_path:
        Path to the Phalanx DB file.
    bus_db_path:
        Path to the Agent Bus DB file.
    agent_bus_instance:
        Optional pre-configured ``AgentBus`` Core instance.  When ``None`` a
        new instance is created from *bus_db_path*.
    max_attempts:
        Bus Message max_attempts (default 3).
    priority:
        Override Bus Message priority.  When ``None`` the value is derived
        from the Task.

    Returns
    -------
    dict with keys ``message_id``, ``enqueued`` (bool), ``existing`` (bool).
    """
    # --- connect to Phalanx DB and check idempotency -------------------
    ph_conn = _phalanx_conn(phalanx_db_path)
    try:
        existing = _find_existing_mapping(ph_conn, task_record["id"])
        if existing:
            bus_conn = _bus_conn(bus_db_path)
            try:
                if _is_message_live(bus_conn, existing["message_id"]):
                    return {
                        "message_id": existing["message_id"],
                        "enqueued": False,
                        "existing": True,
                    }
            finally:
                bus_conn.close()

        # --- build envelope -------------------------------------------
        mode = task_record.get("execution_mode", "managed")
        if mode == "managed":
            envelope = _build_managed_envelope(task_record, dispatch_id)
        else:
            envelope = _build_raw_pane_envelope(task_record)

        if priority is None:
            priority = _projection_priority(task_record)

        # --- enqueue via Agent Bus Core --------------------------------
        if agent_bus_instance is None:
            # Deferred import so the bridge module can be tested with a stub.
            from agent_bus_core import AgentBus

            agent_bus_instance = AgentBus(
                database_path=bus_db_path,
                artifacts_root=bus_db_path.parent / "runs" / "agent-bus",
            )

        bus_msg = agent_bus_instance.enqueue(
            caller_id=BRIDGE_CALLER_ID,
            agent_kind=task_record.get("preferred_agent", "omp"),
            payload=envelope,
            priority=priority,
            max_attempts=max_attempts,
        )

        # --- record mapping -------------------------------------------
        ph_conn.execute(
            """INSERT INTO bus_message_map
               (task_id, dispatch_id, message_id, execution_mode)
               VALUES (?,?,?,?)""",
            (task_record["id"], dispatch_id, bus_msg["id"], mode),
        )
        ph_conn.commit()

        return {
            "message_id": bus_msg["id"],
            "enqueued": True,
            "existing": False,
        }
    finally:
        ph_conn.close()


# ---------------------------------------------------------------------------
# Core action: on_message_complete
# ---------------------------------------------------------------------------


def on_message_complete(
    bus_message: dict,
    phalanx_db_path: Path,
    bus_db_path: Path,
    *,
    coordinator: str = "phalanx-bridge",
) -> dict:
    """Promote a completed Bus Message back into Phalanx Dispatch / Task state.

    Parameters
    ----------
    bus_message:
        Bus Message dict (as returned by ``AgentBus`` / result-list) with at
        least ``id``, ``status``, ``result`` keys.
    phalanx_db_path:
        Path to the Phalanx DB file.
    bus_db_path:
        Path to the Agent Bus DB file (unused today; reserved for artifact
        cross-references).
    coordinator:
        Identity used when asserting run ownership.

    Returns
    -------
    dict with keys ``updated`` (bool), ``reason`` (str), ``dispatch_id``.
    """
    message_id = bus_message["id"]

    ph_conn = _phalanx_conn(phalanx_db_path)
    try:
        # --- locate the mapping ----------------------------------------
        map_row = ph_conn.execute(
            "SELECT * FROM bus_message_map WHERE message_id=?", (message_id,)
        ).fetchone()
        if not map_row:
            return {
                "updated": False,
                "reason": f"no bus_message_map entry for message {message_id}",
                "dispatch_id": None,
            }

        dispatch_id = map_row["dispatch_id"]
        task_id = map_row["task_id"]

        # --- guard: terminal dispatch ----------------------------------
        if dispatch_id:
            disp = ph_conn.execute(
                "SELECT * FROM dispatches WHERE id=?", (dispatch_id,)
            ).fetchone()
            if disp and disp["status"] in TERMINAL_DISPATCH_STATUSES:
                return {
                    "updated": False,
                    "reason": f"dispatch {dispatch_id} already terminal ({disp['status']})",
                    "dispatch_id": dispatch_id,
                }

        # --- parse result ----------------------------------------------
        result = bus_message.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = {"outcome": "failed", "reason": "unparseable result"}

        outcome = "succeeded"
        summary = ""
        files_modified: list[str] = []
        if isinstance(result, dict):
            outcome = result.get("outcome", "succeeded")
            summary = result.get("summary", "")
            if isinstance(result.get("files_modified"), list):
                files_modified = result["files_modified"]

        # --- promote through dispatch state machine --------------------
        if dispatch_id:
            dispatch_status = "completed" if outcome == "succeeded" else "failed"
            now_ts = datetime.now().isoformat(timespec="seconds")
            files_json = json.dumps(files_modified, ensure_ascii=False)
            ph_conn.execute(
                """UPDATE dispatches
                   SET status=?, outcome=?, files_modified=?, summary=?, completed_at=?
                   WHERE id=?""",
                (dispatch_status, outcome, files_json, summary, now_ts, dispatch_id),
            )

        # --- promote task ----------------------------------------------
        task = ph_conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if task:
            if outcome == "succeeded":
                task_status = "completed"
                completed_at = now_ts if dispatch_id else datetime.now().isoformat(timespec="seconds")
            else:
                retry_count = task["retry_count"]
                max_retries = task["max_retries"]
                task_status = "pending" if retry_count < max_retries else "failed"
                completed_at = None

            result_json = json.dumps(
                {"outcome": outcome, "summary": summary, "files_modified": files_modified},
                ensure_ascii=False,
            )
            ph_conn.execute(
                """UPDATE tasks
                   SET status=?, result=?, updated_at=?, completed_at=?
                   WHERE id=?""",
                (
                    task_status,
                    result_json,
                    datetime.now().isoformat(timespec="seconds"),
                    completed_at,
                    task_id,
                ),
            )

        # --- audit event ------------------------------------------------
        event_type = "worker_done" if outcome == "succeeded" else "worker_failed"
        ph_conn.execute(
            """INSERT INTO events (run_id, task_id, dispatch_id, event_type, payload)
               VALUES (?,?,?,?,?)""",
            (
                task["run_id"] if task else None,
                task_id,
                dispatch_id,
                event_type,
                json.dumps(
                    {"outcome": outcome, "summary": summary, "bridge": True},
                    ensure_ascii=False,
                ),
            ),
        )
        ph_conn.commit()

        return {
            "updated": True,
            "reason": f"promoted to {outcome}",
            "dispatch_id": dispatch_id,
        }
    finally:
        ph_conn.close()
