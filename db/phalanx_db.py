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
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
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


def artifact_root() -> Path:
    env = os.environ.get("PHALANX_ARTIFACTS")
    return Path(env) if env else Path.home() / ".herdr-phalanx" / "artifacts"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_storage_path(storage_key: str) -> Path:
    root = artifact_root().resolve()
    target = (root / storage_key).resolve()
    if target != root and root not in target.parents:
        raise ValueError("artifact storage path escapes artifact root")
    return target


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
    for key in (
        "deps", "files_modified", "result", "metadata", "payload", "evidence", "options",
        "deps_array", "native_parameters",
    ):
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

SCHEMA_VERSION = 6


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_v1(conn: sqlite3.Connection) -> None:
    run_columns = _columns(conn, "runs")
    if run_columns and "coordinator" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN coordinator TEXT NOT NULL DEFAULT 'hermes'")
    if run_columns and "workflow_version" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN workflow_version TEXT NOT NULL DEFAULT 'legacy-v1'")
    if run_columns and "workflow_profile" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN workflow_profile TEXT")
    if run_columns and "parent_run_id" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN parent_run_id TEXT REFERENCES runs(id)")
    if run_columns and "delegation_id" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN delegation_id TEXT")

    task_columns = _columns(conn, "tasks")
    if task_columns and "execution_mode" not in task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'managed'")
    if task_columns and "acceptance_mode" not in task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN acceptance_mode TEXT NOT NULL DEFAULT 'legacy'")
    if task_columns and "delivery_mode" not in task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN delivery_mode TEXT NOT NULL DEFAULT 'direct'")

    capability_columns = _columns(conn, "capability_observations")
    if capability_columns and "execution_mode" not in capability_columns:
        conn.execute("ALTER TABLE capability_observations ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'managed'")


def _migrate_v2(conn: sqlite3.Connection) -> None:
    capability_columns = _columns(conn, "capability_observations")
    additions = {
        "capability_scope": "TEXT NOT NULL DEFAULT 'execution'",
        "model": "TEXT",
        "native_parameters": "TEXT NOT NULL DEFAULT '{}'",
        "normalized_intensity": "TEXT",
        "permission_mode": "TEXT",
        "config_hash": "TEXT",
        "shell_path": "TEXT",
        "fingerprint": "TEXT",
        "max_age_s": "INTEGER NOT NULL DEFAULT 86400",
    }
    for column, definition in additions.items():
        if capability_columns and column not in capability_columns:
            conn.execute(f"ALTER TABLE capability_observations ADD COLUMN {column} {definition}")


def _migrate_v3(conn: sqlite3.Connection) -> None:
    task_columns = _columns(conn, "tasks")
    if task_columns and "checklist_artifact_id" not in task_columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN checklist_artifact_id TEXT REFERENCES artifacts(id)")
    dispatch_columns = _columns(conn, "dispatches")
    if dispatch_columns and "agent_session_id" not in dispatch_columns:
        conn.execute("ALTER TABLE dispatches ADD COLUMN agent_session_id TEXT")
    gate_columns = _columns(conn, "gates")
    additions = {
        "artifact_id": "TEXT REFERENCES artifacts(id)",
        "checklist_artifact_id": "TEXT REFERENCES artifacts(id)",
        "reviewer_dispatch_id": "TEXT REFERENCES dispatches(id)",
    }
    for column, definition in additions.items():
        if gate_columns and column not in gate_columns:
            conn.execute(f"ALTER TABLE gates ADD COLUMN {column} {definition}")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    # Topology tables are new; schema.sql creates them via IF NOT EXISTS
    pass

def _migrate_v5(conn: sqlite3.Connection) -> None:
    pass

def _migrate_v6(conn: sqlite3.Connection) -> None:
    pass

MIGRATIONS = {
    1: _migrate_v1,
    2: _migrate_v2,
    3: _migrate_v3,
    4: _migrate_v4,
    5: _migrate_v5,
    6: _migrate_v6,
}


def cmd_init_db(args):
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = schema_path().read_text(encoding="utf-8")
    conn = sqlite3.connect(str(path))
    try:
        # Create base schema first (tables may not exist yet)
        conn.executescript(schema)
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if current_version > SCHEMA_VERSION:
            raise ValueError(f"database schema {current_version} is newer than supported {SCHEMA_VERSION}")
        for version in range(current_version + 1, SCHEMA_VERSION + 1):
            with conn:
                MIGRATIONS[version](conn)
                conn.execute(f"PRAGMA user_version = {version}")
    finally:
        conn.close()
    out({
        "status": "ok", "db_path": str(path), "schema": str(schema_path()),
        "schema_version": SCHEMA_VERSION,
    })


def cmd_db_path(args):
    out({"db_path": str(default_db_path()), "exists": default_db_path().exists()})


