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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    if columns and "coordinator" not in columns:
        conn.execute("ALTER TABLE runs ADD COLUMN coordinator TEXT NOT NULL DEFAULT 'hermes'")
    conn.executescript(schema)
    conn.close()
    out({"status": "ok", "db_path": str(path), "schema": str(schema_path())})


def cmd_db_path(args):
    out({"db_path": str(default_db_path()), "exists": default_db_path().exists()})


def cmd_run_create(args):
    conn = get_db()
    run_id = gen_id("run")
    metadata = json.loads(args.metadata or "{}")
    metadata["dispatcher"] = args.coordinator
    conn.execute(
        "INSERT INTO runs (id, objective, workspace_id, coordinator, metadata) VALUES (?,?,?,?,?)",
        (run_id, args.objective, args.workspace, args.coordinator, json.dumps(metadata, ensure_ascii=False)),
    )
    log_event(conn, "run_created", run_id=run_id,
              payload={"objective": args.objective, "coordinator": args.coordinator})
    conn.commit()
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def assert_run_owner(
    conn: sqlite3.Connection, run_id: str, coordinator: str, require_active: bool = False
) -> sqlite3.Row:
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise ValueError(f"run not found: {run_id}")
    if run["coordinator"] != coordinator:
        raise PermissionError(f"run {run_id} is owned by {run['coordinator']}")
    if require_active and run["status"] != "active":
        raise ValueError(f"run status must be active, got: {run['status']}")
    return run


def transition_run(conn: sqlite3.Connection, run_id: str, coordinator: str, status: str) -> dict:
    if status not in ("completed", "aborted"):
        raise ValueError(f"unsupported run status: {status}")
    run = assert_run_owner(conn, run_id, coordinator)
    if run["status"] != "active":
        raise ValueError(f"run status must be active, got: {run['status']}")
    conn.execute("UPDATE runs SET status=?, completed_at=? WHERE id=?", (status, now(), run_id))
    log_event(conn, f"run_{status}", run_id=run_id, payload={"coordinator": coordinator})
    conn.commit()
    result = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    return row_to_dict(result)


def cmd_run_complete(args):
    out(transition_run(get_db(), args.run, args.coordinator, "completed"))


def cmd_run_abort(args):
    out(transition_run(get_db(), args.run, args.coordinator, "aborted"))


CAPABILITY_LEVELS = {"declared", "discovered", "ready", "verified", "degraded", "unknown"}


