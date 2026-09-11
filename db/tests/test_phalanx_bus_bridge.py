"""Phalanx ↔ Agent Bus Bridge tests.

Tests the projection module in isolation using temporary Phalanx and Agent
Bus databases.  No modifications are made to either Core — the bridge is
exercised through its public API.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "db"))

import phalanx_bus_bridge as bridge  # noqa: E402


def _make_task(
    task_id="task_001",
    run_id="run_001",
    spec="do the thing",
    execution_mode="managed",
    preferred_agent="omp",
    assigned_role="Developer",
) -> dict:
    return {
        "id": task_id,
        "run_id": run_id,
        "spec": spec,
        "execution_mode": execution_mode,
        "preferred_agent": preferred_agent,
        "assigned_role": assigned_role,
        "retry_count": 0,
        "max_retries": 3,
    }


class _BridgeTestCase(unittest.TestCase):
    """Base case that creates isolated Phalanx + Agent Bus DBs and seeds
    a route so the Bus can enqueue."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="bridge-test-"))
        self.phalanx_db = self.tmpdir / "phalanx.db"
        self.bus_db = self.tmpdir / "agent-bus.db"
        self.artifacts = self.tmpdir / "runs"

        # Mirror env into the current process so AgentBus Core picks up the
        # same paths the test uses.
        self._old_env = {
            "AGENT_BUS_DB": os.environ.get("AGENT_BUS_DB"),
            "AGENT_BUS_ARTIFACTS": os.environ.get("AGENT_BUS_ARTIFACTS"),
            "PHALANX_DB": os.environ.get("PHALANX_DB"),
        }
        os.environ["AGENT_BUS_DB"] = str(self.bus_db)
        os.environ["AGENT_BUS_ARTIFACTS"] = str(self.artifacts)
        os.environ["PHALANX_DB"] = str(self.phalanx_db)

        # --- init Phalanx DB with schema + bridge table ---
        schema_sql = (PROJECT_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
        conn = sqlite3.connect(str(self.phalanx_db))
        conn.executescript(schema_sql)
        conn.commit()
        conn.close()
        bridge.init_bridge(self.phalanx_db)

        # --- init Agent Bus DB ---
        bus_schema_sql = (PROJECT_ROOT / "db" / "agent_bus_schema.sql").read_text(encoding="utf-8")
        from agent_bus_core import AgentBus

        self.bus = AgentBus(
            database_path=self.bus_db,
            artifacts_root=self.artifacts,
        )
        self.bus.init_database(bus_schema_sql)

        # Seed a route so enqueue succeeds.
        self.bus.set_route(
            agent_kind="omp",
            max_in_flight=5,
            default_lease_seconds=300,
            enabled=1,
        )

    def tearDown(self):
        # Restore env.
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---------------- helpers ----------------

    def _seed_run(self, run_id="run_001"):
        conn = sqlite3.connect(str(self.phalanx_db))
        conn.execute(
            "INSERT INTO runs (id, objective, coordinator, status) VALUES (?,?,?,?)",
            (run_id, "test run", "hermes", "active"),
        )
        conn.commit()
        conn.close()

    def _seed_task(self, task_id="task_001", run_id="run_001", execution_mode="managed"):
        conn = sqlite3.connect(str(self.phalanx_db))
        conn.execute(
            """INSERT INTO tasks (id, run_id, spec, status, execution_mode, preferred_agent, assigned_role)
               VALUES (?,?,?,?,?,?,?)""",
            (task_id, run_id, "do the thing", "pending", execution_mode, "omp", "Developer"),
        )
        conn.commit()
        conn.close()

    def _seed_dispatch(self, dispatch_id="disp_001", task_id="task_001", run_id="run_001",
                       status="running"):
        conn = sqlite3.connect(str(self.phalanx_db))
        conn.execute(
            """INSERT INTO dispatches (id, task_id, run_id, agent_name, agent_kind, status)
               VALUES (?,?,?,?,?,?)""",
            (dispatch_id, task_id, run_id, "omp-w1", "omp", status),
        )
        conn.commit()
        conn.close()

    def _phalanx_query(self, sql, params=()):
        conn = sqlite3.connect(str(self.phalanx_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        conn.close()
        return dict(row) if row else None


class TestTaskToMessageProjection(_BridgeTestCase):
    """bridge_task_to_message projects fields correctly."""

    def test_task_to_message_projection(self):
        self._seed_run()
        self._seed_task()
        self._seed_dispatch()

        task = _make_task()
        result = bridge.bridge_task_to_message(
            task_record=task,
            dispatch_id="disp_001",
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
            agent_bus_instance=self.bus,
        )

        self.assertTrue(result["enqueued"])
        self.assertFalse(result["existing"])
        message_id = result["message_id"]
        self.assertTrue(message_id.startswith("msg_"))

        # Verify the Bus Message payload carries the projected fields.
        bus_conn = sqlite3.connect(str(self.bus_db))
        bus_conn.row_factory = sqlite3.Row
        msg = bus_conn.execute("SELECT * FROM bus_messages WHERE id=?", (message_id,)).fetchone()
        bus_conn.close()

        self.assertIsNotNone(msg)
        payload = json.loads(msg["payload"])
        self.assertEqual(payload["instruction"], "do the thing")
        self.assertEqual(payload["bridge"]["task_id"], "task_001")
        self.assertEqual(payload["bridge"]["dispatch_id"], "disp_001")
        self.assertEqual(payload["bridge"]["run_id"], "run_001")
        self.assertEqual(payload["bridge"]["mode"], "managed")
        self.assertEqual(msg["agent_kind"], "omp")
        self.assertEqual(msg["caller_id"], bridge.BRIDGE_CALLER_ID)

        # Verify the mapping row was written.
        mapping = self._phalanx_query(
            "SELECT * FROM bus_message_map WHERE task_id=?", ("task_001",)
        )
        self.assertIsNotNone(mapping)
        self.assertEqual(mapping["message_id"], message_id)
        self.assertEqual(mapping["dispatch_id"], "disp_001")
        self.assertEqual(mapping["execution_mode"], "managed")


class TestManagedVsRawPaneEnvelope(_BridgeTestCase):
    """The two execution modes produce structurally different envelopes."""

    def test_managed_mode_envelope_differs_from_raw_pane(self):
        self._seed_run()
        self._seed_task(execution_mode="managed")
        self._seed_dispatch()

        managed_task = _make_task(execution_mode="managed")
        managed_result = bridge.bridge_task_to_message(
            task_record=managed_task,
            dispatch_id="disp_001",
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
            agent_bus_instance=self.bus,
        )

        # Second task for raw-pane.
        self._seed_task(task_id="task_002", execution_mode="raw-pane")
        self._seed_dispatch(dispatch_id="disp_002", task_id="task_002")
        raw_task = _make_task(task_id="task_002", execution_mode="raw-pane")
        raw_result = bridge.bridge_task_to_message(
            task_record=raw_task,
            dispatch_id="disp_002",
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
            agent_bus_instance=self.bus,
        )

        # Load payloads.
        bus_conn = sqlite3.connect(str(self.bus_db))
        bus_conn.row_factory = sqlite3.Row
        managed_msg = bus_conn.execute(
            "SELECT * FROM bus_messages WHERE id=?", (managed_result["message_id"],)
        ).fetchone()
        raw_msg = bus_conn.execute(
            "SELECT * FROM bus_messages WHERE id=?", (raw_result["message_id"],)
        ).fetchone()
        bus_conn.close()

        managed_payload = json.loads(managed_msg["payload"])
        raw_payload = json.loads(raw_msg["payload"])

        # Managed has constraints + context + dispatch_id.
        self.assertIn("constraints", managed_payload)
        self.assertIn("context", managed_payload)
        self.assertIn("dispatch_id", managed_payload["bridge"])
        self.assertTrue(managed_payload["constraints"].get("require_task_complete"))

        # Raw-pane has none of those.
        self.assertNotIn("constraints", raw_payload)
        self.assertNotIn("context", raw_payload)
        self.assertNotIn("dispatch_id", raw_payload["bridge"])


class TestCompletedBusMessageUpdatesDispatch(_BridgeTestCase):
    """on_message_complete promotes a succeeded result into Phalanx state."""

    def test_completed_bus_message_updates_dispatch(self):
        self._seed_run()
        self._seed_task()
        self._seed_dispatch()

        task = _make_task()
        proj = bridge.bridge_task_to_message(
            task_record=task,
            dispatch_id="disp_001",
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
            agent_bus_instance=self.bus,
        )
        message_id = proj["message_id"]

        # Simulate a completed Bus Message with structured result.
        bus_message = {
            "id": message_id,
            "status": "succeeded",
            "result": {
                "outcome": "succeeded",
                "summary": "all done",
                "files_modified": ["a.py", "b.py"],
            },
        }

        outcome = bridge.on_message_complete(
            bus_message=bus_message,
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
        )

        self.assertTrue(outcome["updated"])
        self.assertEqual(outcome["dispatch_id"], "disp_001")

        # Verify dispatch was promoted.
        disp = self._phalanx_query("SELECT * FROM dispatches WHERE id=?", ("disp_001",))
        self.assertEqual(disp["status"], "completed")
        self.assertEqual(disp["outcome"], "succeeded")
        self.assertEqual(disp["summary"], "all done")
        self.assertEqual(json.loads(disp["files_modified"]), ["a.py", "b.py"])
        self.assertIsNotNone(disp["completed_at"])

        # Verify task was promoted.
        task_row = self._phalanx_query("SELECT * FROM tasks WHERE id=?", ("task_001",))
        self.assertEqual(task_row["status"], "completed")
        self.assertIsNotNone(task_row["completed_at"])

        # Verify audit event was written.
        conn = sqlite3.connect(str(self.phalanx_db))
        conn.row_factory = sqlite3.Row
        event = conn.execute(
            "SELECT * FROM events WHERE dispatch_id=? AND event_type=?",
            ("disp_001", "worker_done"),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(event)


class TestIdempotentEnqueue(_BridgeTestCase):
    """A second projection for the same Task returns the existing mapping."""

    def test_idempotent_enqueue(self):
        self._seed_run()
        self._seed_task()
        self._seed_dispatch()

        task = _make_task()

        # First call enqueues.
        first = bridge.bridge_task_to_message(
            task_record=task,
            dispatch_id="disp_001",
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
            agent_bus_instance=self.bus,
        )
        self.assertTrue(first["enqueued"])
        self.assertFalse(first["existing"])

        # Second call returns existing mapping.
        second = bridge.bridge_task_to_message(
            task_record=task,
            dispatch_id="disp_001",
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
            agent_bus_instance=self.bus,
        )
        self.assertFalse(second["enqueued"])
        self.assertTrue(second["existing"])
        self.assertEqual(second["message_id"], first["message_id"])

        # Only one Bus Message exists for this task.
        conn = sqlite3.connect(str(self.bus_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM bus_messages WHERE caller_id=?",
            (bridge.BRIDGE_CALLER_ID,),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


class TestTerminalDispatchRejected(_BridgeTestCase):
    """on_message_complete refuses to overwrite a terminal Dispatch."""

    def test_terminal_dispatch_rejected(self):
        self._seed_run()
        self._seed_task()
        self._seed_dispatch(status="completed")  # already terminal

        task = _make_task()
        proj = bridge.bridge_task_to_message(
            task_record=task,
            dispatch_id="disp_001",
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
            agent_bus_instance=self.bus,
        )

        bus_message = {
            "id": proj["message_id"],
            "status": "succeeded",
            "result": {"outcome": "succeeded", "summary": "late result"},
        }

        outcome = bridge.on_message_complete(
            bus_message=bus_message,
            phalanx_db_path=self.phalanx_db,
            bus_db_path=self.bus_db,
        )

        self.assertFalse(outcome["updated"])
        self.assertIn("already terminal", outcome["reason"])

        # Dispatch status unchanged.
        disp = self._phalanx_query("SELECT * FROM dispatches WHERE id=?", ("disp_001",))
        self.assertEqual(disp["status"], "completed")
        self.assertNotEqual(disp["summary"], "late result")


if __name__ == "__main__":
    unittest.main()
