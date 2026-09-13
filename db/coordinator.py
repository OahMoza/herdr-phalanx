#!/usr/bin/env python3
"""
Herdr Phalanx Coordinator Core.

Pure Python replacement for templates/coordinator_loop.ps1.
Drives one active Run: claims ready tasks, dispatches to managed Workers,
waits for settlement, parses output, persists through Phalanx DB CLI.

Three modes:
  - direct: PM specifies agent-name per task (PS1 parity)
  - hrbp: coordinator selects worker from current_capabilities
  - artifact: Constitution-driven wake loop for artifact-v1 workflow

No LLM calls. All Herdr interaction via subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Herdr subprocess wrapper
# ---------------------------------------------------------------------------

class HerdrClient:
    """Thin wrapper around herdr CLI. All calls are subprocess, no LLM."""

    def __init__(self, herdr_bin: str = "herdr"):
        self.herdr_bin = herdr_bin

    def _run(self, *args: str, timeout: int = 30) -> dict | str:
        cmd = [self.herdr_bin, *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(f"herdr {' '.join(args)} failed: {result.stderr.strip()}")
        out = result.stdout.strip()
        if not out:
            return {}
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    def agent_start(self, name: str, kind: str, pane: str,
                    extra_args: list[str] | None = None, timeout_ms: int = 30000) -> dict:
        args = ["agent", "start", name, "--kind", kind, "--pane", pane,
                "--timeout", str(timeout_ms)]
        if extra_args:
            args.extend(["--", *extra_args])
        return self._run(*args, timeout=timeout_ms // 1000 + 5)

    def agent_prompt(self, name: str, text: str, timeout_ms: int = 300000) -> str:
        return self._run("agent", "prompt", name, text,
                         "--wait", "--timeout", str(timeout_ms),
                         timeout=timeout_ms // 1000 + 5)

    def agent_wait(self, name: str, until: list[str], timeout_ms: int = 300000) -> tuple[str | None, bool]:
        result = subprocess.run(
            [self.herdr_bin, "agent", "wait", name,
             "--until", ",".join(until), "--timeout", str(timeout_ms)],
            capture_output=True, text=True, timeout=timeout_ms // 1000 + 5,
        )
        return result.stdout.strip(), result.returncode != 0

    def agent_read(self, name: str) -> str:
        return self._run("agent", "read", name)

    def agent_get(self, name: str) -> dict:
        return self._run("agent", "get", name)

    def agent_list(self) -> list[dict]:
        result = self._run("agent", "list")
        if isinstance(result, dict) and "result" in result:
            agents = result["result"].get("agents", [])
            return agents if isinstance(agents, list) else []
        return []


# ---------------------------------------------------------------------------
# Phalanx DB client (in-process, no subprocess)
# ---------------------------------------------------------------------------

class PhalanxDB:
    """Direct Python API over phalanx_db module. Avoids subprocess overhead."""

    def __init__(self, db_path: Path | None = None):
        from db import phalanx_db as db
        self._db = db
        if db_path:
            os.environ["PHALANX_DB"] = str(db_path)

    def _capture_dict(self, action, args) -> dict:
        result = {}
        old_out = self._db.out
        self._db.out = lambda data, table=False: result.update(data) if isinstance(data, dict) else None
        try:
            action(args)
        finally:
            self._db.out = old_out
        return result

    def _capture_list(self, action, args) -> list:
        result = []
        old_out = self._db.out
        self._db.out = lambda data, table=False: result.extend(data) if isinstance(data, list) else None
        try:
            action(args)
        finally:
            self._db.out = old_out
        return result

    def run_status(self, run_id: str) -> dict:
        return self._capture_dict(self._db.cmd_run_status, SimpleNamespace(run=run_id))

    def task_ready(self, run_id: str) -> list[dict]:
        return self._capture_list(self._db.cmd_task_ready, SimpleNamespace(run=run_id, table=False))

    def task_claim(self, task_id: str, coordinator: str, kind: str,
                   agent_name: str, pane: str, tab: str | None = None,
                   profile: str | None = None) -> dict:
        return self._capture_dict(self._db.cmd_task_claim, SimpleNamespace(
            task=task_id, coordinator=coordinator, kind=kind,
            agent_name=agent_name, pane=pane, tab=tab, profile=profile,
        ))

    def dispatch_complete_from_output(self, dispatch_id: str, coordinator: str, text: str) -> dict:
        return self._capture_dict(self._db.cmd_dispatch_complete_from_output,
            SimpleNamespace(dispatch=dispatch_id, coordinator=coordinator, text=text))

    def dispatch_ask_from_output(self, dispatch_id: str, coordinator: str, text: str) -> dict:
        return self._capture_dict(self._db.cmd_dispatch_ask_from_output,
            SimpleNamespace(dispatch=dispatch_id, coordinator=coordinator, text=text))

    def dispatch_block(self, dispatch_id: str, coordinator: str,
                       state: str, reason: str, evidence: dict) -> dict:
        return self._capture_dict(self._db.cmd_dispatch_block,
            SimpleNamespace(
                dispatch=dispatch_id, coordinator=coordinator,
                state=state, reason=reason, evidence=json.dumps(evidence),
            ))

    def capability_list(self) -> list[dict]:
        return self._capture_list(self._db.cmd_capability_list, SimpleNamespace(table=False))

    def current_capabilities(self) -> list[dict]:
        conn = self._db.get_db()
        rows = conn.execute("SELECT * FROM current_capabilities").fetchall()
        return [self._db.row_to_dict(r) for r in rows]

    def dispatch_list(self, run_id: str, status: str | None = None) -> list[dict]:
        conn = self._db.get_db()
        q = "SELECT * FROM dispatches WHERE run_id=?"
        params: list[Any] = [run_id]
        if status:
            q += " AND status=?"
            params.append(status)
        rows = conn.execute(q, params).fetchall()
        return [self._db.row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Constitution parser for HRBP Semi-Auto
# ---------------------------------------------------------------------------

@dataclass
class ConstitutionTask:
    id: str
    spec: str
    role: str
    preferred_agent: str | None
    execution_mode: str
    deps: list[str]
    checklist_id: str | None
    output_slots: list[dict]


@dataclass
class Constitution:
    """Parsed Constitution Artifact for artifact-v1 workflow."""
    run_id: str
    objective: str
    workflow_profile: str
    tasks: list[ConstitutionTask]
    budget: dict
    gates: list[dict]
    amendment_rules: dict

    @classmethod
    def from_artifact(cls, artifact_path: str, run_id: str) -> "Constitution":
        path = Path(artifact_path)
        if not path.exists():
            raise FileNotFoundError(f"Constitution Artifact not found: {artifact_path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        tasks = [ConstitutionTask(
            id=t["id"], spec=t.get("spec", ""),
            role=t.get("role", "Developer"),
            preferred_agent=t.get("preferred_agent"),
            execution_mode=t.get("execution_mode", "managed"),
            deps=t.get("deps", []), checklist_id=t.get("checklist_id"),
            output_slots=t.get("output_slots", []),
        ) for t in data.get("tasks", [])]
        return cls(
            run_id=run_id, objective=data.get("objective", ""),
            workflow_profile=data.get("workflow_profile", "standard"),
            tasks=tasks, budget=data.get("budget", {}),
            gates=data.get("gates", []), amendment_rules=data.get("amendment_rules", {}),
        )

    @classmethod
    def from_db(cls, db: PhalanxDB, run_id: str) -> "Constitution":
        conn = db._db.get_db()
        row = conn.execute(
            "SELECT * FROM artifacts WHERE run_id=? AND artifact_type='constitution' AND status='accepted' ORDER BY version DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"no accepted Constitution Artifact for run {run_id}")
        artifact_root = Path(os.environ.get(
            "PHALANX_ARTIFACTS", str(Path.home() / ".herdr-phalanx" / "artifacts")))
        return cls.from_artifact(str(artifact_root / row["storage_key"]), run_id)

    def get_task(self, task_id: str) -> ConstitutionTask | None:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def ready_tasks(self, completed_ids: set[str]) -> list[ConstitutionTask]:
        return [t for t in self.tasks if t.id not in completed_ids and all(d in completed_ids for d in t.deps)]


# ---------------------------------------------------------------------------
# Worker selection (HRBP)
# ---------------------------------------------------------------------------

@dataclass
class WorkerMatch:
    agent_name: str
    agent_kind: str
    profile: str | None
    pane: str
    tab: str | None
    score: float


class WorkerSelector:
    """Select best worker from current_capabilities for a task."""

    def __init__(self, db: PhalanxDB, herdr: HerdrClient):
        self._db = db
        self._herdr = herdr

    def find_worker(self, run_id: str, assigned_role: str,
                    preferred_agent: str | None = None,
                    execution_mode: str = "managed") -> WorkerMatch | None:
        capabilities = self._db.current_capabilities()
        dispatches = self._db.dispatch_list(run_id, status="running")
        occupied_agents = {d["agent_name"] for d in dispatches}
        occupied_panes = {d["pane_id"] for d in dispatches if d.get("pane_id")}
        candidates: list[WorkerMatch] = []

        for cap in capabilities:
            if cap.get("level") != "verified" or cap.get("capability_scope") != "execution":
                continue
            if cap.get("execution_mode") != execution_mode:
                continue
            evidence = cap.get("evidence", {}) if isinstance(cap.get("evidence"), dict) else {}
            roles = evidence.get("roles", [])
            if assigned_role not in roles:
                continue
            agent_name = evidence.get("agent_name")
            pane = evidence.get("pane_id")
            tab = evidence.get("tab_id")
            kind = cap.get("agent_kind", "")
            profile = cap.get("profile")
            if not agent_name or agent_name in occupied_agents:
                continue
            if pane and pane in occupied_panes:
                continue
            score = self._score_worker(cap, kind, profile, preferred_agent)
            candidates.append(WorkerMatch(agent_name, kind, profile, pane, tab, score))

        if not candidates:
            return None
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[0]

    def _score_worker(self, cap: dict, kind: str, profile: str | None,
                      preferred_agent: str | None) -> float:
        score = 0.0
        if preferred_agent and kind == preferred_agent:
            score += 10.0
        if cap.get("level") == "verified":
            score += 5.0
        observed = cap.get("observed_at", "")
        if observed:
            try:
                dt = datetime.fromisoformat(observed)
                age_hours = (datetime.now() - dt).total_seconds() / 3600
                score += max(0, 2.0 - age_hours * 0.1)
            except (ValueError, TypeError):
                pass
        return score


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

@dataclass
class CoordinatorConfig:
    run_id: str
    coordinator: str
    agent_name: str | None = None
    agent_kind: str = "omp"
    pane: str = ""
    tab: str | None = None
    profile: str | None = None
    dispatch_mode: str = "blocking"
    wait_timeout_ms: int = 900000
    delivery_mode: str = "direct"
    herdr_bin: str = "herdr"
    on_event: Callable[[str, dict], None] | None = None


class Coordinator:
    """
    Event-driven coordinator for one active Run.

    direct mode: uses fixed agent_name from config (PS1 parity).
    hrbp mode: selects worker per task from current_capabilities.
    artifact mode: Constitution-driven wake loop for artifact-v1.
    """

    def __init__(self, config: CoordinatorConfig):
        self.config = config
        self._herdr = HerdrClient(config.herdr_bin)
        self._db = PhalanxDB()
        self._selector = WorkerSelector(self._db, self._herdr)
        self._preamble = self._load_preamble()

    @staticmethod
    def _load_preamble() -> str:
        path = Path(__file__).parent.parent / "templates" / "worker_done_preamble.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "## Report Format\nEnd output with TASK_COMPLETE marker."

    def _emit(self, event_type: str, data: dict) -> None:
        cb = self.config.on_event
        if cb:
            cb(event_type, data)

    def run(self) -> None:
        """Main loop: advance Run until no ready tasks remain."""
        while True:
            run_status = self._db.run_status(self.config.run_id)
            status = run_status.get("status")
            if status != "active":
                self._emit("run_finished", {"status": status})
                break
            pending = run_status.get("pending_tasks", 0)
            running = run_status.get("running_tasks", 0)
            if pending == 0 and running == 0:
                self._emit("run_drained", {"status": status})
                break
            ready_tasks = self._db.task_ready(self.config.run_id)
            if not ready_tasks:
                time.sleep(0.5)
                continue
            for task in ready_tasks:
                self._dispatch_task(task)

    def _dispatch_task(self, task: dict) -> None:
        """Claim and dispatch one task."""
        task_id = task["id"]
        agent_name, agent_kind, pane, tab, profile = self._resolve_worker(task, "direct")
        if agent_name is None:
            return
        self._execute_dispatch(task_id, task["spec"], agent_name, agent_kind, pane, tab, profile)

    def _resolve_worker(self, task: dict, fallback_role: str) -> tuple:
        """Resolve worker based on delivery mode. Returns (name, kind, pane, tab, profile)."""
        if self.config.delivery_mode == "hrbp":
            match = self._selector.find_worker(
                run_id=task.get("run_id", self.config.run_id),
                assigned_role=task.get("assigned_role", fallback_role),
                preferred_agent=task.get("preferred_agent"),
                execution_mode=task.get("execution_mode", "managed"),
            )
            if not match:
                self._block_no_worker(task["id"], task)
                return (None, None, None, None, None)
            return (match.agent_name, match.agent_kind, match.pane, match.tab, match.profile)
        return (self.config.agent_name or "", self.config.agent_kind,
                self.config.pane, self.config.tab, self.config.profile)

    def _execute_dispatch(self, task_id: str, spec: str, agent_name: str,
                          agent_kind: str, pane: str, tab: str | None,
                          profile: str | None) -> None:
        """Claim and dispatch a task to a specific worker."""
        try:
            claim = self._db.task_claim(
                task_id=task_id, coordinator=self.config.coordinator,
                kind=agent_kind, agent_name=agent_name, pane=pane, tab=tab, profile=profile,
            )
        except Exception as e:
            self._block_dispatch_unknown(task_id, str(e), {})
            return

        dispatch_id = claim["dispatch"]["id"]
        self._emit("dispatch_started", {"dispatch_id": dispatch_id, "task_id": task_id})
        prompt = f"{self._preamble}\n\n## Dispatch ID\n{dispatch_id}\n\n## Task\n{spec}"

        try:
            self._herdr.agent_prompt(agent_name, prompt)
        except Exception as e:
            self._block_dispatch(dispatch_id, "blocked", "agent prompt failed",
                                {"agent": agent_name, "error": str(e)})
            return

        if self.config.dispatch_mode == "non-blocking":
            return
        self._wait_and_settle(agent_name, dispatch_id)

    def _wait_and_settle(self, agent_name: str, dispatch_id: str) -> None:
        """Wait for worker to settle and persist result."""
        output, timed_out = self._herdr.agent_wait(
            agent_name, until=["idle", "done", "blocked", "unknown"],
            timeout_ms=self.config.wait_timeout_ms,
        )
        if timed_out:
            read_output = self._herdr.agent_read(agent_name)
            self._block_dispatch(dispatch_id, "timeout", "timeout after output inspection",
                                {"agent": agent_name, "output": read_output})
            return

        read_output = self._herdr.agent_read(agent_name)
        try:
            agent_info = self._herdr.agent_get(agent_name)
            state = agent_info.get("result", {}).get("agent", {}).get("agent_status", "unknown")
        except Exception:
            state = "unknown"

        if state == "blocked":
            self._block_dispatch(dispatch_id, "blocked", "Herdr reported blocked",
                                {"agent": agent_name, "output": read_output})
        elif state == "unknown":
            self._block_dispatch(dispatch_id, "unknown", "Herdr reported unknown",
                                {"agent": agent_name, "output": read_output})
        else:
            self._settle_output(dispatch_id, read_output)

    def _settle_output(self, dispatch_id: str, output: str) -> None:
        """Try complete, then ask, then block."""
        try:
            self._db.dispatch_complete_from_output(dispatch_id, self.config.coordinator, output)
            self._emit("dispatch_completed", {"dispatch_id": dispatch_id})
            return
        except Exception:
            pass
        try:
            self._db.dispatch_ask_from_output(dispatch_id, self.config.coordinator, output)
            self._emit("dispatch_asked", {"dispatch_id": dispatch_id})
            return
        except Exception:
            pass
        self._block_dispatch(dispatch_id, "settled", "missing valid TASK_COMPLETE report",
                            {"output": output})

    def _block_dispatch(self, dispatch_id: str, state: str, reason: str, evidence: dict) -> None:
        self._db.dispatch_block(dispatch_id, self.config.coordinator, state, reason, evidence)
        self._emit("dispatch_blocked", {"dispatch_id": dispatch_id, "state": state, "reason": reason})

    def _block_no_worker(self, task_id: str, task: dict) -> None:
        self._emit("no_worker_available", {"task_id": task_id, "task": task})

    def _block_dispatch_unknown(self, task_id: str, reason: str, evidence: dict) -> None:
        self._emit("claim_failed", {"task_id": task_id, "reason": reason})

    # ---------------------------------------------------------------------------
    # Artifact mode (HRBP Semi-Auto)
    # ---------------------------------------------------------------------------

    def run_artifact_loop(self, constitution: Constitution) -> None:
        """Constitution-driven wake loop for artifact-v1 workflow."""
        completed: set[str] = set()
        running: dict[str, str] = {}

        while True:
            run_status = self._db.run_status(self.config.run_id)
            status = run_status.get("status")
            if status != "active":
                self._emit("run_finished", {"status": status})
                break

            pending = run_status.get("pending_tasks", 0)
            running_count = run_status.get("running_tasks", 0)
            if pending == 0 and running_count == 0:
                self._emit("run_drained", {"status": status})
                break

            ready = constitution.ready_tasks(completed)
            if not ready and not running:
                time.sleep(0.5)
                continue

            for ct in ready:
                if ct.id in running:
                    continue
                self._dispatch_constitution_task(ct)
                running[ct.id] = "pending"

            self._check_completed_dispatches(completed, running)
            self._handle_pending_gates()
            time.sleep(0.5)

    def _dispatch_constitution_task(self, ct: ConstitutionTask) -> None:
        """Dispatch a Constitution task."""
        agent_name, agent_kind, pane, tab, profile = self._resolve_worker(
            {"id": ct.id, "assigned_role": ct.role, "preferred_agent": ct.preferred_agent,
             "execution_mode": ct.execution_mode, "run_id": self.config.run_id},
            ct.role,
        )
        if agent_name is None:
            return
        self._execute_dispatch(ct.id, ct.spec, agent_name, agent_kind, pane, tab, profile)

    def _check_completed_dispatches(self, completed: set[str], running: dict[str, str]) -> None:
        """Check running dispatches and mark completed ones."""
        dispatches = self._db.dispatch_list(self.config.run_id, status="completed")
        for d in dispatches:
            task_id = d.get("task_id")
            if task_id and task_id not in completed:
                completed.add(task_id)
                running.pop(task_id, None)

    def _handle_pending_gates(self) -> None:
        """Process pending gates that need resolution."""
        conn = self._db._db.get_db()
        pending = conn.execute(
            "SELECT * FROM gates WHERE run_id=? AND resolution IS NULL",
            (self.config.run_id,),
        ).fetchall()
        for gate in pending:
            if gate["gate_type"] == "smoke_test":
                evidence = json.loads(gate["evidence"] or "{}")
                if evidence.get("auto_pass"):
                    conn.execute(
                        "UPDATE gates SET resolution='pass', resolved_by='coordinator', resolved_at=? WHERE id=?",
                        (datetime.now().isoformat(), gate["id"]),
                    )
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def cmd_coordinator_run(args: Any) -> None:
    """CLI entry: python -m db.coordinator run ..."""
    if not os.environ.get("HERDR_ENV"):
        print(json.dumps({"error": "HERDR_ENV=1 required"}), flush=True)
        sys.exit(1)

    config = CoordinatorConfig(
        run_id=args.run, coordinator=args.coordinator,
        agent_name=getattr(args, "agent_name", None),
        agent_kind=getattr(args, "agent_kind", "omp"),
        pane=getattr(args, "pane", ""),
        tab=getattr(args, "tab", None),
        profile=getattr(args, "profile", None),
        dispatch_mode=getattr(args, "dispatch_mode", "blocking"),
        wait_timeout_ms=getattr(args, "wait_timeout_ms", 900000),
        delivery_mode=getattr(args, "delivery_mode", "direct"),
        herdr_bin=getattr(args, "herdr_bin", "herdr"),
        on_event=lambda et, d: print(json.dumps({"event": et, **d}), flush=True),
    )

    coord = Coordinator(config)

    # Check for Constitution artifact
    workflow_version = getattr(args, "workflow_version", None)
    if workflow_version == "artifact-v1":
        try:
            constitution = Constitution.from_db(coord._db, args.run)
            coord.run_artifact_loop(constitution)
        except ValueError as e:
            print(json.dumps({"error": str(e)}), flush=True)
            sys.exit(1)
    else:
        coord.run()


def build_coordinator_parser():
    """Build argparse subparser for coordinator commands."""
    import argparse
    p = argparse.ArgumentParser(description="Herdr Phalanx Coordinator")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("run")
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
    sp.add_argument("--workflow-version", choices=["legacy-v1", "artifact-v1"])
    sp.add_argument("--herdr-bin", default="herdr")
    sp.set_defaults(func=cmd_coordinator_run)

    return p


def main():
    parser = build_coordinator_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