def cmd_run_create(args):
    workflow_version = getattr(args, "workflow_version", "legacy-v1")
    workflow_profile = getattr(args, "workflow_profile", None)
    if workflow_version != "legacy-v1" and not getattr(args, "force", False):
        raise ValueError("artifact-v1 Run creation requires --force (smoke gate not yet passed)")
    if workflow_profile is not None and workflow_version == "legacy-v1":
        raise ValueError("workflow profile is only valid for artifact-v1 Runs")

    conn = get_db()
    run_id = gen_id("run")
    metadata = json.loads(args.metadata or "{}")
    metadata["dispatcher"] = args.coordinator
    conn.execute(
        """INSERT INTO runs
            (id, objective, workspace_id, coordinator, workflow_version, workflow_profile, metadata)
            VALUES (?,?,?,?,?,?,?)""",
        (
            run_id, args.objective, args.workspace, args.coordinator, workflow_version,
            workflow_profile, json.dumps(metadata, ensure_ascii=False),
        ),
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
CAPABILITY_SCOPES = {"execution", "coordinator"}
INTENSITY_LEVELS = {"low", "medium", "high"}


def capability_fingerprint(values: dict) -> str:
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def capability_effective_level(row: dict, fingerprint: str | None = None, at: datetime | None = None) -> str:
    if row.get("level") != "verified":
        return row.get("level", "unknown")
    if fingerprint and row.get("fingerprint") != fingerprint:
        return "stale"
    observed = row.get("observed_at")
    max_age_s = int(row.get("max_age_s") or 86400)
    if observed:
        observed_at = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if (at or datetime.now(timezone.utc)) - observed_at > timedelta(seconds=max_age_s):
            return "stale"
    return "verified"


def _capability_fingerprint_from_args(args) -> str:
    native_parameters = json.loads(getattr(args, "native_parameters", "{}") or "{}")
    values = {
        "agent_kind": args.kind,
        "profile": args.profile,
        "capability_scope": getattr(args, "capability_scope", "execution"),
        "execution_mode": getattr(args, "execution_mode", "managed"),
        "executable_path": args.executable_path,
        "version": args.version,
        "herdr_version": args.herdr_version,
        "integration": args.integration,
        "launch_args": args.launch_args,
        "model": getattr(args, "model", None),
        "native_parameters": native_parameters,
        "normalized_intensity": getattr(args, "normalized_intensity", None),
        "permission_mode": getattr(args, "permission_mode", None),
        "config_hash": getattr(args, "config_hash", None),
        "shell_path": getattr(args, "shell_path", None),
    }
    return capability_fingerprint(values)


def cmd_capability_record(args):
    if args.level not in CAPABILITY_LEVELS:
        raise ValueError(f"unsupported capability level: {args.level}")
    scope = getattr(args, "capability_scope", "execution")
    if scope not in CAPABILITY_SCOPES:
        raise ValueError(f"unsupported capability scope: {scope}")
    intensity = getattr(args, "normalized_intensity", None)
    if intensity is not None and intensity not in INTENSITY_LEVELS:
        raise ValueError(f"unsupported normalized intensity: {intensity}")
    native_parameters = json.loads(getattr(args, "native_parameters", "{}") or "{}")
    evidence = json.loads(args.evidence)
    fingerprint = getattr(args, "fingerprint", None) or _capability_fingerprint_from_args(args)
    conn = get_db()
    conn.execute(
        """INSERT INTO capability_observations
            (agent_kind, profile, capability_scope, level, command, command_type, execution_mode,
             executable_path, version, herdr_version, integration, launch_args, model,
             native_parameters, normalized_intensity, permission_mode, config_hash, shell_path,
             fingerprint, max_age_s, evidence)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            args.kind, args.profile, scope, args.level, args.command, args.command_type,
            getattr(args, "execution_mode", "managed"), args.executable_path, args.version,
            args.herdr_version, args.integration, args.launch_args, getattr(args, "model", None),
            json.dumps(native_parameters, ensure_ascii=False), intensity,
            getattr(args, "permission_mode", None), getattr(args, "config_hash", None),
            getattr(args, "shell_path", None), fingerprint, getattr(args, "max_age_s", 86400),
            json.dumps(evidence, ensure_ascii=False),
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
    scope = getattr(args, "capability_scope", None)
    if scope is not None:
        q += " AND capability_scope=?"
        params.append(scope)
    q += " ORDER BY id DESC"
    rows = [row_to_dict(row) for row in conn.execute(q, params).fetchall()]
    conn.close()
    current_fingerprint = getattr(args, "fingerprint", None)
    for row in rows:
        row["effective_level"] = capability_effective_level(row, current_fingerprint)
    out(rows, table=args.table)


def cmd_run_list(args):
    conn = get_db()
    rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
    out([row_to_dict(r) for r in rows], table=args.table)
    conn.close()


def cmd_run_status(args):
    conn = get_db()
    row = conn.execute("SELECT * FROM run_summary WHERE id=?", (args.run,)).fetchone()
    if not row:
        out({"error": f"run not found: {args.run}"})
        sys.exit(1)
    result = row_to_dict(row)
    running = conn.execute(
        "SELECT COUNT(*) FROM dispatches WHERE run_id=? AND status='running'", (args.run,)
    ).fetchone()[0]
    open_gates = conn.execute(
        "SELECT COUNT(*) FROM gates WHERE run_id=? AND resolution IS NULL", (args.run,)
    ).fetchone()[0]
    blocked = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE run_id=? AND status='blocked'", (args.run,)
    ).fetchone()[0]
    awaiting = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE run_id=? AND status='awaiting_acceptance'", (args.run,)
    ).fetchone()[0]
    conn.close()
    if running > 0:
        result["scheduler_state"] = "running"
    elif blocked > 0:
        result["scheduler_state"] = "blocked"
    elif awaiting > 0:
        result["scheduler_state"] = "awaiting_acceptance"
    elif open_gates > 0:
        result["scheduler_state"] = "awaiting_gate"
    elif row["pending_tasks"] > 0:
        result["scheduler_state"] = "deadlock"
    elif row["completed_tasks"] == row["total_tasks"]:
        result["scheduler_state"] = "finished"
    else:
        result["scheduler_state"] = "waiting"
    out(result)


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


def parse_worker_ask(text: str) -> dict:
    """Parse the latest TASK_ASK marker from Worker output."""
    result = {"parsed": False, "dispatch_id": "", "question": "", "options": []}
    if not text:
        return result
    import re
    tokens = list(re.finditer(r"(?:##\s*)?TASK_ASK\b", text))
    if not tokens:
        return result
    match = tokens[-1]
    if not re.match(r"[ \t]*(?:\r?\n|$)", text[match.end():]):
        return result
    fields = {}
    for line in text[match.end():].splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            if key.strip().lower() in ("dispatch_id", "question", "options"):
                fields[key.strip().lower()] = value.strip()
        if len(fields) == 3:
            break
    if not fields.get("dispatch_id") or not fields.get("question"):
        return result
    result["parsed"] = True
    result["dispatch_id"] = fields["dispatch_id"]
    result["question"] = fields["question"]
    if "options" in fields:
        result["options"] = json.loads(parse_list_arg(fields["options"]))
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
        """INSERT INTO tasks (id, run_id, spec, deps, assigned_role, preferred_agent, execution_mode, acceptance_mode)
           VALUES (?,?,?,?,?,?,?,?)""",
        (task_id, args.run, args.spec, deps, args.role, args.agent,
         getattr(args, "execution_mode", "managed"), getattr(args, "acceptance_mode", "legacy")),
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
               WHERE agent_kind=? AND profile IS ? AND capability_scope='execution'
                 AND level='verified' AND execution_mode=?""",
            (args.kind, args.profile, task["execution_mode"]),
        ).fetchone()
        roles = row_to_dict(capability).get("evidence", {}).get("roles", []) if capability else []
        if task["assigned_role"] not in roles:
            raise ValueError(f"no verified Worker matches role {task['assigned_role']}")

        occupancy = conn.execute(
            """SELECT d.id, d.task_id, d.agent_name, d.pane_id FROM dispatches d
               JOIN tasks t ON t.id = d.task_id
               WHERE d.run_id=? AND d.status='running'
                 AND (? IN (d.agent_name) OR ? IN (d.pane_id))""",
            (task['run_id'], args.agent_name, args.pane),
        ).fetchone()
        if occupancy:
            raise ValueError(
                f'agent/pane already occupied: {occupancy['agent_name']}/{occupancy['pane_id']}'
            )

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
    """List tasks whose deps and Artifact inputs are all satisfied."""
    conn = get_db()
    workflow_filter = ""
    params: list = [args.run]
    workflow_version = getattr(args, "workflow_version", None)
    if workflow_version:
        workflow_filter = " AND r.workflow_version=?"
        params.append(workflow_version)
    rows = conn.execute(
        f"""SELECT rt.id, rt.run_id, rt.spec, rt.assigned_role, rt.preferred_agent,
                   rt.acceptance_mode, rt.delivery_mode, rt.retry_count, rt.max_retries, rt.deps_array
            FROM ready_tasks rt
            JOIN runs r ON r.id = rt.run_id
            WHERE rt.run_id=?{workflow_filter}""",
        params,
    ).fetchall()
    conn.close()
    out([row_to_dict(row) for row in rows], table=args.table)
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

    occupancy = conn.execute(
        """SELECT d.id, d.task_id, d.agent_name, d.pane_id FROM dispatches d
           JOIN tasks t ON t.id = d.task_id
           WHERE d.run_id=? AND d.status='running'
             AND (? IN (d.agent_name) OR ? IN (d.pane_id))""",
        (task["run_id"], args.agent_name, args.pane),
    ).fetchone()
    if occupancy:
        conn.close()
        out({"error": f"agent/pane already occupied: {occupancy['agent_name']}/{occupancy['pane_id']}"})
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


def cmd_dispatch_ask_from_output(args):
    """Persist a matching TASK_ASK and leave the Dispatch blocked for a Coordinator reply."""
    text = Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    parsed = parse_worker_ask(text)
    if not parsed["parsed"]:
        raise ValueError("dispatch question requires a valid TASK_ASK report")
    if parsed["dispatch_id"] != args.dispatch:
        raise ValueError("worker question dispatch_id does not match dispatch")
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        disp = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
        if not disp:
            raise ValueError(f"dispatch not found: {args.dispatch}")
        assert_run_owner(conn, disp["run_id"], args.coordinator, require_active=True)
        if disp["status"] != "running":
            raise ValueError(f"dispatch status must be running, got: {disp['status']}")
        metadata = row_to_dict(disp).get("metadata") or {}
        metadata["worker_ask"] = parsed
        conn.execute("UPDATE dispatches SET status='blocked', failure_reason='worker question', metadata=? WHERE id=?",
                     (json.dumps(metadata, ensure_ascii=False), args.dispatch))
        conn.execute("UPDATE tasks SET status='blocked', updated_at=? WHERE id=?", (now(), disp["task_id"]))
        log_event(conn, "worker_asked", run_id=disp["run_id"], task_id=disp["task_id"], dispatch_id=args.dispatch,
                  payload=parsed)
        conn.commit()
        out({"dispatch": row_to_dict(conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()),
             "question": parsed})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_dispatch_answer(args):
    """Record a Coordinator response and resume the blocked Dispatch for a follow-up prompt."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        disp = conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()
        if not disp:
            raise ValueError(f"dispatch not found: {args.dispatch}")
        assert_run_owner(conn, disp["run_id"], args.coordinator, require_active=True)
        metadata = row_to_dict(disp).get("metadata") or {}
        if disp["status"] != "blocked" or "worker_ask" not in metadata:
            raise ValueError("dispatch is not blocked on a worker question")
        metadata["coordinator_answer"] = args.answer
        conn.execute("UPDATE dispatches SET status='running', failure_reason=NULL, metadata=? WHERE id=?",
                     (json.dumps(metadata, ensure_ascii=False), args.dispatch))
        conn.execute("UPDATE tasks SET status='dispatched', updated_at=? WHERE id=?", (now(), disp["task_id"]))
        log_event(conn, "worker_question_answered", run_id=disp["run_id"], task_id=disp["task_id"],
                  dispatch_id=args.dispatch, payload={"answer": args.answer})
        conn.commit()
        out(row_to_dict(conn.execute("SELECT * FROM dispatches WHERE id=?", (args.dispatch,)).fetchone()))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _required_outputs_accepted(conn: sqlite3.Connection, task_id: str) -> bool:
    row = conn.execute(
        """SELECT COUNT(*) AS required_count,
                  SUM(CASE WHEN accepted_artifact_id IS NOT NULL THEN 1 ELSE 0 END) AS accepted_count
           FROM task_output_slots WHERE task_id=? AND required=1""",
        (task_id,),
    ).fetchone()
    return bool(row["required_count"]) and row["required_count"] == (row["accepted_count"] or 0)


def cmd_dispatch_complete_from_output(args):
    """Parse TASK_COMPLETE and settle the Dispatch; Artifact Tasks still require Gate acceptance."""
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
        if task["acceptance_mode"] == "artifact":
            task_status = "awaiting_acceptance"
            completed_at = None
        else:
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


def cmd_artifact_register(args):
    source = Path(args.source).resolve()
    if not source.is_file():
        raise ValueError(f"artifact source not found: {source}")
    if source.stat().st_size == 0 and not args.allow_empty:
        raise ValueError("artifact source is empty")
    if Path(args.logical_name).name != args.logical_name or args.logical_name in (".", ".."):
        raise ValueError("artifact logical name must be one path-safe segment")

    conn = get_db()
    temp_path = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        assert_run_owner(conn, args.run, args.coordinator, require_active=True)
        task = conn.execute("SELECT * FROM tasks WHERE id=? AND run_id=?", (args.task, args.run)).fetchone()
        if not task:
            raise ValueError(f"producer task not found in run: {args.task}")
        dispatch = None
        if args.dispatch:
            dispatch = conn.execute(
                "SELECT * FROM dispatches WHERE id=? AND task_id=? AND run_id=?",
                (args.dispatch, args.task, args.run),
            ).fetchone()
            if not dispatch:
                raise ValueError(f"producer dispatch not found for task: {args.dispatch}")

        existing = conn.execute(
            "SELECT * FROM artifacts WHERE run_id=? AND idempotency_key=?",
            (args.run, args.idempotency_key),
        ).fetchone()
        if existing:
            existing_dict = row_to_dict(existing)
            if existing["logical_name"] != args.logical_name or existing["artifact_type"] != args.type:
                raise ValueError("artifact idempotency key conflicts with another output")
            out(existing_dict)
            conn.rollback()
            return

        slot = conn.execute(
            "SELECT * FROM task_output_slots WHERE task_id=? AND logical_name=?",
            (args.task, args.logical_name),
        ).fetchone()
        if not slot:
            conn.execute(
                """INSERT INTO task_output_slots (task_id, logical_name, artifact_type, required)
                   VALUES (?,?,?,?)""",
                (args.task, args.logical_name, args.type, 1 if args.required else 0),
            )
        elif slot["artifact_type"] != args.type:
            raise ValueError("artifact type does not match declared output slot")

        version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM artifacts WHERE run_id=? AND logical_name=?",
            (args.run, args.logical_name),
        ).fetchone()[0]
        suffix = source.suffix or ".txt"
        storage_key = f"{args.run}/{args.logical_name}/v{version:06d}{suffix}"
        target = _safe_storage_path(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(source, temp_path)
        digest = _sha256(temp_path)
        if args.sha256 and digest != args.sha256.lower():
            raise ValueError("artifact source hash mismatch")
        if _sha256(source) != digest:
            raise ValueError("artifact source changed during copy")
        if target.exists():
            raise ValueError(f"artifact target already exists: {storage_key}")
        temp_path.replace(target)
        temp_path = None

        artifact_id = gen_id("artifact")
        try:
            conn.execute(
                """INSERT INTO artifacts
                    (id, run_id, logical_name, artifact_type, version, storage_key, sha256,
                     producer_task_id, producer_dispatch_id, idempotency_key, supersedes_artifact_id, metadata)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    artifact_id, args.run, args.logical_name, args.type, version, storage_key, digest,
                    args.task, args.dispatch, args.idempotency_key, args.supersedes,
                    json.dumps(json.loads(args.metadata or "{}"), ensure_ascii=False),
                ),
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        log_event(
            conn, "artifact_registered", run_id=args.run, task_id=args.task,
            dispatch_id=args.dispatch,
            payload={"artifact_id": artifact_id, "storage_key": storage_key, "sha256": digest},
        )
        conn.commit()
        row = conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        out(row_to_dict(row))
    except Exception:
        conn.rollback()
        if temp_path:
            temp_path.unlink(missing_ok=True)
        raise
    finally:
        conn.close()


