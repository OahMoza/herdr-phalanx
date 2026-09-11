"""Herdr Adapter and AgentBus Core/CLI wiring tests.

These tests inject a FakeCommandRunner into the Core to verify the
"build a restricted lease prompt and deliver it to a pane" path without
spawning `herdr agent prompt` or touching the TUI.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "db"))

import agent_bus_core as core  # noqa: E402
import herdr_adapter  # noqa: E402


class _FakeCommandRunner:
    """Records every invocation; returns ok=True and a synthetic result
    dict. Tests assert on `calls`."""

    def __init__(self) -> None:
        self.calls: List[dict] = []

    def run_agent_prompt(self, *, agent_name, prompt, timeout):
        self.calls.append({
            "agent_name": agent_name,
            "prompt": prompt,
            "timeout": timeout,
        })
        return herdr_adapter.CommandResult(ok=True, output={"accepted": True})


class _BusSetup(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="herdr-adapter-test-"))
        self.db_path = self.tmp / "bus.db"
        self.run_root = self.tmp / "runs"
        os.environ["AGENT_BUS_DB"] = str(self.db_path)
        os.environ["AGENT_BUS_ARTIFACTS"] = str(self.run_root)
        self.bus = core.AgentBus(self.db_path, self.run_root)
        schema_path = (Path(__file__).resolve().parents[1]
                       / "agent_bus_schema.sql")
        self.bus.init_database(schema_path.read_text(encoding="utf-8"))
        self.bus.set_route(agent_kind="omp", max_in_flight=2,
                            default_lease_seconds=300)
        self.runner = _FakeCommandRunner()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBuildLeasePrompt(unittest.TestCase):
    """The prompt must list only the four allowed commands and forbid
    `enqueue`/`claim`."""

    def test_prompt_lists_only_allowed_commands(self):
        msg = {
            "id": "msg_abc",
            "lease_id": "lease_xyz",
            "attempts": 1,
            "payload": {"instruction": "say hi", "workspace": "/tmp"},
        }
        prompt = herdr_adapter.build_lease_prompt(
            msg, agent_kind="omp", profile=None,
            python_executable="python",
            db_module="db/agent_bus.py")
        self.assertIn("msg_abc", prompt)
        self.assertIn("lease_xyz", prompt)
        # Allowed commands listed (CLI subcommand names):
        self.assertIn("heartbeat", prompt)
        self.assertIn("complete", prompt)
        self.assertIn("fail", prompt)
        self.assertIn("cancelled", prompt)
        # Allowed flag names match the real CLI:
        self.assertIn("--id", prompt)
        self.assertIn("--worker-id", prompt)
        self.assertIn("--lease", prompt)
        self.assertIn("--result-file", prompt)
        self.assertIn("--raw-report-file", prompt)
        # Forbidden commands called out:
        self.assertIn("enqueue", prompt)
        self.assertIn("claim", prompt)
        # Header marks the prompt as restricted.
        self.assertIn("RESTRICTED", prompt)

    def test_prompt_handles_profile_none(self):
        msg = {"id": "msg_x", "lease_id": "lease_x", "attempts": 1, "payload": {}}
        prompt = herdr_adapter.build_lease_prompt(
            msg, agent_kind="omp", profile=None,
            python_executable="python",
            db_module="db/agent_bus.py")
        self.assertIn("profile: ``", prompt)


class TestWorkerLoopOnce(_BusSetup):
    def test_claim_and_deliver_calls_runner_and_heartbeats(self):
        msg = self.bus.enqueue(
            caller_id="c1", agent_kind="omp",
            payload={"instruction": "go"},
        )
        result = self.bus.worker_loop_once(
            worker_id="omp-w1",
            agent_kind="omp",
            profile=None,
            agent_name="omp-dev",
            commander_runner=self.runner,
            prompt_builder=lambda m: f"PROMPT-FOR-{m['id']}",
            lease_seconds=120,
        )
        self.assertTrue(result["claimed"])
        self.assertTrue(result["delivered"])
        self.assertEqual(result["message_id"], msg["id"])
        self.assertEqual(len(self.runner.calls), 1)
        call = self.runner.calls[0]
        self.assertEqual(call["agent_name"], "omp-dev")
        self.assertIn(msg["id"], call["prompt"])
        self.assertEqual(call["timeout"], 120)
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT status, lease_until FROM bus_messages WHERE id=?",
                (msg["id"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["status"], "leased")
        self.assertIsNotNone(row["lease_until"])

    def test_no_pending_message_returns_clean_no_claim(self):
        result = self.bus.worker_loop_once(
            worker_id="omp-w1", agent_kind="omp", profile=None,
            agent_name="omp-dev",
            commander_runner=self.runner,
            prompt_builder=lambda m: "should not run",
        )
        self.assertFalse(result["claimed"])
        self.assertEqual(self.runner.calls, [])

    def test_runner_failure_does_not_finalise_message(self):
        self.runner.run_agent_prompt = lambda **kw: herdr_adapter.CommandResult(
            ok=False, output={}, error="herdr binary missing",
        )
        msg = self.bus.enqueue(caller_id="c1", agent_kind="omp",
                                payload={"instruction": "go"})
        result = self.bus.worker_loop_once(
            worker_id="omp-w1", agent_kind="omp", profile=None,
            agent_name="omp-dev",
            commander_runner=self.runner,
            prompt_builder=lambda m: "x",
        )
        self.assertTrue(result["claimed"])
        self.assertFalse(result["delivered"])
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT status FROM bus_messages WHERE id=?",
                (msg["id"],),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["status"], "leased")


if __name__ == "__main__":
    unittest.main()