def cmd_capability_record(args):
    if args.level not in CAPABILITY_LEVELS:
        raise ValueError(f"unsupported capability level: {args.level}")
    evidence = json.loads(args.evidence)
    conn = get_db()
    conn.execute(
        """INSERT INTO capability_observations
           (agent_kind, profile, level, command, command_type, executable_path, version,
            herdr_version, integration, launch_args, evidence)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            args.kind, args.profile, args.level, args.command, args.command_type,
            args.executable_path, args.version, args.herdr_version, args.integration,
            args.launch_args, json.dumps(evidence, ensure_ascii=False),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM capability_observations WHERE id=last_insert_rowid()").fetchone()
    out(row_to_dict(row))
    conn.close()


def cmd_capability_list(args):
    conn = get_db()
    source = "current_capabilities" if args.current else "capability_observations"
    q = f"SELECT * FROM {source} WHERE agent_kind=?"
    params = [args.kind]
    if args.profile is not None:
        q += " AND profile IS ?"
        params.append(args.profile)
    q += " ORDER BY id DESC"
    rows = conn.execute(q, params).fetchall()
    out([row_to_dict(row) for row in rows], table=args.table)
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


def parse_list_arg(value) -> str:
    """Accept JSON array ('["a","b"]'), comma-separated ('a,b'), or brackets without quotes ('[a, b]').
    Always returns a valid JSON array string."""
    if not value or value == "[]":
        return "[]"
    value = str(value).strip()
    # Try JSON parse first (handles ["a","b"] and [])
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        # JSON parse failed — likely [a, b] without quotes. Strip brackets.
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1]
    # Fallback: comma-separated
    items = [s.strip().strip('"').strip("'") for s in value.split(",") if s.strip()]
    return json.dumps(items, ensure_ascii=False)


def parse_worker_done(text: str) -> dict:
    """Parse TASK_COMPLETE marker from agent output.

    Compatible with:
    - Standard format: ## TASK_COMPLETE\\noutcome: succeeded\\nfiles_modified: [\"a.py\"]\\nsummary: ...
    - omp rendered (no ##): TASK_COMPLETE\\noutcome: ...
    - omp rendered (blank line between marker and fields): TASK_COMPLETE\\n\\noutcome: ...
    - OpenCode rendered: TASK_COMPLETE     outcome: succeeded\n...
    - Missing fields: defaults applied

    Returns: {parsed: bool, dispatch_id: str, outcome: str, files_modified: list, summary: str, raw_marker: str}
    """
    result = {
        "parsed": False,
        "dispatch_id": "",
        "outcome": "succeeded",
        "files_modified": [],
        "summary": "",
        "raw_marker": "",
    }
    if not text:
        return result

    # Find TASK_COMPLETE marker (with or without ## prefix)
    import re
    marker_pattern = re.compile(r"(?:##\s*)?TASK_COMPLETE\s*$", re.MULTILINE)
    tokens = list(re.finditer(r"(?:##\s*)?TASK_COMPLETE\b", text))
    if not tokens:
        return result
    match = tokens[-1]
    if not marker_pattern.fullmatch(match.group(0)):
        return result
    if not re.match(
        r"[ \t]*(?:\r?\n|(?=(?:dispatch_id|outcome|files_modified|summary)\s*:)|$)",
        text[match.end():],
    ):
        return result

    result["parsed"] = True
    result["raw_marker"] = match.group(0).strip()

    # Extract content after marker, up to next ## heading or end of text
    after = text[match.end():]
    # Stop at next ## heading (omp may render subsequent content as new heading)
    next_heading = re.search(r"\n##\s", after)
    if next_heading:
        after = after[:next_heading.start()]

    # Parse key: value lines (tolerate blank lines between fields)
    fields = {}
    for line in after.split("\n"):
        line = line.strip()
        if not line or line.startswith("##"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip()
            if key in ("dispatch_id", "outcome", "files_modified", "summary"):
                fields[key] = val
        # Stop after all completion fields; summary may be multi-line but we take its first line.
        if len(fields) >= 4:
            break

    if "dispatch_id" in fields:
        result["dispatch_id"] = fields["dispatch_id"]

    if "outcome" in fields:
        result["outcome"] = fields["outcome"].lower()
        if result["outcome"] not in ("succeeded", "failed"):
            result["outcome"] = "succeeded"

    if "files_modified" in fields:
        files_str = fields["files_modified"]
        # Parse using parse_list_arg (handles JSON, comma, brackets-no-quotes)
        try:
            result["files_modified"] = json.loads(parse_list_arg(files_str))
        except (json.JSONDecodeError, TypeError):
            result["files_modified"] = []

    if "summary" in fields:
        result["summary"] = fields["summary"]

    return result


def cmd_smoke_verify_managed(args):
    """Persist a managed smoke Dispatch from Coordinator-observed Herdr outcomes."""
    evidence = json.loads(args.evidence)
    parsed = parse_worker_done(args.output) if args.output_read == "succeeded" else {
        "parsed": False, "outcome": None, "files_modified": [], "summary": ""
    }
    complete = (
        args.launch == "succeeded"
        and args.readiness == "ready"
        and args.output_read == "succeeded"
        and parsed["parsed"]
        and parsed["outcome"] == "succeeded"
    )
    failure_reason = None
    if not complete:
        if args.launch != "succeeded":
            failure_reason = "launch failed"
        elif args.readiness != "ready":
            failure_reason = "readiness failed"
        elif args.output_read != "succeeded":
            failure_reason = "output read failed"
        elif not parsed["parsed"]:
            failure_reason = "missing valid TASK_COMPLETE report"
        else:
            failure_reason = "worker reported failure"

    smoke_evidence = {
        **evidence,
        "smoke": {
            "launch": args.launch,
            "readiness": args.readiness,
            "output_read": args.output_read,
            "parser": "succeeded" if parsed["parsed"] else (
                "failed" if args.output_read == "succeeded" else "not_attempted"
            ),
            "reported_outcome": parsed["outcome"],
        },
    }
    conn = get_db()
    try:
        assert_run_owner(conn, args.run, args.coordinator, require_active=True)
        task_id = gen_id("task")
        dispatch_id = gen_id("disp")
        task_status = "completed" if complete else "failed"
        dispatch_status = "completed" if complete else "failed"
        files = json.dumps(parsed["files_modified"], ensure_ascii=False)
        result = json.dumps({
            "outcome": parsed["outcome"] if parsed["parsed"] else "failed",
            "summary": parsed["summary"] if parsed["parsed"] else failure_reason,
            "files_modified": parsed["files_modified"],
        }, ensure_ascii=False)
        conn.execute(
            """INSERT INTO tasks (id, run_id, spec, status, result, completed_at)
               VALUES (?,?,?,?,?,?)""",
            (task_id, args.run, f"Managed smoke verification for {args.kind}", task_status, result, now()),
        )
        conn.execute(
            """INSERT INTO dispatches
               (id, task_id, run_id, agent_name, agent_kind, pane_id, tab_id, status, outcome,
                files_modified, summary, failure_reason, completed_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                dispatch_id, task_id, args.run, args.agent_name, args.kind, args.pane, args.tab,
                dispatch_status, parsed["outcome"] if parsed["parsed"] else "failed", files,
                parsed["summary"] if parsed["parsed"] else None, failure_reason, now(),
                json.dumps({"smoke": True, "profile": args.profile}, ensure_ascii=False),
            ),
        )
        level = "verified" if complete else "degraded"
        conn.execute(
            """INSERT INTO capability_observations (agent_kind, profile, level, evidence)
               VALUES (?,?,?,?)""",
            (args.kind, args.profile, level, json.dumps(smoke_evidence, ensure_ascii=False)),
        )
        capability = conn.execute(
            "SELECT * FROM capability_observations WHERE id=last_insert_rowid()"
        ).fetchone()
        log_event(
            conn,
            "smoke_dispatch_verified" if complete else "smoke_dispatch_degraded",
            run_id=args.run,
            task_id=task_id,
            dispatch_id=dispatch_id,
            payload={"agent_kind": args.kind, "profile": args.profile, "failure_reason": failure_reason},
        )
        conn.commit()
        dispatch = conn.execute("SELECT * FROM dispatches WHERE id=?", (dispatch_id,)).fetchone()
        out({
            "capability_level": level,
            "capability": row_to_dict(capability),
            "dispatch": row_to_dict(dispatch),
        })
    finally:
        conn.close()


def cmd_task_add(args):
    conn = get_db()
    # Validate run exists
    run = conn.execute("SELECT id FROM runs WHERE id=?", (args.run,)).fetchone()
    if not run:
        conn.close()
        out({"error": f"run not found: {args.run}"})
        sys.exit(1)
    try:
        assert_run_owner(conn, args.run, args.coordinator, require_active=True)
    except Exception:
        conn.close()
        raise
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


def cmd_task_claim(args):
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (args.task,)).fetchone()
        if not task:
            raise ValueError(f"task not found: {args.task}")
        assert_run_owner(conn, task["run_id"], args.coordinator, require_active=True)
        if task["status"] != "pending":
            raise ValueError(f"task status must be pending, got: {task['status']}")
        dependency_ready = conn.execute(
            """SELECT 1 FROM ready_tasks WHERE id=? AND run_id=?""", (args.task, task["run_id"])
        ).fetchone()
        if not dependency_ready:
            raise ValueError("task is not dependency-ready")
        capability = conn.execute(
            """SELECT * FROM current_capabilities
               WHERE agent_kind=? AND profile IS ? AND level='verified'""",
            (args.kind, args.profile),
        ).fetchone()
        roles = row_to_dict(capability).get("evidence", {}).get("roles", []) if capability else []
        if task["assigned_role"] not in roles:
            raise ValueError(f"no verified Worker matches role {task['assigned_role']}")

        dispatch_id = gen_id("disp")
        conn.execute(
            """INSERT INTO dispatches (id, task_id, run_id, agent_name, agent_kind, pane_id, tab_id)
               VALUES (?,?,?,?,?,?,?)""",
            (dispatch_id, args.task, task["run_id"], args.agent_name, args.kind, args.pane, args.tab),
        )
        conn.execute(
            "UPDATE tasks SET status='dispatched', retry_count=retry_count+1, updated_at=? WHERE id=?",
            (now(), args.task),
        )
        log_event(
            conn, "task_claimed", run_id=task["run_id"], task_id=args.task, dispatch_id=dispatch_id,
            payload={"agent": args.agent_name, "kind": args.kind, "pane": args.pane, "tab": args.tab},
        )
        conn.commit()
        claimed_task = conn.execute("SELECT * FROM tasks WHERE id=?", (args.task,)).fetchone()
        dispatch = conn.execute("SELECT * FROM dispatches WHERE id=?", (dispatch_id,)).fetchone()
        out({"task": row_to_dict(claimed_task), "dispatch": row_to_dict(dispatch)})
    except Exception:
        conn.rollback()
        raise
    finally:
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
        conn.close()
        out({"error": f"task not found: {args.task}"})
        sys.exit(1)
    if task["status"] not in ("pending", "ready", "failed"):
        conn.close()
        out({"error": f"task status must be pending/ready/failed to dispatch, got: {task['status']}"})
        sys.exit(1)
    try:
        assert_run_owner(conn, task["run_id"], args.coordinator, require_active=True)
    except Exception:
        conn.close()
        raise

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
    raise ValueError("dispatch completion requires a parsed TASK_COMPLETE report")