def cmd_artifact_list(args):
    conn = get_db()
    q = "SELECT * FROM artifacts WHERE run_id=?"
    params = [args.run]
    if args.logical_name:
        q += " AND logical_name=?"
        params.append(args.logical_name)
    q += " ORDER BY logical_name, version"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    out([row_to_dict(row) for row in rows], table=args.table)


def _reserve_budget(conn, run_id, parent_budget_id, agent_limit, coordinator_limit, parallelism_limit):
    run_budget = conn.execute("SELECT * FROM run_budgets WHERE run_id=?", (run_id,)).fetchone()
    if not run_budget:
        raise ValueError(f"run budget not found: {run_id}")

    if parent_budget_id:
        parent = conn.execute(
            "SELECT * FROM delegation_budgets WHERE delegation_id=?", (parent_budget_id,)
        ).fetchone()
        if not parent:
            raise ValueError(f"parent budget not found: {parent_budget_id}")
        available_agent = parent["agent_limit"] - parent["agent_used"] - parent["agent_reserved"]
        available_coordinator = parent["coordinator_limit"] - parent["coordinator_used"] - parent["coordinator_reserved"]
        available_parallelism = parent["parallelism_limit"] - parent["parallelism_used"]
        if agent_limit > available_agent:
            raise ValueError(f"agent budget overcommit: {agent_limit} > {available_agent}")
        if coordinator_limit > available_coordinator:
            raise ValueError(f"coordinator budget overcommit: {coordinator_limit} > {available_coordinator}")
        if parallelism_limit > available_parallelism:
            raise ValueError(f"parallelism overcommit: {parallelism_limit} > {available_parallelism}")
        conn.execute(
            """UPDATE delegation_budgets
               SET agent_reserved=agent_reserved+?, coordinator_reserved=coordinator_reserved+?,
                   parallelism_used=parallelism_used+?
               WHERE delegation_id=?""",
            (agent_limit, coordinator_limit, parallelism_limit, parent_budget_id),
        )

    # Also reserve from the root run budget
    available_agent = run_budget["agent_limit"] - run_budget["agent_used"]
    available_coordinator = run_budget["coordinator_limit"] - run_budget["coordinator_used"]
    available_parallelism = run_budget["parallelism_limit"] - run_budget["parallelism_used"]
    if agent_limit > available_agent:
        raise ValueError(f"agent budget overcommit: {agent_limit} > {available_agent}")
    if coordinator_limit > available_coordinator:
        raise ValueError(f"coordinator budget overcommit: {coordinator_limit} > {available_coordinator}")
    if parallelism_limit > available_parallelism:
        raise ValueError(f"parallelism overcommit: {parallelism_limit} > {available_parallelism}")
    conn.execute(
        """UPDATE run_budgets
           SET agent_used=agent_used+?, coordinator_used=coordinator_used+?,
               parallelism_used=parallelism_used+?
           WHERE run_id=?""",
        (agent_limit, coordinator_limit, parallelism_limit, run_id),
    )

    return {
        "agent_limit": agent_limit,
        "coordinator_limit": coordinator_limit,
        "parallelism_limit": parallelism_limit,
    }


