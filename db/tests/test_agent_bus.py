"""Agent Bus unit tests.

Every test sets `AGENT_BUS_DB` and `AGENT_BUS_ARTIFACTS` to a fresh temporary
directory so tests are isolated and do not touch the user's default Bus DB.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "db"))

import agent_bus as bus  # noqa: E402


def _run_cli(args, env, cwd=None) -> Any:
    """Run agent_bus.py with explicit env, returning parsed JSON when possible."""
    cmd = [sys.executable, str(PROJECT_ROOT / "db" / "agent_bus.py"), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd)
    if proc.returncode != 0:
        raise AssertionError(f"agent_bus {args} failed: {proc.stdout}\n{proc.stderr}")
    if not proc.stdout.strip():
        return ""
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout


class _BusTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="agent-bus-test-"))
        self.db_path = self.tmpdir / "bus.db"
        self.artifacts = self.tmpdir / "runs"
        self.env = os.environ.copy()
        self.env["AGENT_BUS_DB"] = str(self.db_path)
        self.env["AGENT_BUS_ARTIFACTS"] = str(self.artifacts)
        # Mirror env into the current process so in-process bus._connect()
        # calls (used by tests that inspect state) target the same DB.
        os.environ["AGENT_BUS_DB"] = str(self.db_path)
        os.environ["AGENT_BUS_ARTIFACTS"] = str(self.artifacts)
        _run_cli(["init-db"], self.env)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---------------- helpers ----------------
    def _setup_route(self, agent_kind="omp", profile=None, max_in_flight=2, lease=300) -> dict:
        args = ["route-set", "--agent-kind", agent_kind,
                "--max-in-flight", str(max_in_flight),
                "--default-lease-seconds", str(lease),
                "--actor", "test"]
        if profile:
            args += ["--profile", profile]
        return dict(_run_cli(args, self.env))

    def _enqueue(self, caller, agent_kind="omp", profile=None, priority=5, payload=None,
                max_attempts=3, callback_name=None) -> dict:
        if payload is None:
            payload = {"instruction": "do work"}
        path = Path(self.tmpdir) / "payload.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        args = ["enqueue", "--caller", caller, "--agent-kind", agent_kind,
                "--priority", str(priority), "--payload-file", str(path),
                "--max-attempts", str(max_attempts)]
        if profile:
            args += ["--profile", profile]
        if callback_name:
            args += ["--callback-name", callback_name]
        return dict(_run_cli(args, self.env))

    def _write_report(self, content="worker did the thing\n"):
        path = Path(self.tmpdir) / "report.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def _write_result(self, payload) -> Path:
        path = Path(self.tmpdir) / "result.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _claim(self, worker_id="omp-w1", agent_kind="omp", profile=None, lease=300):
        args = ["claim", "--worker-id", worker_id, "--agent-kind", agent_kind,
                "--lease-seconds", str(lease)]
        if profile:
            args += ["--profile", profile]
        return _run_cli(args, self.env)


class TestRouteCRUD(_BusTestCase):
    def test_route_set_creates_and_updates(self):
        row = self._setup_route(max_in_flight=4, lease=120)
        self.assertEqual(row["max_in_flight"], 4)
        self.assertEqual(row["default_lease_s"], 120)
        # Update
        row = self._setup_route(max_in_flight=8, lease=180)
        self.assertEqual(row["max_in_flight"], 8)
        self.assertEqual(row["default_lease_s"], 180)

    def test_route_delete_blocked_by_pending_message(self):
        self._setup_route()
        self._enqueue("callerA")
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["route-delete", "--agent-kind", "omp"], self.env)
        self.assertIn("pending", str(ctx.exception))

    def test_route_status_reports_counts(self):
        self._setup_route(max_in_flight=1)
        self._enqueue("callerA")
        self._enqueue("callerA")
        status = _run_cli(["route-status"], self.env)
        omp = next(r for r in status if r["agent_kind"] == "omp")
        self.assertEqual(omp["pending_count"], 2)
        self.assertEqual(omp["leased_count"], 0)
        self.assertEqual(omp["dead_count"], 0)


class TestWorkerRegistration(_BusTestCase):
    def test_register_and_heartbeat_update(self):
        row = _run_cli([
            "worker-register", "--worker-id", "omp-w1", "--agent-kind", "omp",
            "--agent-name", "omp-dev-1", "--pane", "w1:p3", "--tab", "w1:t1"
        ], self.env)
        self.assertEqual(row["worker_id"], "omp-w1")
        self.assertEqual(row["agent_kind"], "omp")
        hb = _run_cli(["worker-heartbeat", "--worker-id", "omp-w1"], self.env)
        self.assertEqual(hb["worker_id"], "omp-w1")

    def test_worker_register_is_observable_but_does_not_change_route_capacity(self):
        self._setup_route(max_in_flight=1)
        for wid in ("w1", "w2", "w3"):
            _run_cli([
                "worker-register", "--worker-id", wid, "--agent-kind", "omp",
                "--agent-name", wid, "--pane", "w1:p1", "--tab", "w1:t1",
            ], self.env)
        status = _run_cli(["route-status"], self.env)
        omp = next(r for r in status if r["agent_kind"] == "omp")
        self.assertEqual(omp["active_workers"], 3)
        self.assertEqual(omp["max_in_flight"], 1)

    def test_worker_register_with_new_fields(self):
        row = _run_cli([
            "worker-register", "--worker-id", "full-w1", "--agent-kind", "omp",
            "--agent-name", "omp-dev-1", "--workspace-id", "wD",
            "--pane", "wD:p2", "--tab", "wD:t1",
            "--session-id", "ses_abc123",
            "--roles", "Developer,QA",
            "--launch-args=--auto-approve",
            "--permission-mode", "bypassPermissions",
            "--cwd", "E:\\WorkSpace\\github\\herdr-phalanx",
            "--registered-by", "supervisor",
            "--agent-version", "omp v18.0.4",
            "--herdr-version", "herdr 0.9.0",
        ], self.env)
        self.assertEqual(row["worker_id"], "full-w1")
        self.assertEqual(row["workspace_id"], "wD")
        self.assertEqual(row["session_id"], "ses_abc123")
        self.assertEqual(row["roles"], ["Developer", "QA"])
        self.assertEqual(row["launch_args"], "--auto-approve")
        self.assertEqual(row["permission_mode"], "bypassPermissions")
        self.assertEqual(row["cwd"], "E:\\WorkSpace\\github\\herdr-phalanx")
        self.assertEqual(row["registered_by"], "supervisor")
        self.assertEqual(row["agent_version"], "omp v18.0.4")
        self.assertEqual(row["herdr_version"], "herdr 0.9.0")
        self.assertEqual(row["messages_claimed"], 0)
        self.assertEqual(row["messages_completed"], 0)
        self.assertEqual(row["messages_failed"], 0)

    def test_worker_register_defaults(self):
        row = _run_cli([
            "worker-register", "--worker-id", "defaults-w1", "--agent-kind", "omp",
        ], self.env)
        self.assertIsNone(row.get("workspace_id"))
        self.assertIsNone(row.get("session_id"))
        self.assertEqual(row.get("roles"), [])
        self.assertEqual(row["registered_by"], "operator")
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["messages_claimed"], 0)
        self.assertEqual(row["messages_completed"], 0)
        self.assertEqual(row["messages_failed"], 0)


class TestWorkerMigration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="agent-bus-mig-"))
        self.db_path = self.tmpdir / "bus.db"
        self.artifacts = self.tmpdir / "runs"
        self.env = os.environ.copy()
        self.env["AGENT_BUS_DB"] = str(self.db_path)
        self.env["AGENT_BUS_ARTIFACTS"] = str(self.artifacts)
        os.environ["AGENT_BUS_DB"] = str(self.db_path)
        os.environ["AGENT_BUS_ARTIFACTS"] = str(self.artifacts)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_migrate_old_schema_adds_new_columns(self):
        """A legacy bus_workers with only the original 9 columns should be
        migrated forward by init-database without losing data."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("""CREATE TABLE bus_workers (
            worker_id TEXT PRIMARY KEY, agent_kind TEXT NOT NULL,
            profile TEXT, agent_name TEXT, pane_id TEXT, tab_id TEXT,
            status TEXT NOT NULL DEFAULT 'ready',
            last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
            metadata TEXT
        )""")
        conn.execute(
            "INSERT INTO bus_workers (worker_id, agent_kind, agent_name) VALUES (?,?,?)",
            ("legacy-w1", "omp", "legacy-omp"),
        )
        conn.commit()
        conn.close()

        _run_cli(["init-db"], self.env)

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(bus_workers)")}
        row = conn.execute("SELECT * FROM bus_workers WHERE worker_id=?",
                           ("legacy-w1",)).fetchone()
        conn.close()

        for expected in (
            "workspace_id", "session_id", "roles", "launch_args",
            "permission_mode", "cwd", "registered_by", "agent_version",
            "herdr_version", "messages_claimed", "messages_completed",
            "messages_failed",
        ):
            self.assertIn(expected, cols)
        self.assertIsNotNone(row)
        self.assertEqual(row["worker_id"], "legacy-w1")


class TestEnqueue(_BusTestCase):
    def test_enqueue_requires_instruction(self):
        self._setup_route()
        path = Path(self.tmpdir) / "payload.json"
        path.write_text(json.dumps({"context": {}}), encoding="utf-8")
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["enqueue", "--caller", "c1", "--agent-kind", "omp",
                      "--payload-file", str(path)], self.env)
        self.assertIn("instruction", str(ctx.exception))

    def test_enqueue_rejects_nonexistent_workspace(self):
        self._setup_route()
        path = Path(self.tmpdir) / "payload.json"
        path.write_text(json.dumps({"instruction": "do", "workspace": str(self.tmpdir / "nope")}),
                       encoding="utf-8")
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["enqueue", "--caller", "c1", "--agent-kind", "omp",
                      "--payload-file", str(path)], self.env)
        self.assertIn("workspace", str(ctx.exception))

    def test_enqueue_rejects_relative_workspace(self):
        self._setup_route()
        path = Path(self.tmpdir) / "payload.json"
        path.write_text(json.dumps({"instruction": "do", "workspace": "relative/path"}),
                       encoding="utf-8")
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["enqueue", "--caller", "c1", "--agent-kind", "omp",
                      "--payload-file", str(path)], self.env)
        self.assertIn("absolute", str(ctx.exception))

    def test_enqueue_rejects_unregistered_callback(self):
        self._setup_route()
        with self.assertRaises(AssertionError) as ctx:
            self._enqueue("c1", callback_name="nope")
        self.assertIn("callback", str(ctx.exception))

    def test_profile_round_trips(self):
        self._setup_route(agent_kind="hermes", profile="testing")
        msg = self._enqueue("c1", agent_kind="hermes", profile="testing")
        self.assertEqual(msg["profile"], "testing")
        self.assertEqual(msg["status"], "pending")


class TestClaim(_BusTestCase):
    def test_claim_only_pending_messages(self):
        self._setup_route(max_in_flight=1)
        m1 = self._enqueue("c1")
        claimed = self._claim()
        self.assertEqual(claimed["id"], m1["id"])
        self.assertEqual(claimed["status"], "leased")
        self.assertEqual(claimed["attempts"], 1)

    def test_route_capacity_caps_concurrent_claims(self):
        self._setup_route(max_in_flight=1)
        self._enqueue("c1")
        self._enqueue("c1")
        a = self._claim(worker_id="w1")
        self.assertIsNotNone(a)
        b = self._claim(worker_id="w2")
        self.assertIsNone(b)

    def test_priority_then_fifo(self):
        self._setup_route(max_in_flight=1)
        low = self._enqueue("c1", priority=1)
        high = self._enqueue("c1", priority=10)
        claimed = self._claim()
        self.assertEqual(claimed["id"], high["id"])
        self.assertNotEqual(claimed["id"], low["id"])

    def test_cross_route_isolation(self):
        self._setup_route(agent_kind="omp")
        self._setup_route(agent_kind="claude", max_in_flight=1)
        m_omp = self._enqueue("c1", agent_kind="omp")
        m_cl = self._enqueue("c1", agent_kind="claude")
        claimed = self._claim(agent_kind="omp")
        self.assertEqual(claimed["id"], m_omp["id"])
        claimed2 = self._claim(worker_id="w2", agent_kind="claude")
        self.assertEqual(claimed2["id"], m_cl["id"])

    def test_cancel_requested_message_not_claimed(self):
        self._setup_route()
        m = self._enqueue("c1")
        _run_cli(["cancel", "--caller", "c1", "--id", m["id"]], self.env)
        # Cancel of pending moves status directly to cancelled; claim must not pick it.
        claimed = self._claim()
        self.assertIsNone(claimed)


class TestHeartbeat(_BusTestCase):
    def test_heartbeat_extends_lease(self):
        self._setup_route(lease=30)
        self._enqueue("c1")
        claimed = self._claim(lease=30)
        hb = _run_cli(["heartbeat", "--id", claimed["id"],
                       "--worker-id", "omp-w1", "--lease", claimed["lease_id"],
                       "--lease-seconds", "120"], self.env)
        self.assertEqual(hb["id"], claimed["id"])

    def test_heartbeat_rejects_wrong_lease(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["heartbeat", "--id", claimed["id"],
                      "--worker-id", "omp-w1", "--lease", "wrong-lease"], self.env)
        self.assertIn("lease", str(ctx.exception).lower())

    def test_heartbeat_rejects_wrong_worker_id(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["heartbeat", "--id", claimed["id"],
                      "--worker-id", "someone-else", "--lease", claimed["lease_id"]], self.env)
        self.assertIn("worker_id", str(ctx.exception))

    def test_heartbeat_after_cancel_requested_rejected(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        # Simulate the lease going on; producer cancel sets cancel_requested.
        _run_cli(["cancel", "--caller", "c1", "--id", claimed["id"]], self.env)
        # The cancel on leased sets cancel_requested=1; heartbeat should now refuse.
        # Reload row to confirm.
        conn = bus._connect()
        row = conn.execute("SELECT cancel_requested, status FROM bus_messages WHERE id=?",
                            (claimed["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "leased")
        self.assertEqual(row["cancel_requested"], 1)
        with self.assertRaises(AssertionError):
            _run_cli(["heartbeat", "--id", claimed["id"],
                      "--worker-id", "omp-w1", "--lease", claimed["lease_id"]], self.env)


class TestComplete(_BusTestCase):
    def test_complete_requires_raw_report(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        result = self._write_result({"outcome": "succeeded", "summary": "ok"})
        with self.assertRaises(AssertionError):
            _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                      "--lease", claimed["lease_id"],
                      "--result-file", str(result)], self.env)

    def test_complete_persists_structured_result_and_artifact(self):
        self._setup_route()
        m = self._enqueue("c1")
        claimed = self._claim()
        report = self._write_report("hello world\n")
        result = self._write_result({"outcome": "succeeded", "summary": "ok",
                                       "files_modified": ["a.py"]})
        final = _run_cli(["complete", "--id", claimed["id"],
                          "--worker-id", "omp-w1", "--lease", claimed["lease_id"],
                          "--result-file", str(result),
                          "--raw-report-file", str(report)], self.env)
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["raw_report_sha256"], bus._sha256_file(report))
        # Artifact exists
        artifacts = list((self.artifacts / m["id"]).iterdir())
        self.assertTrue(any(p.name.startswith("attempt-") for p in artifacts))
        # Caller can pull
        listing = _run_cli(["result-list", "--caller", "c1", "--after-event", "0"], self.env)
        ids = [mm["id"] for mm in listing["messages"]]
        self.assertIn(m["id"], ids)

    def test_complete_rejects_double_completion(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        report = self._write_report()
        result = self._write_result({"outcome": "succeeded", "summary": "ok"})
        _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                  "--lease", claimed["lease_id"],
                  "--result-file", str(result),
                  "--raw-report-file", str(report)], self.env)
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                      "--lease", claimed["lease_id"],
                      "--result-file", str(result),
                      "--raw-report-file", str(report)], self.env)
        self.assertIn("status", str(ctx.exception).lower())

    def test_complete_rejects_stale_lease(self):
        self._setup_route()
        self._enqueue("c1", max_attempts=3)
        claimed = self._claim(lease=1)
        report = self._write_report()
        result = self._write_result({"outcome": "succeeded", "summary": "ok"})
        # Force lease into the past, then reap so the original lease is cleared.
        conn = bus._connect()
        past = "2000-01-01T00:00:00"
        conn.execute("UPDATE bus_messages SET lease_until=? WHERE id=?", (past, claimed["id"]))
        conn.commit()
        conn.close()
        _run_cli(["reap"], self.env)
        # A late Worker can't complete a Message that has been reaped.
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                      "--lease", claimed["lease_id"],
                      "--result-file", str(result),
                      "--raw-report-file", str(report)], self.env)
        self.assertIn("lease", str(ctx.exception).lower())

    def test_complete_requires_matching_callback(self):
        self._setup_route()
        # Register callback but don't pass callback_name on enqueue to keep this
        # purely about complete requiring a registered callback when set.
        _run_cli(["callback-register", "--name", "notify", "--kind", "command",
                   "--command-template", "echo {message_id}"], self.env)
        # Now enqueue with that callback_name.
        msg = self._enqueue("c1", callback_name="notify")
        claimed = self._claim()
        report = self._write_report()
        result = self._write_result({"outcome": "succeeded", "summary": "ok"})
        final = _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                          "--lease", claimed["lease_id"],
                          "--result-file", str(result),
                          "--raw-report-file", str(report)], self.env)
        self.assertEqual(final["status"], "succeeded")
        # callback_deliveries row should now exist for this Message.
        conn = bus._connect()
        delivery = conn.execute(
            "SELECT * FROM callback_deliveries WHERE message_id=?", (msg["id"],)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery["callback_name"], "notify")


class TestFail(_BusTestCase):
    def test_fail_under_max_attempts_requeues(self):
        self._setup_route()
        self._enqueue("c1", max_attempts=3)
        first = self._claim()
        report = self._write_report("boom\n")
        _run_cli(["fail", "--id", first["id"], "--worker-id", "omp-w1",
                   "--lease", first["lease_id"], "--reason", "boom",
                   "--raw-report-file", str(report)], self.env)
        # Re-claim should still succeed.
        again = self._claim(worker_id="omp-w2")
        self.assertIsNotNone(again)
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(again["attempts"], 2)

    def test_fail_at_max_attempts_dead(self):
        self._setup_route()
        self._enqueue("c1", max_attempts=1)
        claimed = self._claim()
        report = self._write_report()
        # attempts becomes 1, which equals max_attempts, so fail should dead-letter.
        _run_cli(["fail", "--id", claimed["id"], "--worker-id", "omp-w1",
                   "--lease", claimed["lease_id"], "--reason", "boom",
                   "--raw-report-file", str(report)], self.env)
        conn = bus._connect()
        row = conn.execute("SELECT status FROM bus_messages WHERE id=?",
                            (claimed["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "dead")

    def test_dead_message_requeued_with_same_id(self):
        self._setup_route()
        self._enqueue("c1", max_attempts=1)
        claimed = self._claim()
        report = self._write_report()
        _run_cli(["fail", "--id", claimed["id"], "--worker-id", "omp-w1",
                   "--lease", claimed["lease_id"], "--reason", "boom",
                   "--raw-report-file", str(report)], self.env)
        conn = bus._connect()
        row = conn.execute("SELECT status FROM bus_messages WHERE id=?", (claimed["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "dead")
        requeued = _run_cli(["requeue", "--caller", "c1", "--id", claimed["id"]], self.env)
        self.assertEqual(requeued["status"], "pending")
        self.assertEqual(requeued["id"], claimed["id"])
        # Attempt counter is reset
        self.assertEqual(requeued["attempts"], 0)


class TestCancelled(_BusTestCase):
    def test_cancelled_requires_cancel_requested(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        report = self._write_report()
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["cancelled", "--id", claimed["id"], "--worker-id", "omp-w1",
                       "--lease", claimed["lease_id"], "--reason", "stop",
                       "--raw-report-file", str(report)], self.env)
        self.assertIn("cancel_requested", str(ctx.exception))

    def test_cancelled_after_cancel_requested(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        _run_cli(["cancel", "--caller", "c1", "--id", claimed["id"]], self.env)
        report = self._write_report()
        final = _run_cli(["cancelled", "--id", claimed["id"],
                          "--worker-id", "omp-w1", "--lease", claimed["lease_id"],
                          "--reason", "stopped", "--raw-report-file", str(report)],
                         self.env)
        self.assertEqual(final["status"], "cancelled")


class TestCancel(_BusTestCase):
    def test_cancel_pending(self):
        self._setup_route()
        m = self._enqueue("c1")
        result = _run_cli(["cancel", "--caller", "c1", "--id", m["id"]], self.env)
        self.assertEqual(result["status"], "cancelled")

    def test_cancel_leased_sets_request(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        result = _run_cli(["cancel", "--caller", "c1", "--id", claimed["id"]], self.env)
        self.assertEqual(result["status"], "leased")
        self.assertEqual(result["cancel_requested"], 1)

    def test_cancel_rejects_other_caller(self):
        self._setup_route()
        m = self._enqueue("c1")
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["cancel", "--caller", "other", "--id", m["id"]], self.env)
        self.assertIn("caller", str(ctx.exception))


class TestReap(_BusTestCase):
    def test_reap_expired_lease_returns_to_pending(self):
        self._setup_route()
        self._enqueue("c1", max_attempts=3)
        claimed = self._claim(lease=1)
        # Force lease into the past via direct SQL.
        conn = bus._connect()
        past = "2000-01-01T00:00:00"
        conn.execute("UPDATE bus_messages SET lease_until=? WHERE id=?", (past, claimed["id"]))
        conn.commit()
        conn.close()
        reap = _run_cli(["reap"], self.env)
        self.assertEqual(reap["reaped"], 1)
        # Message is back to pending
        conn = bus._connect()
        row = conn.execute("SELECT status, worker_id, lease_id FROM bus_messages WHERE id=?",
                            (claimed["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["worker_id"])
        self.assertIsNone(row["lease_id"])

    def test_reap_at_max_attempts_dead(self):
        self._setup_route()
        self._enqueue("c1", max_attempts=1)
        claimed = self._claim(lease=1)
        conn = bus._connect()
        past = "2000-01-01T00:00:00"
        conn.execute("UPDATE bus_messages SET lease_until=? WHERE id=?", (past, claimed["id"]))
        conn.commit()
        conn.close()
        _run_cli(["reap"], self.env)
        conn = bus._connect()
        row = conn.execute("SELECT status FROM bus_messages WHERE id=?",
                            (claimed["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "dead")

    def test_late_result_does_not_overwrite_active_lease(self):
        self._setup_route()
        self._enqueue("c1", max_attempts=2)
        first = self._claim(lease=1)
        # Reap to pending.
        conn = bus._connect()
        past = "2000-01-01T00:00:00"
        conn.execute("UPDATE bus_messages SET lease_until=? WHERE id=?", (past, first["id"]))
        conn.commit()
        conn.close()
        _run_cli(["reap"], self.env)
        # Another worker claims.
        second = self._claim(worker_id="w2")
        self.assertIsNotNone(second)
        # First Worker tries to complete late; must fail.
        report = self._write_report()
        result = self._write_result({"outcome": "succeeded", "summary": "late"})
        with self.assertRaises(AssertionError):
            _run_cli(["complete", "--id", first["id"], "--worker-id", "omp-w1",
                       "--lease", first["lease_id"],
                       "--result-file", str(result),
                       "--raw-report-file", str(report)], self.env)
        # Active Worker still has the lease.
        conn = bus._connect()
        row = conn.execute("SELECT status, worker_id, lease_id FROM bus_messages WHERE id=?",
                            (first["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "leased")
        self.assertEqual(row["worker_id"], "w2")
        # Late result recorded as Event.
        events = _run_cli(["result-history", "--caller", "c1", "--id", first["id"]], self.env)
        types = [e["event_type"] for e in events["events"]]
        self.assertIn("late_result", types)


class TestResultPull(_BusTestCase):
    def test_caller_filter_isolation(self):
        self._setup_route()
        a = self._enqueue("callerA")
        b = self._enqueue("callerB")
        # Both succeed.
        for mid in (a["id"], b["id"]):
            claimed = self._claim()
            report = self._write_report()
            result = self._write_result({"outcome": "succeeded", "summary": "ok"})
            _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                      "--lease", claimed["lease_id"],
                      "--result-file", str(result),
                      "--raw-report-file", str(report)], self.env)
        a_listing = _run_cli(["result-list", "--caller", "callerA", "--after-event", "0"],
                              self.env)
        a_ids = [m["id"] for m in a_listing["messages"]]
        self.assertEqual(a_ids, [a["id"]])
        b_listing = _run_cli(["result-list", "--caller", "callerB", "--after-event", "0"],
                              self.env)
        b_ids = [m["id"] for m in b_listing["messages"]]
        self.assertEqual(b_ids, [b["id"]])

    def test_result_ack_archives_message(self):
        self._setup_route()
        self._enqueue("c1")
        claimed = self._claim()
        report = self._write_report()
        result = self._write_result({"outcome": "succeeded", "summary": "ok"})
        _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                   "--lease", claimed["lease_id"],
                   "--result-file", str(result),
                   "--raw-report-file", str(report)], self.env)
        before = _run_cli(["result-list", "--caller", "c1", "--after-event", "0"], self.env)
        self.assertEqual(len(before["messages"]), 1)
        _run_cli(["result-ack", "--caller", "c1", "--id", claimed["id"]], self.env)
        after = _run_cli(["result-list", "--caller", "c1", "--after-event", "0"], self.env)
        self.assertEqual(after["messages"], [])
        history = _run_cli(["result-history", "--caller", "c1", "--id", claimed["id"]],
                            self.env)
        self.assertEqual(history["message"]["status"], "succeeded")

    def test_result_cursor_is_incremental(self):
        self._setup_route()
        ids = [self._enqueue("c1")["id"] for _ in range(3)]
        # Complete all three
        for mid in ids:
            claimed = self._claim()
            report = self._write_report()
            result = self._write_result({"outcome": "succeeded", "summary": "ok"})
            _run_cli(["complete", "--id", mid, "--worker-id", "omp-w1",
                       "--lease", claimed["lease_id"],
                       "--result-file", str(result),
                       "--raw-report-file", str(report)], self.env)
        first = _run_cli(["result-list", "--caller", "c1", "--after-event", "0"], self.env)
        self.assertEqual(len(first["messages"]), 3)
        next_cursor = first["next_cursor"]
        # Pull only events after that cursor.
        second = _run_cli(["result-list", "--caller", "c1",
                            "--after-event", str(next_cursor)], self.env)
        self.assertEqual(second["messages"], [])

    def test_result_get_rejects_other_caller(self):
        self._setup_route()
        m = self._enqueue("c1")
        with self.assertRaises(AssertionError) as ctx:
            _run_cli(["result-get", "--caller", "other", "--id", m["id"]], self.env)
        self.assertIn("caller", str(ctx.exception))


class TestCallback(_BusTestCase):
    def _write_echo_script(self) -> Path:
        """Write a tiny PowerShell script that records the message id to a file."""
        script = self.tmpdir / "echo.ps1"
        script.write_text(
            'param([string]$MessageId)\n'
            "[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'echo.txt'), $MessageId)\n",
            encoding="utf-8",
        )
        return script

    def _register_echo_callback(self, script: Path):
        template = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{script}" -MessageId {{message_id}}'
        _run_cli(["callback-register", "--name", "echo", "--kind", "command",
                   "--command-template", template], self.env)

    def test_callback_delivery_creates_row_and_succeeds(self):
        self._setup_route()
        script = self._write_echo_script()
        self._register_echo_callback(script)
        m = self._enqueue("c1", callback_name="echo")
        claimed = self._claim()
        report = self._write_report()
        result = self._write_result({"outcome": "succeeded", "summary": "ok"})
        _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                   "--lease", claimed["lease_id"],
                   "--result-file", str(result),
                   "--raw-report-file", str(report)], self.env)
        # Delivery is pending.
        conn = bus._connect()
        delivery = conn.execute("SELECT * FROM callback_deliveries WHERE message_id=?",
                                 (m["id"],)).fetchone()
        conn.close()
        self.assertEqual(delivery["status"], "pending")
        out = _run_cli(["callback-deliver"], self.env)
        # callback-deliver --once returns the result for the single delivery.
        self.assertEqual(out["delivered"][0]["result"], "delivered")
        # Check echo file was written.
        self.assertTrue((self.tmpdir / "echo.txt").exists())
        self.assertEqual((self.tmpdir / "echo.txt").read_text(encoding="utf-8").strip(),
                         m["id"])
        # Delivery is now delivered.
        conn = bus._connect()
        delivery = conn.execute("SELECT * FROM callback_deliveries WHERE id=?",
                                 (delivery["id"],)).fetchone()
        conn.close()
        self.assertEqual(delivery["status"], "delivered")
        # Message status remains succeeded.
        conn = bus._connect()
        row = conn.execute("SELECT status FROM bus_messages WHERE id=?", (m["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "succeeded")

    def test_callback_failure_does_not_revert_message(self):
        self._setup_route()
        # Register a callback that always fails (no such command).
        _run_cli(["callback-register", "--name", "fail", "--kind", "command",
                   "--command-template", "definitely-not-a-real-command-12345"],
                  self.env)
        m = self._enqueue("c1", callback_name="fail")
        claimed = self._claim()
        report = self._write_report()
        result = self._write_result({"outcome": "succeeded", "summary": "ok"})
        _run_cli(["complete", "--id", claimed["id"], "--worker-id", "omp-w1",
                   "--lease", claimed["lease_id"],
                   "--result-file", str(result),
                   "--raw-report-file", str(report)], self.env)
        # Drive one delivery attempt.
        out = _run_cli(["callback-deliver"], self.env)
        self.assertEqual(out["delivered"][0]["result"], "pending")
        # Message is still succeeded.
        conn = bus._connect()
        row = conn.execute("SELECT status FROM bus_messages WHERE id=?", (m["id"],)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "succeeded")


class TestSQLiteBusyRetry(_BusTestCase):
    def test_writer_succeeds_under_wal(self):
        # Sanity check: an insert under WAL works in a follow-up connection.
        self._setup_route()
        self._enqueue("c1")
        conn = bus._connect()
        rows = conn.execute("SELECT COUNT(*) FROM bus_messages").fetchall()
        conn.close()
        self.assertEqual(rows[0][0], 1)


if __name__ == "__main__":
    unittest.main()
