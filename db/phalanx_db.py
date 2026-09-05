#!/usr/bin/env python3
"""
Herdr Phalanx — Orchestration State Database CLI
Run/Task/Dispatch three-layer model + DAG deps + events + gates.

Usage:
  python phalanx_db.py init-db
  python phalanx_db.py run-create --objective "..." [--workspace w1]
  python phalanx_db.py run-list
  python phalanx_db.py run-status --run <id>
  python phalanx_db.py task-add --run <id> --spec "..." [--deps '["t1"]'] [--role Developer] [--agent omp]
  python phalanx_db.py task-list [--run <id>] [--status pending]
  python phalanx_db.py task-ready --run <id>
  python phalanx_db.py task-get --id <id>
  python phalanx_db.py dispatch-start --task <id> --agent-name dev1 --agent-kind omp --pane w1:p3 [--tab w1:t3]
  python phalanx_db.py dispatch-complete --dispatch <id> --outcome succeeded [--files '["a.py"]'] [--summary "..."]
  python phalanx_db.py dispatch-fail --dispatch <id> --reason "..."
  python phalanx_db.py dispatch-list [--run <id>] [--task <id>]
  python phalanx_db.py gate-create --run <id> --type design_review --question "..." [--task <id>]
  python phalanx_db.py gate-resolve --gate <id> --resolution pass [--by dispatcher] [--evidence '{}']
  python phalanx_db.py event-log [--run <id>] [--limit 20]
  python phalanx_db.py db-path

Environment:
  PHALANX_DB — override database path (default: ~/.herdr-phalanx/phalanx.db)
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

def default_db_path() -> Path:
    env = os.environ.get("PHALANX_DB")
    if env:
        return Path(env)
    return Path.home() / ".herdr-phalanx" / "phalanx.db"


def get_db() -> sqlite3.Connection:
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schema_path() -> Path:
    return Path(__file__).parent / "schema.sql"


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def out(data, table: bool = False):
    """Print JSON by default, or a simple table."""
    if table and isinstance(data, list) and data and isinstance(data[0], dict):
        keys = list(data[0].keys())
        col_widths = {k: max(len(k), max((len(str(r.get(k, ""))) for r in data), default=0)) for k in keys}
        header = " | ".join(k.ljust(col_widths[k]) for k in keys)
        sep = "-+-".join("-" * col_widths[k] for k in keys)
        print(header)
        print(sep)
        for r in data:
            print(" | ".join(str(r.get(k, "")).ljust(col_widths[k]) for k in keys))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def row_to_dict(row: sqlite3.Row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    # Parse JSON fields
    for key in ("deps", "files_modified", "result", "metadata", "payload", "evidence", "options", "deps_array"):
        if key in d and d[key]:
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def log_event(conn: sqlite3.Connection, event_type: str, run_id=None, task_id=None, dispatch_id=None, payload=None):
    conn.execute(
        "INSERT INTO events (run_id, task_id, dispatch_id, event_type, payload) VALUES (?,?,?,?,?)",
        (run_id, task_id, dispatch_id, event_type, json.dumps(payload, ensure_ascii=False) if payload else None),
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init_db(args):
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = schema_path().read_text(encoding="utf-8")
    conn = sqlite3.connect(str(path))
    conn.executescript(schema)
    conn.close()
    out({"status": "ok", "db_path": str(path), "schema": str(schema_path())})


def cmd_db_path(args):
    out({"db_path": str(default_db_path()), "exists": default_db_path().exists()})


def cmd_run_create(args):
    conn = get_db()
    run_id = gen_id("run")
    conn.execute(
        "INSERT INTO runs (id, objective, workspace_id, metadata) VALUES (?,?,?,?)",
        (run_id, args.objective, args.workspace, json.dumps({"dispatcher": "hermes"}, ensure_ascii=False)),
    )
    log_event(conn, "run_created", run_id=run_id, payload={"objective": args.objective})
    conn.commit()
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def cmd_run_list(args):
    conn = get_db()
    rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
    out([row_to_dict(r) for r in rows], table=args.table)
    conn.close()


def cmd_run_status(args):
    conn = get_db()
    row = conn.execute("SELECT * FROM run_summary WHERE id=?", (args.run,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def parse_list_arg(value: str) -> str:
    """Accept either a JSON array ('["a","b"]') or a comma-separated list ('a,b').
    Always returns a valid JSON array string."""
    if not value or value == "[]":
        return "[]"
    value = value.strip()
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    # Fallback: comma-separated
    items = [s.strip() for s in value.split(",") if s.strip()]
    return json.dumps(items, ensure_ascii=False)


def cmd_task_add(args):
    conn = get_db()
    # Validate run exists
    run = conn.execute("SELECT id FROM runs WHERE id=?", (args.run,)).fetchone()
    if not run:
        out({"error": f"run not found: {args.run}"})
        sys.exit(1)
    task_id = gen_id("task")
    deps = parse_list_arg(args.deps)
    conn.execute(
        "INSERT INTO tasks (id, run_id, spec, deps, assigned_role, preferred_agent) VALUES (?,?,?,?,?,?)",
        (task_id, args.run, args.spec, deps, args.role, args.agent),
    )
    log_event(conn, "task_created", run_id=args.run, task_id=task_id, payload={"spec": args.spec[:100]})
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def cmd_task_list(args):
    conn = get_db()
    q = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if args.run:
        q += " AND run_id=?"
        params.append(args.run)
    if args.status:
        q += " AND status=?"
        params.append(args.status)
    q += " ORDER BY created_at"
    rows = conn.execute(q, params).fetchall()
    out([row_to_dict(r) for r in rows], table=args.table)
    conn.close()


def cmd_task_ready(args):
    """List tasks whose deps are all completed (uses ready_tasks view)."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM ready_tasks WHERE run_id=?", (args.run,)).fetchall()
    out([row_to_dict(r) for r in rows], table=args.table)
    conn.close()