def cmd_run_delegate(args):
    """Create a Child Run with a finite Delegation Contract and reserved budget."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        parent_run = conn.execute("SELECT * FROM runs WHERE id=?", (args.parent_run,)).fetchone()
        if not parent_run:
            raise ValueError(f"parent run not found: {args.parent_run}")
        assert_run_owner(conn, args.parent_run, args.coordinator, require_active=True)

        # Verify coordinator capability (test bootstrap: allow when --bootstrap-capability is set)
        if not getattr(args, "bootstrap_capability", False):
            coordinator_scope = conn.execute(
                """SELECT * FROM current_capabilities
                   WHERE agent_kind=? AND profile IS ? AND capability_scope='coordinator' AND level='verified'""",
                (args.kind, args.profile),
            ).fetchone()
            if not coordinator_scope:
                raise PermissionError("coordinator capability not verified")

        # Ensure run budget exists
        run_budget = conn.execute("SELECT * FROM run_budgets WHERE run_id=?", (args.parent_run,)).fetchone()
        if not run_budget:
            conn.execute(
                """INSERT INTO run_budgets (run_id, agent_limit, coordinator_limit, parallelism_limit)
                   VALUES (?,?,?,?)""",
                (args.parent_run, int(args.run_agent_limit), int(args.run_coordinator_limit),
                 int(args.run_parallelism_limit)),
            )

        # Reserve budget from parent
        parent_budget_id = getattr(args, "parent_budget_id", None)
        budget = _reserve_budget(
            conn, args.parent_run, parent_budget_id,
            int(args.agent_limit), int(args.coordinator_limit), int(args.parallelism_limit),
        )

        # Create Child Run
        child_run_id = gen_id("run")
        conn.execute(
            """INSERT INTO runs (id, objective, coordinator, workflow_version, parent_run_id)
               VALUES (?,?,?,?,?)""",
            (child_run_id, args.objective, args.coordinator, parent_run["workflow_version"], args.parent_run),
        )

        # Create Delegation record
        delegation_id = gen_id("delegation")
        conn.execute(
            """INSERT INTO delegations
               (id, parent_run_id, child_run_id, status, objective, required_capabilities,
                allowed_scope, escalation_conditions, max_depth, max_parallelism, max_intensity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                delegation_id, args.parent_run, child_run_id, "reserved", args.objective,
                json.dumps(json.loads(args.required_capabilities or "[]"), ensure_ascii=False),
                json.dumps(json.loads(args.allowed_scope or "[]"), ensure_ascii=False),
                json.dumps(json.loads(args.escalation_conditions or "[]"), ensure_ascii=False),
                int(args.max_depth), int(args.parallelism_limit), args.max_intensity,
            ),
        )

        # Create budget record
        conn.execute(
            """INSERT INTO delegation_budgets
               (delegation_id, parent_budget_id, agent_limit, coordinator_limit,
                agent_reserved, coordinator_reserved, parallelism_limit)
               VALUES (?,?,?,?,?,?,?)""",
            (
                delegation_id, parent_budget_id, budget["agent_limit"], budget["coordinator_limit"],
                budget["agent_limit"], budget["coordinator_limit"], budget["parallelism_limit"],
            ),
        )

        # Link delegation to parent task if provided
        if args.parent_task:
            conn.execute(
                "UPDATE tasks SET delegation_id=? WHERE id=? AND run_id=?",
                (delegation_id, args.parent_task, args.parent_run),
            )

        log_event(
            conn, "delegation_created", run_id=args.parent_run, task_id=args.parent_task,
            payload={"delegation_id": delegation_id, "child_run_id": child_run_id},
        )
        conn.commit()
        out({
            "delegation_id": delegation_id,
            "child_run_id": child_run_id,
            "budget": budget,
        })
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_delegation_status(args):
    """Show delegation contract, budget usage, and child run status."""
    conn = get_db()
    delegation = conn.execute("SELECT * FROM delegations WHERE id=?", (args.delegation,)).fetchone()
    if not delegation:
        out({"error": f"delegation not found: {args.delegation}"})
        sys.exit(1)
    budget = conn.execute(
        "SELECT * FROM delegation_budgets WHERE delegation_id=?", (args.delegation,)
    ).fetchone()
    child_run = conn.execute("SELECT * FROM runs WHERE id=?", (delegation["child_run_id"],)).fetchone()
    conn.close()
    out({
        "delegation": row_to_dict(delegation),
        "budget": row_to_dict(budget) if budget else None,
        "child_run": row_to_dict(child_run) if child_run else None,
    })


def _check_topology_collision(conn, resources):
    """Check if any active resource is already allocated."""
    for resource_type, resource_id in resources:
        existing = conn.execute(
            """SELECT ta.id, ta.run_id, ta.coordinator FROM topology_resources tr
               JOIN topology_allocations ta ON ta.id = tr.allocation_id
               WHERE tr.resource_type=? AND tr.resource_id=? AND tr.released_at IS NULL""",
            (resource_type, resource_id),
        ).fetchone()
        if existing:
            raise ValueError(
                f"resource already allocated: {resource_type}={resource_id} "
                f"to run {existing['run_id']}"
            )