def cmd_dispatch_fail(args):
    conn = get_db()
    disp = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
    if not disp:
        conn.close()
        out({"error": f"dispatch not found: {args.dispatch}"})
        sys.exit(1)
    try:
        assert_run_owner(conn, disp["run_id"], args.coordinator, require_active=True)
    except Exception:
        conn.close()
        raise
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


def cmd_parse_worker_done(args):
    """Parse TASK_COMPLETE marker from agent output text (pure parsing, no DB write)."""
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    result = parse_worker_done(text)
    out(result)


def cmd_dispatch_complete_from_output(args):
    """Parse TASK_COMPLETE from agent output and complete the dispatch in one step."""
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    parsed = parse_worker_done(text)
    if not parsed["parsed"]:
        raise ValueError("dispatch completion requires a valid TASK_COMPLETE report")

    conn = get_db()
    disp = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
    if not disp:
        conn.close()
        out({"error": f"dispatch not found: {args.dispatch}"})
        sys.exit(1)
    try:
        assert_run_owner(conn, disp["run_id"], args.coordinator, require_active=True)
    except Exception:
        conn.close()
        raise
    if disp["status"] in ("completed", "failed", "blocked", "abandoned"):
        conn.close()
        raise ValueError(f"dispatch already terminal: {disp['status']}")
    if parsed["dispatch_id"] != args.dispatch:
        conn.close()
        raise ValueError("worker report dispatch_id does not match dispatch")

    files_json = json.dumps(parsed["files_modified"], ensure_ascii=False)
    dispatch_status = "completed" if parsed["outcome"] == "succeeded" else "failed"
    conn.execute(
        """UPDATE dispatches SET status=?, outcome=?, files_modified=?, summary=?, completed_at=?
           WHERE id=?""",
        (dispatch_status, parsed["outcome"], files_json, parsed["summary"], now(), args.dispatch),
    )
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (disp["task_id"],)).fetchone()
    if parsed["outcome"] == "succeeded":
        task_status = "completed"
        completed_at = now()
    else:
        task_status = "pending" if task["retry_count"] < task["max_retries"] else "failed"
        completed_at = None
    result = json.dumps({"outcome": parsed["outcome"], "summary": parsed["summary"],
                          "files_modified": parsed["files_modified"]}, ensure_ascii=False)
    conn.execute(
        "UPDATE tasks SET status=?, result=?, updated_at=?, completed_at=? WHERE id=?",
        (task_status, result, now(), completed_at, disp["task_id"]),
    )
    event_type = "worker_done" if parsed["outcome"] == "succeeded" else "worker_failed"
    log_event(conn, event_type, run_id=disp["run_id"], task_id=disp["task_id"], dispatch_id=args.dispatch,
               payload={"outcome": parsed["outcome"], "summary": parsed["summary"], "parsed": True,
                        "retry_eligible": task_status == "pending"})
    conn.commit()
    row = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
    out(row_to_dict(row))
    conn.close()