def cmd_task_get(args):
    conn = get_db()
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (args.id,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def cmd_dispatch_start(args):
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (args.task,)).fetchone()
    if not task:
        out({"error": f"task not found: {args.task}"})
        sys.exit(1)
    if task["status"] not in ("pending", "ready", "failed"):
        out({"error": f"task status must be pending/ready/failed to dispatch, got: {task['status']}"})
        sys.exit(1)

    disp_id = gen_id("disp")
    conn.execute(
        """INSERT INTO dispatches (id, task_id, run_id, agent_name, agent_kind, pane_id, tab_id, workspace_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        (disp_id, args.task, task["run_id"], args.agent_name, args.agent_kind, args.pane, args.tab, task["workspace_id"] if "workspace_id" in task.keys() else None),
    )
    conn.execute(
        "UPDATE tasks SET status='dispatched', retry_count=retry_count+1, updated_at=? WHERE id=?",
        (now(), args.task),
    )
    log_event(conn, "dispatch_started", run_id=task["run_id"], task_id=args.task, dispatch_id=disp_id,
               payload={"agent": args.agent_name, "kind": args.agent_kind, "pane": args.pane})
    conn.commit()
    row = conn.execute("SELECT * FROM dispatches WHERE id=?", (disp_id,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def cmd_dispatch_complete(args):
    conn = get_db()
    disp = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
    if not disp:
        out({"error": f"dispatch not found: {args.dispatch}"})
        sys.exit(1)

    files = parse_list_arg(args.files)
    conn.execute(
        """UPDATE dispatches SET status='completed', outcome=?, files_modified=?, summary=?, completed_at=?
           WHERE id=?""",
        (args.outcome, files, args.summary, now(), args.dispatch),
    )
    task_status = "completed" if args.outcome == "succeeded" else "failed"
    result = json.dumps({"outcome": args.outcome, "summary": args.summary,
                          "files_modified": json.loads(files)}, ensure_ascii=False)
    conn.execute(
        "UPDATE tasks SET status=?, result=?, updated_at=?, completed_at=? WHERE id=?",
        (task_status, result, now(), now() if task_status == "completed" else None, disp["task_id"]),
    )
    event_type = "worker_done" if args.outcome == "succeeded" else "worker_failed"
    log_event(conn, event_type, run_id=disp["run_id"], task_id=disp["task_id"], dispatch_id=args.dispatch,
               payload={"outcome": args.outcome, "summary": args.summary})
    conn.commit()
    row = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def cmd_dispatch_fail(args):
    conn = get_db()
    disp = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
    if not disp:
        out({"error": f"dispatch not found: {args.dispatch}"})
        sys.exit(1)
    conn.execute(
        "UPDATE dispatches SET status='failed', outcome='failed', failure_reason=?, completed_at=? WHERE id=?",
        (args.reason, now(), args.dispatch),
    )
    # Check retry eligibility
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (disp["task_id"],)).fetchone()
    if task["retry_count"] < task["max_retries"]:
        new_status = "pending"  # can be retried
    else:
        new_status = "failed"
    conn.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (new_status, now(), disp["task_id"]))
    log_event(conn, "worker_failed", run_id=disp["run_id"], task_id=disp["task_id"], dispatch_id=args.dispatch,
               payload={"reason": args.reason, "retry_eligible": new_status == "pending"})
    conn.commit()
    out({"dispatch_id": args.dispatch, "task_status": new_status, "reason": args.reason})
    conn.close()


def cmd_dispatch_list(args):
    conn = get_db()
    q = "SELECT * FROM dispatches WHERE 1=1"
    params = []
    if args.run:
        q += " AND run_id=?"
        params.append(args.run)
    if args.task:
        q += " AND task_id=?"
        params.append(args.task)
    q += " ORDER BY started_at DESC"
    rows = conn.execute(q, params).fetchall()
    out([row_to_dict(r) for r in rows], table=args.table)
    conn.close()


def cmd_gate_create(args):
    conn = get_db()
    gate_id = gen_id("gate")
    conn.execute(
        "INSERT INTO gates (id, run_id, task_id, gate_type, question) VALUES (?,?,?,?,?)",
        (gate_id, args.run, args.task, args.type, args.question),
    )
    log_event(conn, "gate_created", run_id=args.run, task_id=args.task, payload={"type": args.type})
    conn.commit()
    row = conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def cmd_gate_resolve(args):
    conn = get_db()
    gate = conn.execute("SELECT * FROM gates WHERE id=?", (args.gate,)).fetchone()
    if not gate:
        out({"error": f"gate not found: {args.gate}"})
        sys.exit(1)
    evidence = args.evidence or "{}"
    conn.execute(
        "UPDATE gates SET resolution=?, resolved_by=?, resolved_at=?, evidence=? WHERE id=?",
        (args.resolution, args.by, now(), evidence, args.gate),
    )
    event_type = "gate_passed" if args.resolution == "pass" else "gate_failed"
    log_event(conn, event_type, run_id=gate["run_id"], task_id=gate["task_id"],
               payload={"resolution": args.resolution})
    conn.commit()
    out({"gate_id": args.gate, "resolution": args.resolution})
    conn.close()


def cmd_event_log(args):
    conn = get_db()
    q = "SELECT * FROM events WHERE 1=1"
    params = []
    if args.run:
        q += " AND run_id=?"
        params.append(args.run)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(q, params).fetchall()
    out([row_to_dict(r) for r in reversed(rows)], table=args.table)
    conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Herdr Phalanx orchestration DB CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("db-path").set_defaults(func=cmd_db_path)

    sp = sub.add_parser("run-create")
    sp.add_argument("--objective", required=True)
    sp.add_argument("--workspace")
    sp.set_defaults(func=cmd_run_create)

    sp = sub.add_parser("run-list")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_run_list)

    sp = sub.add_parser("run-status")
    sp.add_argument("--run", required=True)
    sp.set_defaults(func=cmd_run_status)

    sp = sub.add_parser("task-add")
    sp.add_argument("--run", required=True)
    sp.add_argument("--spec", required=True)
    sp.add_argument("--deps", default="[]")
    sp.add_argument("--role")
    sp.add_argument("--agent")
    sp.set_defaults(func=cmd_task_add)

    sp = sub.add_parser("task-list")
    sp.add_argument("--run")
    sp.add_argument("--status")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_task_list)

    sp = sub.add_parser("task-ready")
    sp.add_argument("--run", required=True)
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_task_ready)

    sp = sub.add_parser("task-get")
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_task_get)

    sp = sub.add_parser("dispatch-start")
    sp.add_argument("--task", required=True)
    sp.add_argument("--agent-name", required=True)
    sp.add_argument("--agent-kind", required=True)
    sp.add_argument("--pane", required=True)
    sp.add_argument("--tab")
    sp.set_defaults(func=cmd_dispatch_start)

    sp = sub.add_parser("dispatch-complete")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--outcome", required=True, choices=["succeeded", "failed"])
    sp.add_argument("--files", default="[]")
    sp.add_argument("--summary", default="")
    sp.set_defaults(func=cmd_dispatch_complete)

    sp = sub.add_parser("dispatch-fail")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_dispatch_fail)

    sp = sub.add_parser("dispatch-list")
    sp.add_argument("--run")
    sp.add_argument("--task")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_dispatch_list)

    sp = sub.add_parser("gate-create")
    sp.add_argument("--run", required=True)
    sp.add_argument("--type", required=True)
    sp.add_argument("--question", required=True)
    sp.add_argument("--task")
    sp.set_defaults(func=cmd_gate_create)

    sp = sub.add_parser("gate-resolve")
    sp.add_argument("--gate", required=True)
    sp.add_argument("--resolution", required=True, choices=["pass", "fail", "escalate"])
    sp.add_argument("--by", default="dispatcher")
    sp.add_argument("--evidence", default="{}")
    sp.set_defaults(func=cmd_gate_resolve)

    sp = sub.add_parser("event-log")
    sp.add_argument("--run")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_event_log)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