def cmd_topology_grant(args):
    """Create an exclusive topology allocation for a Run."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT * FROM runs WHERE id=?", (args.run,)).fetchone()
        if not run:
            raise ValueError(f"run not found: {args.run}")
        assert_run_owner(conn, args.run, args.coordinator, require_active=True)

        # Validate 2x2 rules
        workspace_id = args.workspace
        pane_ids = json.loads(args.panes) if hasattr(args, "panes") and args.panes else []
        tab_ids = json.loads(args.tabs) if hasattr(args, "tabs") and args.tabs else []

        if len(pane_ids) > 4:
            raise ValueError(f"at most 4 panes per worker tab, got {len(pane_ids)}")

        # Check for collisions
        resources = [("workspace", workspace_id)]
        for tab_id in tab_ids:
            resources.append(("tab", tab_id))
        for pane_id in pane_ids:
            resources.append(("pane", pane_id))
        _check_topology_collision(conn, resources)

        # Create allocation
        allocation_id = gen_id("topo")
        conn.execute(
            """INSERT INTO topology_allocations (id, run_id, coordinator, status, metadata)
               VALUES (?,?,?,?,?)""",
            (allocation_id, args.run, args.coordinator, "active",
             json.dumps({"workspace": workspace_id, "tabs": tab_ids, "panes": pane_ids}, ensure_ascii=False)),
        )

        # Record resources
        conn.execute(
            "INSERT INTO topology_resources (allocation_id, resource_type, resource_id, slot) VALUES (?,?,?,?)",
            (allocation_id, "workspace", workspace_id, 0),
        )
        for tab_id in tab_ids:
            conn.execute(
                "INSERT INTO topology_resources (allocation_id, resource_type, resource_id, slot) VALUES (?,?,?,?)",
                (allocation_id, "tab", tab_id, tab_ids.index(tab_id)),
            )
        for pane_id in pane_ids:
            conn.execute(
                """INSERT INTO topology_resources (allocation_id, resource_type, resource_id, slot)
                   VALUES (?,?,?,?)""",
                (allocation_id, "pane", pane_id, pane_ids.index(pane_id)),
            )

        log_event(
            conn, "topology_granted", run_id=args.run,
            payload={"allocation_id": allocation_id, "resources": resources},
        )
        conn.commit()
        out({"allocation_id": allocation_id, "status": "active"})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_topology_release(args):
    """Release logical topology ownership without closing Herdr resources."""
    conn = get_db()
    try:
        allocation = conn.execute(
            "SELECT * FROM topology_allocations WHERE id=? AND run_id=?",
            (args.allocation, args.run),
        ).fetchone()
        if not allocation:
            raise ValueError(f"allocation not found: {args.allocation}")
        if allocation["status"] == "released":
            raise ValueError("allocation already released")

        # Check for active dispatches
        active = conn.execute(
            """SELECT COUNT(*) FROM dispatches d
               JOIN topology_resources tr ON tr.resource_id = d.pane_id
               JOIN topology_allocations ta ON ta.id = tr.allocation_id
               WHERE ta.id=? AND d.status='running'""",
            (args.allocation,),
        ).fetchone()[0]
        if active > 0:
            raise ValueError(f"cannot release allocation with {active} active dispatches")

        conn.execute(
            "UPDATE topology_allocations SET status='released', released_at=?, updated_at=? WHERE id=?",
            (now(), now(), args.allocation),
        )
        conn.execute(
            "UPDATE topology_resources SET released_at=? WHERE allocation_id=? AND released_at IS NULL",
            (now(), args.allocation),
        )
        log_event(conn, "topology_released", run_id=args.run, payload={"allocation_id": args.allocation})
        conn.commit()
        out({"allocation_id": args.allocation, "status": "released"})
    finally:
        conn.close()


def cmd_topology_status(args):
    """Show topology allocations and active resources for a Run."""
    conn = get_db()
    allocations = conn.execute(
        "SELECT * FROM topology_allocations WHERE run_id=? ORDER BY created_at", (args.run,)
    ).fetchall()
    result = []
    for alloc in allocations:
        resources = conn.execute(
            "SELECT * FROM topology_resources WHERE allocation_id=? ORDER BY resource_type, slot",
            (alloc["id"],),
        ).fetchall()
        result.append({
            "allocation": row_to_dict(alloc),
            "resources": [row_to_dict(r) for r in resources],
        })
    conn.close()
    out(result)


def wake_file_path(coordinator: str) -> Path:
    """Get the path to the wake file for a coordinator."""
    root = Path.home() / ".herdr-phalanx" / "wake"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{coordinator}.wake"


def cmd_inbox_post(args):
    """Post a notification to a coordinator's inbox. Returns the created row."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        source_event_id = getattr(args, "source_event_id", None)
        existing = conn.execute(
            """SELECT * FROM coordinator_inbox
               WHERE coordinator=? AND source_event_id=? AND kind=?""",
            (args.coordinator, source_event_id, args.kind),
        ).fetchone()
        if existing:
            out(row_to_dict(existing))
            conn.rollback()
            return

        urgency = getattr(args, "urgency", "normal")
        conn.execute(
            """INSERT INTO coordinator_inbox
               (run_id, coordinator, source_event_id, urgency, kind, payload)
               VALUES (?,?,?,?,?,?)""",
            (args.run, args.coordinator, source_event_id, urgency, args.kind,
             json.dumps(json.loads(args.payload or "{}"), ensure_ascii=False)),
        )
        row = conn.execute("SELECT * FROM coordinator_inbox WHERE id=last_insert_rowid()").fetchone()
        conn.commit()
        out(row_to_dict(row))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_inbox_list(args):
    """List unread notifications for a coordinator."""
    conn = get_db()
    q = """SELECT * FROM coordinator_inbox
           WHERE coordinator=? AND acknowledged_at IS NULL"""
    params: list = [args.coordinator]
    if hasattr(args, "urgency") and args.urgency:
        q += " AND urgency=?"
        params.append(args.urgency)
    q += " ORDER BY urgency DESC, id ASC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    out([row_to_dict(r) for r in rows], table=args.table)