def cmd_dispatch_block(args):
    evidence = json.loads(args.evidence)
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        disp = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
        if not disp:
            raise ValueError(f"dispatch not found: {args.dispatch}")
        assert_run_owner(conn, disp["run_id"], args.coordinator, require_active=True)
        if disp["status"] in ("completed", "failed", "abandoned"):
            raise ValueError(f"dispatch already terminal: {disp['status']}")
        metadata = row_to_dict(disp).get("metadata") or {}
        metadata.update({"block_state": args.state, "block_evidence": evidence})
        conn.execute(
            "UPDATE dispatches SET status='blocked', failure_reason=?, metadata=? WHERE id=?",
            (args.reason, json.dumps(metadata, ensure_ascii=False), args.dispatch),
        )
        conn.execute("UPDATE tasks SET status='blocked', updated_at=? WHERE id=?", (now(), disp["task_id"]))
        log_event(
            conn, "dispatch_blocked", run_id=disp["run_id"], task_id=disp["task_id"], dispatch_id=args.dispatch,
            payload={"state": args.state, "reason": args.reason, "evidence": evidence},
        )
        conn.commit()
        dispatch = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (disp["task_id"],)).fetchone()
        out({"dispatch": row_to_dict(dispatch), "task": row_to_dict(task), "evidence": evidence})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_dispatch_resolve_block(args):
    if args.decision not in ("retry", "fail"):
        raise ValueError(f"unsupported block decision: {args.decision}")
    evidence = json.loads(args.evidence)
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        disp = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
        if not disp:
            raise ValueError(f"dispatch not found: {args.dispatch}")
        assert_run_owner(conn, disp["run_id"], args.coordinator, require_active=True)
        if disp["status"] != "blocked":
            raise ValueError(f"dispatch status must be blocked, got: {disp['status']}")
        task_status = "pending" if args.decision == "retry" else "failed"
        conn.execute(
            "UPDATE dispatches SET status='failed', outcome='failed', completed_at=? WHERE id=?",
            (now(), args.dispatch),
        )
        conn.execute(
            "UPDATE tasks SET status=?, updated_at=?, completed_at=? WHERE id=?",
            (task_status, now(), now() if task_status == "failed" else None, disp["task_id"]),
        )
        log_event(
            conn, "blocked_dispatch_resolved", run_id=disp["run_id"], task_id=disp["task_id"],
            dispatch_id=args.dispatch, payload={"decision": args.decision, "evidence": evidence},
        )
        conn.commit()
        dispatch = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (disp["task_id"],)).fetchone()
        out({"dispatch": row_to_dict(dispatch), "task": row_to_dict(task)})
    except Exception:
        conn.rollback()
        raise
    finally:
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
    sp.add_argument("--coordinator", default="hermes")
    sp.add_argument("--metadata", default="{}")
    sp.set_defaults(func=cmd_run_create)

    sp = sub.add_parser("run-list")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_run_list)

    sp = sub.add_parser("run-status")
    sp.add_argument("--run", required=True)
    sp.set_defaults(func=cmd_run_status)

    sp = sub.add_parser("run-complete")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.set_defaults(func=cmd_run_complete)

    sp = sub.add_parser("run-abort")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.set_defaults(func=cmd_run_abort)

    sp = sub.add_parser("capability-record")
    sp.add_argument("--kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--level", required=True, choices=sorted(CAPABILITY_LEVELS))
    sp.add_argument("--command")
    sp.add_argument("--command-type")
    sp.add_argument("--executable-path")
    sp.add_argument("--version")
    sp.add_argument("--herdr-version")
    sp.add_argument("--integration")
    sp.add_argument("--launch-args")
    sp.add_argument("--evidence", default="{}")
    sp.set_defaults(func=cmd_capability_record)

    sp = sub.add_parser("capability-list")
    sp.add_argument("--kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--current", action="store_true")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_capability_list)

    sp = sub.add_parser("smoke-verify-managed")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--agent-name", required=True)
    sp.add_argument("--pane")
    sp.add_argument("--tab")
    sp.add_argument("--launch", required=True)
    sp.add_argument("--readiness", required=True)
    sp.add_argument("--output-read", required=True)
    sp.add_argument("--output", default="")
    sp.add_argument("--evidence", default="{}")
    sp.set_defaults(func=cmd_smoke_verify_managed)

    sp = sub.add_parser("task-add")
    sp.add_argument("--run", required=True)
    sp.add_argument("--spec", required=True)
    sp.add_argument("--deps", default="[]")
    sp.add_argument("--role")
    sp.add_argument("--agent")
    sp.add_argument("--coordinator", default="hermes")
    sp.set_defaults(func=cmd_task_add)

    sp = sub.add_parser("task-claim")
    sp.add_argument("--task", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--agent-name", required=True)
    sp.add_argument("--pane", required=True)
    sp.add_argument("--tab")
    sp.set_defaults(func=cmd_task_claim)

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
    sp.add_argument("--coordinator", default="hermes")
    sp.set_defaults(func=cmd_dispatch_start)

    sp = sub.add_parser("dispatch-complete")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--outcome", required=True, choices=["succeeded", "failed"])
    sp.add_argument("--files", default="[]")
    sp.add_argument("--summary", default="")
    sp.add_argument("--coordinator", default="hermes")
    sp.set_defaults(func=cmd_dispatch_complete)

    sp = sub.add_parser("dispatch-fail")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--coordinator", default="hermes")
    sp.set_defaults(func=cmd_dispatch_fail)

    sp = sub.add_parser("dispatch-block")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--state", required=True, choices=["settled", "blocked", "unknown", "timeout"])
    sp.add_argument("--reason", required=True)
    sp.add_argument("--evidence", default="{}")
    sp.set_defaults(func=cmd_dispatch_block)

    sp = sub.add_parser("dispatch-resolve-block")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--decision", required=True, choices=["retry", "fail"])
    sp.add_argument("--evidence", default="{}")
    sp.set_defaults(func=cmd_dispatch_resolve_block)

    sp = sub.add_parser("parse-worker-done", help="Parse TASK_COMPLETE marker from agent output (pure parsing)")
    sp.add_argument("--text", default="")
    sp.add_argument("--file", help="Read agent output from file instead of --text")
    sp.set_defaults(func=cmd_parse_worker_done)

    sp = sub.add_parser("dispatch-complete-from-output", help="Parse TASK_COMPLETE from output and complete dispatch")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--text", default="")
    sp.add_argument("--file", help="Read agent output from file instead of --text")
    sp.add_argument("--coordinator", default="hermes")
    sp.set_defaults(func=cmd_dispatch_complete_from_output)

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