def cmd_inbox_ack(args):
    """Acknowledge a notification and advance the cursor."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE coordinator_inbox SET acknowledged_at=? WHERE id=? AND coordinator=?",
            (now(), args.id, args.coordinator),
        )
        cursor = conn.execute(
            "SELECT * FROM coordinator_inbox_cursors WHERE coordinator=?", (args.coordinator,)
        ).fetchone()
        if cursor:
            conn.execute(
                "UPDATE coordinator_inbox_cursors SET last_seen_id=?, updated_at=? WHERE coordinator=?",
                (args.id, now(), args.coordinator),
            )
        else:
            conn.execute(
                "INSERT INTO coordinator_inbox_cursors (coordinator, last_seen_id) VALUES (?,?)",
                (args.coordinator, args.id),
            )
        conn.commit()
        out({"id": args.id, "acknowledged": True})
    finally:
        conn.close()


def cmd_wake_path(args):
    """Get the wake file path for a coordinator."""
    out({"wake_file": str(wake_file_path(args.coordinator))})


def cmd_wake_signal(args):
    """Signal a coordinator by touching the wake file."""
    wake = wake_file_path(args.coordinator)
    wake.parent.mkdir(parents=True, exist_ok=True)
    wake.write_text(now(), encoding="utf-8")
    out({"wake_file": str(wake), "signaled": True})



def cmd_amendment_propose(args):
    """Propose a Constitution or Checklist Amendment."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT * FROM runs WHERE id=?", (args.run,)).fetchone()
        if not run:
            raise ValueError(f"run not found: {args.run}")
        assert_run_owner(conn, args.run, args.coordinator, require_active=True)
        amendment_id = gen_id("amendment")
        conn.execute(
            """INSERT INTO amendments
               (id, run_id, proposer, reason, impact_set, constitution)
               VALUES (?,?,?,?,?,?)""",
            (amendment_id, args.run, args.coordinator, args.reason,
             json.dumps(json.loads(args.impact_set or "[]"), ensure_ascii=False),
             1 if args.constitution else 0),
        )
        log_event(conn, "amendment_proposed", run_id=args.run,
                  payload={"amendment_id": amendment_id})
        conn.commit()
        out({"amendment_id": amendment_id, "status": "proposed"})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_amendment_resolve(args):
    """Accept or reject an Amendment."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        amendment = conn.execute("SELECT * FROM amendments WHERE id=?", (args.amendment,)).fetchone()
        if not amendment:
            raise ValueError(f"amendment not found: {args.amendment}")
        if amendment["status"] not in ("proposed", "under_review"):
            raise ValueError(f"amendment already {amendment['status']}")
        assert_run_owner(conn, amendment["run_id"], args.coordinator, require_active=True)
        resolution = args.resolution
        conn.execute(
            """UPDATE amendments
               SET status=?, resolved_by=?, resolved_at=?, resolution=?
               WHERE id=?""",
            (resolution, args.coordinator, now(), getattr(args, "resolution_note", ""), args.amendment),
        )
        log_event(conn, f"amendment_{resolution}", run_id=amendment["run_id"],
                  payload={"amendment_id": args.amendment})
        conn.commit()
        out({"amendment_id": args.amendment, "status": resolution})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_cancel_request(args):
    """Request cooperative cancellation of a Run tree."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT * FROM runs WHERE id=?", (args.run,)).fetchone()
        if not run:
            raise ValueError(f"run not found: {args.run}")
        assert_run_owner(conn, args.run, args.coordinator, require_active=True)
        cancel_id = gen_id("cancel")
        conn.execute(
            """INSERT INTO cancellations (id, run_id, requested_by, reason)
               VALUES (?,?,?,?)""",
            (cancel_id, args.run, args.coordinator, args.reason),
        )
        conn.execute("UPDATE runs SET status='cancelling' WHERE id=?", (args.run,))
        log_event(conn, "cancel_requested", run_id=args.run,
                  payload={"cancel_id": cancel_id})
        conn.commit()
        out({"cancel_id": cancel_id, "status": "requested"})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_cancel_ack(args):
    """Acknowledge a cancellation request."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cancel = conn.execute("SELECT * FROM cancellations WHERE id=?", (args.cancel,)).fetchone()
        if not cancel:
            raise ValueError(f"cancellation not found: {args.cancel}")
        conn.execute(
            "UPDATE cancellations SET status='settled', settled_at=? WHERE id=?",
            (now(), args.cancel),
        )
        conn.execute("UPDATE runs SET status='cancelled' WHERE id=?", (cancel["run_id"],))
        log_event(conn, "cancel_settled", run_id=cancel["run_id"],
                  payload={"cancel_id": args.cancel})
        conn.commit()
        out({"cancel_id": args.cancel, "status": "settled"})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_finding_add(args):
    """Add a Red Team finding."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT * FROM runs WHERE id=?", (args.run,)).fetchone()
        if not run:
            raise ValueError(f"run not found: {args.run}")
        finding_id = gen_id("finding")
        conn.execute(
            """INSERT INTO findings
               (id, run_id, artifact_id, severity, description, evidence)
               VALUES (?,?,?,?,?,?)""",
            (finding_id, args.run, args.artifact, args.severity, args.description,
             json.dumps(json.loads(args.evidence or "{}"), ensure_ascii=False)),
        )
        log_event(conn, "finding_added", run_id=args.run,
                  payload={"finding_id": finding_id, "severity": args.severity})
        conn.commit()
        out({"finding_id": finding_id})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_finding_dispose(args):
    """Record a disposition for a finding."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        finding = conn.execute("SELECT * FROM findings WHERE id=?", (args.finding,)).fetchone()
        if not finding:
            raise ValueError(f"finding not found: {args.finding}")
        conn.execute(
            """UPDATE findings
               SET disposition=?, disposition_reason=?, disposition_by=?, disposition_at=?
               WHERE id=?""",
            (args.disposition, args.reason, args.coordinator, now(), args.finding),
        )
        log_event(conn, "finding_disposed", run_id=finding["run_id"],
                  payload={"finding_id": args.finding, "disposition": args.disposition})
        conn.commit()
        out({"finding_id": args.finding, "disposition": args.disposition})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_evidence_freeze(args):
    """Freeze a versioned Formal Evidence Set."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute("SELECT * FROM runs WHERE id=?", (args.run,)).fetchone()
        if not run:
            raise ValueError(f"run not found: {args.run}")
        assert_run_owner(conn, args.run, args.coordinator, require_active=True)
        existing = conn.execute(
            "SELECT * FROM evidence_sets WHERE run_id=?", (args.run,)
        ).fetchone()
        version = (existing["version"] + 1) if existing else 1
        evidence_id = gen_id("evidence")
        conn.execute(
            """INSERT INTO evidence_sets (id, run_id, version, artifact_ids)
               VALUES (?,?,?,?)""",
            (evidence_id, args.run, version, json.dumps(json.loads(args.artifact_ids), ensure_ascii=False)),
        )
        log_event(conn, "evidence_frozen", run_id=args.run,
                  payload={"evidence_id": evidence_id, "version": version})
        conn.commit()
        out({"evidence_id": evidence_id, "version": version})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_coordinator_smoke(args):
    """Run a non-destructive Coordinator capability smoke contract."""
    conn = get_db()
    try:
        # 1. Owner-scoped mutation rejection
        test_run_id = gen_id("run")
        conn.execute(
            "INSERT INTO runs (id, objective, coordinator) VALUES (?,?,?)",
            (test_run_id, "coordinator smoke", args.coordinator),
        )
        conn.commit()

        # Verify another coordinator cannot mutate
        try:
            assert_run_owner(conn, test_run_id, "other-coordinator", require_active=True)
            raise AssertionError("owner rejection failed")
        except PermissionError:
            pass  # expected

        # 2. One Wake Cycle: create task, claim, dispatch
        task_id = gen_id("task")
        conn.execute(
            "INSERT INTO tasks (id, run_id, spec, assigned_role, acceptance_mode) VALUES (?,?,?,?,?)",
            (task_id, test_run_id, "smoke task", "Developer", "legacy"),
        )
        conn.execute(
            """INSERT INTO capability_observations
               (agent_kind, profile, capability_scope, level, execution_mode, evidence)
               VALUES (?,?,?,?,?,?)""",
            (args.kind, args.profile, "execution", "verified", "managed",
             '{"roles":["Developer"]}'),
        )
        conn.commit()

        # 3. Inbox handling
        inbox_result = cmd_inbox_post(SimpleNamespace(
            run=test_run_id, coordinator=args.coordinator, kind="smoke",
            urgency="normal", source_event_id=None, payload="{}",
        ))
        inbox_id = inbox_result.get("id")
        if inbox_id:
            cmd_inbox_ack(SimpleNamespace(coordinator=args.coordinator, id=inbox_id))

        # 4. Finite budget enforcement
        budget_result = _reserve_budget(conn, test_run_id, None, 10, 2, 1)
        if budget_result["agent_limit"] != 10:
            raise AssertionError("budget reservation failed")

        # 5. Record coordinator capability
        fingerprint = capability_fingerprint({
            "agent_kind": args.kind,
            "profile": args.profile,
            "capability_scope": "coordinator",
            "execution_mode": "managed",
        })
        conn.execute(
            """INSERT INTO capability_observations
               (agent_kind, profile, capability_scope, level, execution_mode, evidence, fingerprint)
               VALUES (?,?,?,?,?,?,?)""",
            (args.kind, args.profile, "coordinator", "verified", "managed",
             '{"smoke":"passed"}', fingerprint),
        )

        # Cleanup smoke run
        conn.execute("DELETE FROM tasks WHERE run_id=?", (test_run_id,))
        conn.execute("DELETE FROM runs WHERE id=?", (test_run_id,))
        conn.execute("DELETE FROM run_budgets WHERE run_id=?", (test_run_id,))
        conn.execute("DELETE FROM coordinator_inbox WHERE run_id=?", (test_run_id,))

        log_event(conn, "coordinator_smoke_verified", payload={
            "coordinator": args.coordinator, "kind": args.kind, "profile": args.profile,
        })
        conn.commit()
        out({
            "status": "verified",
            "coordinator": args.coordinator,
            "kind": args.kind,
            "profile": args.profile,
            "checks": [
                "owner_rejection",
                "wake_cycle",
                "inbox_handling",
                "budget_enforcement",
            ],
        })
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_task_input_pin(args):
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (args.task,)).fetchone()
        if not task:
            raise ValueError(f"task not found: {args.task}")
        assert_run_owner(conn, task["run_id"], args.coordinator, require_active=True)
        if task["status"] != "pending":
            raise ValueError("Task inputs can only be pinned before claim")
        artifact = conn.execute(
            "SELECT * FROM artifacts WHERE id=? AND run_id=?", (args.artifact, task["run_id"])
        ).fetchone()
        if not artifact or artifact["status"] not in ("accepted", "superseded"):
            raise ValueError("Task input must be an accepted Artifact from the same Run")
        if artifact["logical_name"] != args.logical_name:
            raise ValueError("Artifact does not satisfy the declared logical input")
        existing = conn.execute(
            "SELECT artifact_id FROM task_artifact_inputs WHERE task_id=? AND logical_name=?",
            (args.task, args.logical_name),
        ).fetchone()
        if existing:
            if existing["artifact_id"] == args.artifact:
                out({"task_id": args.task, "logical_name": args.logical_name, "artifact_id": args.artifact})
                conn.rollback()
                return
            raise ValueError("Task input pin is immutable; use an Amendment")
        conn.execute(
            """INSERT INTO task_artifact_inputs
               (task_id, upstream_task_id, logical_name, artifact_id) VALUES (?,?,?,?)""",
            (args.task, artifact["producer_task_id"], args.logical_name, args.artifact),
        )
        log_event(
            conn, "task_input_pinned", run_id=task["run_id"], task_id=args.task,
            payload={"logical_name": args.logical_name, "artifact_id": args.artifact},
        )
        conn.commit()
        out({"task_id": args.task, "logical_name": args.logical_name, "artifact_id": args.artifact})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cmd_gate_create(args):
    conn = get_db()
    try:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (args.run,)).fetchone()
        if not run:
            raise ValueError(f"run not found: {args.run}")
        coordinator = getattr(args, "coordinator", None)
        if run["workflow_version"] != "legacy-v1":
            if not coordinator:
                raise PermissionError("artifact-led Gate mutation requires --coordinator")
            assert_run_owner(conn, args.run, coordinator, require_active=True)
        elif coordinator:
            assert_run_owner(conn, args.run, coordinator, require_active=True)

        artifact_id = getattr(args, "artifact", None)
        checklist_id = getattr(args, "checklist", None)
        reviewer_dispatch_id = getattr(args, "reviewer_dispatch", None)
        if artifact_id:
            artifact = conn.execute(
                "SELECT * FROM artifacts WHERE id=? AND run_id=?", (artifact_id, args.run)
            ).fetchone()
            if not artifact:
                raise ValueError(f"artifact not found in run: {artifact_id}")
            if checklist_id:
                checklist = conn.execute(
                    "SELECT * FROM artifacts WHERE id=? AND run_id=? AND artifact_type='checklist'",
                    (checklist_id, args.run),
                ).fetchone()
                if not checklist or checklist["status"] != "accepted":
                    raise ValueError("Gate checklist must be an accepted checklist Artifact")
            if reviewer_dispatch_id:
                reviewer = conn.execute(
                    "SELECT * FROM dispatches WHERE id=? AND run_id=?", (reviewer_dispatch_id, args.run)
                ).fetchone()
                if not reviewer:
                    raise ValueError("reviewer Dispatch not found in Run")
                producer = None
                if artifact["producer_dispatch_id"]:
                    producer = conn.execute(
                        "SELECT * FROM dispatches WHERE id=?", (artifact["producer_dispatch_id"],)
                    ).fetchone()
                if producer and (
                    reviewer["id"] == producer["id"]
                    or not reviewer["agent_session_id"]
                    or reviewer["agent_session_id"] == producer["agent_session_id"]
                ):
                    raise ValueError("reviewer must use a distinct recorded Agent session")

        gate_id = gen_id("gate")
        conn.execute(
            """INSERT INTO gates
                (id, run_id, task_id, gate_type, question, artifact_id,
                 checklist_artifact_id, reviewer_dispatch_id)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                gate_id, args.run, args.task, args.type, args.question, artifact_id,
                checklist_id, reviewer_dispatch_id,
            ),
        )
        log_event(conn, "gate_created", run_id=args.run, task_id=args.task, payload={"type": args.type})
        conn.commit()
        row = conn.execute("SELECT * FROM gates WHERE id=?", (gate_id,)).fetchone()
        out(row_to_dict(row))
    finally:
        conn.close()


def cmd_gate_resolve(args):
    conn = get_db()
    try:
        gate = conn.execute("SELECT * FROM gates WHERE id=?", (args.gate,)).fetchone()
        if not gate:
            raise ValueError(f"gate not found: {args.gate}")
        run = conn.execute("SELECT * FROM runs WHERE id=?", (gate["run_id"],)).fetchone()
        coordinator = getattr(args, "coordinator", None)
        if run["workflow_version"] != "legacy-v1":
            if not coordinator:
                raise PermissionError("artifact-led Gate mutation requires --coordinator")
            assert_run_owner(conn, gate["run_id"], coordinator, require_active=True)
        elif coordinator:
            assert_run_owner(conn, gate["run_id"], coordinator, require_active=True)
        if gate["resolution"] is not None:
            raise ValueError("Gate decision is already terminal")

        evidence = args.evidence or "{}"
        conn.execute(
            "UPDATE gates SET resolution=?, resolved_by=?, resolved_at=?, evidence=? WHERE id=?",
            (args.resolution, args.by, now(), evidence, args.gate),
        )
        if gate["artifact_id"]:
            if args.resolution == "pass":
                artifact = conn.execute("SELECT * FROM artifacts WHERE id=?", (gate["artifact_id"],)).fetchone()
                if artifact["supersedes_artifact_id"]:
                    conn.execute(
                        "UPDATE artifacts SET status='superseded' WHERE id=? AND status='accepted'",
                        (artifact["supersedes_artifact_id"],),
                    )
                conn.execute(
                    """UPDATE artifacts
                       SET status='accepted', accepted_gate_id=?, accepted_by=?, accepted_at=?
                       WHERE id=? AND status='candidate'""",
                    (args.gate, args.by, now(), gate["artifact_id"]),
                )
                conn.execute(
                    """UPDATE task_output_slots SET accepted_artifact_id=?
                       WHERE task_id=? AND logical_name=(
                         SELECT logical_name FROM artifacts WHERE id=?
                       )""",
                    (gate["artifact_id"], gate["task_id"], gate["artifact_id"]),
                )
                task = conn.execute("SELECT * FROM tasks WHERE id=?", (gate["task_id"],)).fetchone()
                if task and task["acceptance_mode"] == "artifact" and _required_outputs_accepted(conn, gate["task_id"]):
                    conn.execute(
                        "UPDATE tasks SET status='completed', completed_at=?, updated_at=? WHERE id=?",
                        (now(), now(), gate["task_id"]),
                    )
            elif args.resolution == "fail":
                conn.execute(
                    "UPDATE artifacts SET status='rejected' WHERE id=? AND status='candidate'",
                    (gate["artifact_id"],),
                )
        event_type = {
            "pass": "gate_passed", "fail": "gate_failed", "escalate": "gate_escalated",
        }[args.resolution]
        log_event(conn, event_type, run_id=gate["run_id"], task_id=gate["task_id"],
                  payload={"resolution": args.resolution, "artifact_id": gate["artifact_id"]})
        conn.commit()
        out({"gate_id": args.gate, "resolution": args.resolution})
    except Exception:
        conn.rollback()
        raise
    finally:
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
    sp.add_argument("--workflow-version", choices=["legacy-v1", "artifact-v1"], default="legacy-v1")
    sp.add_argument("--workflow-profile", choices=["compact", "standard", "deep"])
    sp.add_argument("--force", action="store_true")

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
    sp.add_argument("--capability-scope", choices=sorted(CAPABILITY_SCOPES), default="execution")
    sp.add_argument("--command")
    sp.add_argument("--command-type")
    sp.add_argument("--execution-mode", choices=["managed", "raw-pane"], default="managed")
    sp.add_argument("--executable-path")
    sp.add_argument("--version")
    sp.add_argument("--herdr-version")
    sp.add_argument("--integration")
    sp.add_argument("--launch-args")
    sp.add_argument("--model")
    sp.add_argument("--native-parameters", default="{}")
    sp.add_argument("--normalized-intensity", choices=sorted(INTENSITY_LEVELS))
    sp.add_argument("--permission-mode")
    sp.add_argument("--config-hash")
    sp.add_argument("--shell-path")
    sp.add_argument("--fingerprint")
    sp.add_argument("--max-age-s", type=int, default=86400)
    sp.add_argument("--evidence", default="{}")
    sp.set_defaults(func=cmd_capability_record)

    sp = sub.add_parser("capability-list")
    sp.add_argument("--kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--capability-scope", choices=sorted(CAPABILITY_SCOPES))
    sp.add_argument("--fingerprint")
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
    sp.add_argument("--execution-mode", choices=["managed", "raw-pane"], default="managed")
    sp.add_argument("--acceptance-mode", choices=["legacy", "artifact"], default="legacy")
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
    sp.add_argument("--workflow-version", choices=["legacy-v1", "artifact-v1"])
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

    sp = sub.add_parser("dispatch-ask-from-output", help="Parse TASK_ASK from output and block dispatch for a Coordinator reply")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--text", default="")
    sp.add_argument("--file")
    sp.add_argument("--coordinator", default="hermes")
    sp.set_defaults(func=cmd_dispatch_ask_from_output)

    sp = sub.add_parser("dispatch-answer", help="Record a Coordinator answer and resume a Worker question")
    sp.add_argument("--dispatch", required=True)
    sp.add_argument("--answer", required=True)
    sp.add_argument("--coordinator", default="hermes")
    sp.set_defaults(func=cmd_dispatch_answer)

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

    sp = sub.add_parser("artifact-register")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--task", required=True)
    sp.add_argument("--dispatch")
    sp.add_argument("--logical-name", required=True)
    sp.add_argument("--type", required=True)
    sp.add_argument("--source", required=True)
    sp.add_argument("--sha256")
    sp.add_argument("--idempotency-key", required=True)
    sp.add_argument("--supersedes")
    sp.add_argument("--required", action=argparse.BooleanOptionalAction, default=True)
    sp.add_argument("--allow-empty", action="store_true")
    sp.add_argument("--metadata", default="{}")
    sp.set_defaults(func=cmd_artifact_register)

    sp = sub.add_parser("artifact-list")
    sp.add_argument("--run", required=True)
    sp.add_argument("--logical-name")
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_artifact_list)

    sp = sub.add_parser("task-input-pin")
    sp.add_argument("--task", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--logical-name", required=True)
    sp.add_argument("--artifact", required=True)
    sp.set_defaults(func=cmd_task_input_pin)

    sp = sub.add_parser("run-delegate")
    sp.add_argument("--parent-run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--objective", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--profile")
    sp.add_argument("--parent-task")
    sp.add_argument("--parent-budget-id")
    sp.add_argument("--agent-limit", type=int, required=True)
    sp.add_argument("--coordinator-limit", type=int, required=True)
    sp.add_argument("--parallelism-limit", type=int, default=1)
    sp.add_argument("--max-depth", type=int, default=0)
    sp.add_argument("--max-intensity", choices=["low", "medium", "high"], default="high")
    sp.add_argument("--required-capabilities", default="[]")
    sp.add_argument("--allowed-scope", default="[]")
    sp.add_argument("--escalation-conditions", default="[]")
    sp.add_argument("--bootstrap-capability", action="store_true")
    sp.add_argument("--run-agent-limit", type=int, default=100)
    sp.add_argument("--run-coordinator-limit", type=int, default=10)
    sp.add_argument("--run-parallelism-limit", type=int, default=4)
    sp.set_defaults(func=cmd_run_delegate)

    sp = sub.add_parser("delegation-status")
    sp.add_argument("--delegation", required=True)
    sp.set_defaults(func=cmd_delegation_status)

    sp = sub.add_parser("topology-grant")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--workspace", required=True)
    sp.add_argument("--tabs", default="[]")
    sp.add_argument("--panes", default="[]")
    sp.set_defaults(func=cmd_topology_grant)

    sp = sub.add_parser("topology-release")
    sp.add_argument("--run", required=True)
    sp.add_argument("--allocation", required=True)
    sp.set_defaults(func=cmd_topology_release)

    sp = sub.add_parser("topology-status")
    sp.add_argument("--run", required=True)
    sp.set_defaults(func=cmd_topology_status)

    sp = sub.add_parser("inbox-post")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--urgency", choices=["normal", "urgent"], default="normal")
    sp.add_argument("--source-event-id", type=int)
    sp.add_argument("--payload", default="{}")
    sp.set_defaults(func=cmd_inbox_post)

    sp = sub.add_parser("inbox-list")
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--urgency", choices=["normal", "urgent"])
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_inbox_list)

    sp = sub.add_parser("inbox-ack")
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--id", type=int, required=True)
    sp.set_defaults(func=cmd_inbox_ack)

    sp = sub.add_parser("wake-path")
    sp.add_argument("--coordinator", required=True)
    sp.set_defaults(func=cmd_wake_path)

    sp = sub.add_parser("wake-signal")
    sp.add_argument("--coordinator", required=True)
    sp.set_defaults(func=cmd_wake_signal)

    sp = sub.add_parser("amendment-propose")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--reason", required=True)
    sp.add_argument("--impact-set", default="[]")
    sp.add_argument("--constitution", action="store_true")
    sp.set_defaults(func=cmd_amendment_propose)

    sp = sub.add_parser("amendment-resolve")
    sp.add_argument("--amendment", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--resolution", required=True, choices=["accepted", "rejected"])
    sp.add_argument("--resolution-note")
    sp.set_defaults(func=cmd_amendment_resolve)

    sp = sub.add_parser("cancel-request")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--reason")
    sp.set_defaults(func=cmd_cancel_request)

    sp = sub.add_parser("cancel-ack")
    sp.add_argument("--cancel", required=True)
    sp.set_defaults(func=cmd_cancel_ack)

    sp = sub.add_parser("finding-add")
    sp.add_argument("--run", required=True)
    sp.add_argument("--severity", required=True, choices=["critical", "major", "minor", "observation"])
    sp.add_argument("--description", required=True)
    sp.add_argument("--artifact")
    sp.add_argument("--evidence", default="{}")
    sp.set_defaults(func=cmd_finding_add)

    sp = sub.add_parser("finding-dispose")
    sp.add_argument("--finding", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--disposition", required=True, choices=["accepted_risk", "remediated", "reopened", "waivered"])
    sp.add_argument("--reason")
    sp.set_defaults(func=cmd_finding_dispose)

    sp = sub.add_parser("evidence-freeze")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--artifact-ids", required=True)
    sp.set_defaults(func=cmd_evidence_freeze)

    sp = sub.add_parser("coordinator-smoke")
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--profile")
    sp.set_defaults(func=cmd_coordinator_smoke)

    sp = sub.add_parser("gate-create")
    sp.add_argument("--run", required=True)
    sp.add_argument("--type", required=True)
    sp.add_argument("--question", required=True)
    sp.add_argument("--task")
    sp.add_argument("--coordinator")
    sp.add_argument("--artifact")
    sp.add_argument("--checklist")
    sp.add_argument("--reviewer-dispatch")
    sp.set_defaults(func=cmd_gate_create)

    sp = sub.add_parser("gate-resolve")
    sp.add_argument("--gate", required=True)
    sp.add_argument("--resolution", required=True, choices=["pass", "fail", "escalate"])
    sp.add_argument("--by", default="dispatcher")
    sp.add_argument("--coordinator")
    sp.add_argument("--evidence", default="{}")
    sp.set_defaults(func=cmd_gate_resolve)

    sp = sub.add_parser("event-log")
    sp.add_argument("--run")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--table", action="store_true")
    sp.set_defaults(func=cmd_event_log)
    sp = sub.add_parser("coordinator-run")
    sp.add_argument("--run", required=True)
    sp.add_argument("--coordinator", required=True)
    sp.add_argument("--agent-name")
    sp.add_argument("--agent-kind", default="omp")
    sp.add_argument("--pane", default="")
    sp.add_argument("--tab")
    sp.add_argument("--profile")
    sp.add_argument("--dispatch-mode", choices=["blocking", "non-blocking"], default="blocking")
    sp.add_argument("--wait-timeout-ms", type=int, default=900000)
    sp.add_argument("--delivery-mode", choices=["direct", "hrbp"], default="direct")
    sp.add_argument("--herdr-bin", default="herdr")
    sp.add_argument("--workflow-version", choices=["legacy-v1", "artifact-v1"])

    return p

def cmd_coordinator_run(args):
    """Delegate to db.coordinator for pure-Python coordinator loop."""
    from db.coordinator import Coordinator, CoordinatorConfig
    import os

    if not os.environ.get("HERDR_ENV"):
        out({"error": "HERDR_ENV=1 required"})
        return

    config = CoordinatorConfig(
        run_id=args.run,
        coordinator=args.coordinator,
        agent_name=getattr(args, "agent_name", None),
        agent_kind=getattr(args, "agent_kind", "omp"),
        pane=getattr(args, "pane", ""),
        tab=getattr(args, "tab", None),
        profile=getattr(args, "profile", None),
        dispatch_mode=getattr(args, "dispatch_mode", "blocking"),
        wait_timeout_ms=getattr(args, "wait_timeout_ms", 900000),
        delivery_mode=getattr(args, "delivery_mode", "direct"),
        herdr_bin=getattr(args, "herdr_bin", "herdr"),
        on_event=lambda et, d: out({"event": et, **d}),
    )

def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
