#!/usr/bin/env python3
"""Unit tests for db/coordinator.py — HerdrClient, WorkerSelector, Coordinator, Constitution."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from db import phalanx_db as db
from db.coordinator import (
    Coordinator,
    CoordinatorConfig,
    Constitution,
    ConstitutionTask,
    HerdrClient,
    PhalanxDB,
    WorkerSelector,
)


class TestHerdrClient(unittest.TestCase):
    def setUp(self):
        self.client = HerdrClient(herdr_bin="herdr")

    @patch("db.coordinator.subprocess.run")
    def test_agent_list_parses_json(self, mock_run):
        mock_run.return_value = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"result": {"agents": [{"name": "d1", "agent_status": "idle"}]}}),
            stderr="",
        )
        self.assertEqual(self.client.agent_list()[0]["name"], "d1")

    @patch("db.coordinator.subprocess.run")
    def test_agent_list_non_json(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="bad", stderr="")
        self.assertEqual(self.client.agent_list(), [])

    @patch("db.coordinator.subprocess.run")
    def test_agent_wait_idle(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=0, stdout="idle", stderr="")
        out, to = self.client.agent_wait("d1", ["idle"], timeout_ms=5000)
        self.assertFalse(to)
        self.assertEqual(out, "idle")

    @patch("db.coordinator.subprocess.run")
    def test_agent_wait_timeout(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="to")
        out, to = self.client.agent_wait("d1", ["idle"], timeout_ms=5000)
        self.assertTrue(to)

    @patch("db.coordinator.subprocess.run")
    def test_agent_start_raises(self, mock_run):
        mock_run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="fail")
        with self.assertRaises(RuntimeError):
            self.client.agent_start("d1", "omp", "w1:p1")


class TestPhalanxDB(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "phalanx.db"
        self.art = self.tmp / "artifacts"
        os.environ["PHALANX_DB"] = str(self.db_path)
        os.environ["PHALANX_ARTIFACTS"] = str(self.art)
        old_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_init_db(SimpleNamespace())
        finally:
            db.out = old_out
        self.db = PhalanxDB()

    def tearDown(self):
        os.environ.pop("PHALANX_DB", None)
        os.environ.pop("PHALANX_ARTIFACTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cap_dict(self, action, args):
        result = {}
        old_out = db.out
        db.out = lambda data, table=False: result.update(data) if isinstance(data, dict) else None
        try:
            action(args)
        finally:
            db.out = old_out
        return result

    def _cap_list(self, action, args):
        result = []
        old_out = db.out
        db.out = lambda data, table=False: result.extend(data) if isinstance(data, list) else None
        try:
            action(args)
        finally:
            db.out = old_out
        return result

    def test_run_status(self):
        r = self._cap_dict(db.cmd_run_create, SimpleNamespace(
            objective="t", workspace="w1", coordinator="hermes",
            metadata="{}", workflow_version="legacy-v1", workflow_profile=None,
        ))
        status = self.db.run_status(r["id"])
        self.assertEqual(status["status"], "active")

    def test_task_ready_empty(self):
        r = self._cap_dict(db.cmd_run_create, SimpleNamespace(
            objective="t", workspace="w1", coordinator="hermes",
            metadata="{}", workflow_version="legacy-v1", workflow_profile=None,
        ))
        self.assertEqual(self.db.task_ready(r["id"]), [])

    def test_current_capabilities_empty(self):
        self.assertEqual(self.db.current_capabilities(), [])

    def test_dispatch_list_empty(self):
        r = self._cap_dict(db.cmd_run_create, SimpleNamespace(
            objective="t", workspace="w1", coordinator="hermes",
            metadata="{}", workflow_version="legacy-v1", workflow_profile=None,
        ))
        self.assertEqual(self.db.dispatch_list(r["id"]), [])


class TestWorkerSelector(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "phalanx.db"
        self.art = self.tmp / "artifacts"
        os.environ["PHALANX_DB"] = str(self.db_path)
        os.environ["PHALANX_ARTIFACTS"] = str(self.art)
        old_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_init_db(SimpleNamespace())
        finally:
            db.out = old_out
        self.db = PhalanxDB()
        self.herdr = HerdrClient()
        self.selector = WorkerSelector(self.db, self.herdr)

    def tearDown(self):
        os.environ.pop("PHALANX_DB", None)
        os.environ.pop("PHALANX_ARTIFACTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cap_dict(self, action, args):
        result = {}
        old_out = db.out
        db.out = lambda data, table=False: result.update(data) if isinstance(data, dict) else None
        try:
            action(args)
        finally:
            db.out = old_out
        return result

    def _make_run(self):
        return self._cap_dict(db.cmd_run_create, SimpleNamespace(
            objective="t", workspace="w1", coordinator="hermes",
            metadata="{}", workflow_version="legacy-v1", workflow_profile=None,
        ))["id"]

    def _rec(self, kind, profile, roles, agent_name, pane):
        old_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_capability_record(SimpleNamespace(
                kind=kind, profile=profile, level="verified",
                capability_scope="execution", command="test",
                command_type=None, execution_mode="managed",
                executable_path="/bin/test", version="1.0",
                herdr_version="0.9.0", integration="hooks",
                launch_args="--auto", model="longcat-2.0",
                native_parameters="{}", normalized_intensity="medium",
                permission_mode="auto", config_hash="abc",
                shell_path="pwsh", fingerprint="fp1",
                evidence="{}", max_age_s=86400,
            ))
        finally:
            db.out = old_out
        conn = db.get_db()
        conn.execute(
            "UPDATE capability_observations SET evidence=? WHERE agent_kind=? AND profile IS ?",
            (json.dumps({"roles": roles, "agent_name": agent_name, "pane_id": pane, "tab_id": "w1:t1"}), kind, profile),
        )
        conn.commit()
        conn.close()

    def test_no_caps_returns_none(self):
        run_id = self._make_run()
        self.assertIsNone(self.selector.find_worker(run_id, "Developer"))

    def test_selects_matching(self):
        run_id = self._make_run()
        self._rec("omp", "medium", ["Developer"], "dev1", "w1:p1")
        result = self.selector.find_worker(run_id, "Developer")
        self.assertIsNotNone(result)
        self.assertEqual(result.agent_name, "dev1")

    def test_excludes_occupied(self):
        run_id = self._make_run()
        self._rec("omp", "medium", ["Developer"], "dev1", "w1:p1")
        conn = db.get_db()
        conn.execute("INSERT INTO tasks (id, run_id, spec, status) VALUES (?,?,?,?)",
                     ("task_x", run_id, "test", "dispatched"))
        conn.execute("""INSERT INTO dispatches (id, task_id, run_id, agent_name, agent_kind, pane_id, status)
                        VALUES (?,?,?,?,?,?,?)""",
                     ("disp_x", "task_x", run_id, "dev1", "omp", "w1:p1", "running"))
        conn.commit()
        conn.close()
        self.assertIsNone(self.selector.find_worker(run_id, "Developer"))

    def test_prefers_matching_kind(self):
        run_id = self._make_run()
        self._rec("omp", "medium", ["Developer"], "omp-w", "w1:p1")
        self._rec("claudecode", "medium", ["Developer"], "claude-w", "w1:p2")
        result = self.selector.find_worker(run_id, "Developer", preferred_agent="claudecode")
        self.assertEqual(result.agent_kind, "claudecode")

    def test_filters_by_role(self):
        run_id = self._make_run()
        self._rec("omp", "medium", ["QA"], "qa-w", "w1:p1")
        self.assertIsNone(self.selector.find_worker(run_id, "Developer"))


class TestCoordinatorModes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "phalanx.db"
        self.art = self.tmp / "artifacts"
        os.environ["PHALANX_DB"] = str(self.db_path)
        os.environ["PHALANX_ARTIFACTS"] = str(self.art)
        old_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_init_db(SimpleNamespace())
        finally:
            db.out = old_out
        self.db = PhalanxDB()
        self.events = []

    def tearDown(self):
        os.environ.pop("PHALANX_DB", None)
        os.environ.pop("PHALANX_ARTIFACTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cap_dict(self, action, args):
        result = {}
        old_out = db.out
        db.out = lambda data, table=False: result.update(data) if isinstance(data, dict) else None
        try:
            action(args)
        finally:
            db.out = old_out
        return result

    def _make_run_task(self):
        run_id = self._cap_dict(db.cmd_run_create, SimpleNamespace(
            objective="t", workspace="w1", coordinator="hermes",
            metadata="{}", workflow_version="legacy-v1", workflow_profile=None,
        ))["id"]
        task = self._cap_dict(db.cmd_task_add, SimpleNamespace(
            run=run_id, spec="do the thing", deps="[]",
            role="Developer", agent=None, preferred_agent=None,
            execution_mode="managed", acceptance_mode="legacy",
            delivery_mode="direct", max_retries=3,
            checklist_artifact_id=None, coordinator="hermes",
        ))
        return run_id, task["id"]

    def _mk(self, agent_name=None, pane="w1:p1", delivery_mode="direct"):
        return Coordinator(CoordinatorConfig(
            run_id="", coordinator="hermes",
            agent_name=agent_name, agent_kind="omp",
            pane=pane, tab="w1:t1", profile="medium",
            dispatch_mode="blocking", wait_timeout_ms=5000,
            delivery_mode=delivery_mode,
            on_event=lambda et, d: self.events.append({"type": et, **d}),
        ))

    def test_direct_uses_config_agent(self):
        run_id, _ = self._make_run_task()
        coord = self._mk(agent_name="mydev")
        coord.config.run_id = run_id
        coord._herdr = MagicMock()
        coord._herdr.agent_prompt.return_value = ""
        coord._herdr.agent_wait.return_value = ("", False)
        coord._herdr.agent_read.return_value = "done"
        coord._herdr.agent_get.return_value = {"result": {"agent": {"agent_status": "idle"}}}
        coord._db.dispatch_complete_from_output = MagicMock(return_value={"status": "completed"})
        coord._db.run_status = MagicMock(side_effect=[
            {"status": "active", "pending_tasks": 1, "running_tasks": 0},
            {"status": "active", "pending_tasks": 0, "running_tasks": 0},
        ])
        coord._db.task_ready = MagicMock(side_effect=[
            [{"id": "task_1", "run_id": run_id, "spec": "do the thing",
              "assigned_role": "Developer", "preferred_agent": None,
              "execution_mode": "managed", "deps": "[]"}],
            [],
        ])
        coord._db.task_claim = MagicMock(return_value={
            "task": {"id": "task_1", "status": "dispatched"},
            "dispatch": {"id": "disp_1"},
        })
        coord.run()
        coord._herdr.agent_prompt.assert_called_once()
        self.assertEqual(coord._herdr.agent_prompt.call_args[0][0], "mydev")

    def test_hrbp_no_worker(self):
        run_id, _ = self._make_run_task()
        coord = self._mk(agent_name=None, delivery_mode="hrbp")
        coord.config.run_id = run_id
        coord._db.run_status = MagicMock(side_effect=[
            {"status": "active", "pending_tasks": 1, "running_tasks": 0},
            {"status": "active", "pending_tasks": 0, "running_tasks": 0},
        ])
        coord._db.task_ready = MagicMock(return_value=[
            {"id": "task_1", "run_id": run_id, "spec": "do the thing",
             "assigned_role": "Developer", "preferred_agent": None,
             "execution_mode": "managed", "deps": "[]"},
        ])
        coord.run()
        self.assertTrue(any(e["type"] == "no_worker_available" for e in self.events))


class TestConstitution(unittest.TestCase):
    """Constitution parser for artifact-v1 workflow."""

    def test_from_artifact(self):
        constitution_data = {
            "objective": "Build a feature",
            "workflow_profile": "standard",
            "tasks": [
                {"id": "t1", "spec": "Design the API", "role": "Architect", "deps": []},
                {"id": "t2", "spec": "Implement the code", "role": "Developer", "deps": ["t1"]},
                {"id": "t3", "spec": "Write tests", "role": "QA", "deps": ["t2"]},
            ],
            "budget": {"agent_limit": 10, "parallelism_limit": 2},
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(constitution_data, f)
            path = f.name
        try:
            c = Constitution.from_artifact(path, "run_1")
            self.assertEqual(c.objective, "Build a feature")
            self.assertEqual(len(c.tasks), 3)
            self.assertEqual(c.tasks[0].id, "t1")
            self.assertEqual(c.tasks[2].deps, ["t2"])
        finally:
            os.unlink(path)

    def test_ready_tasks(self):
        c = Constitution(
            run_id="r1", objective="test", workflow_profile="standard",
            tasks=[
                ConstitutionTask(id="t1", spec="a", role="Developer", preferred_agent=None,
                                  execution_mode="managed", deps=[], checklist_id=None, output_slots=[]),
                ConstitutionTask(id="t2", spec="b", role="Developer", preferred_agent=None,
                                  execution_mode="managed", deps=["t1"], checklist_id=None, output_slots=[]),
                ConstitutionTask(id="t3", spec="c", role="QA", preferred_agent=None,
                                  execution_mode="managed", deps=["t1", "t2"], checklist_id=None, output_slots=[]),
            ],
            budget={}, gates=[], amendment_rules={},
        )
        ready = c.ready_tasks(set())
        self.assertEqual([t.id for t in ready], ["t1"])
        ready = c.ready_tasks({"t1"})
        self.assertEqual([t.id for t in ready], ["t2"])
        ready = c.ready_tasks({"t1", "t2"})
        self.assertEqual([t.id for t in ready], ["t3"])
        ready = c.ready_tasks({"t1", "t2", "t3"})
        self.assertEqual(ready, [])

    def test_get_task(self):
        c = Constitution(
            run_id="r1", objective="test", workflow_profile="standard",
            tasks=[
                ConstitutionTask(id="t1", spec="a", role="Developer", preferred_agent=None,
                                  execution_mode="managed", deps=[], checklist_id=None, output_slots=[]),
            ],
            budget={}, gates=[], amendment_rules={},
        )
        self.assertIsNotNone(c.get_task("t1"))
        self.assertIsNone(c.get_task("t2"))


class TestCoordinatorArtifactLoop(unittest.TestCase):
    """Coordinator artifact mode (HRBP Semi-Auto)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "phalanx.db"
        self.art = self.tmp / "artifacts"
        os.environ["PHALANX_DB"] = str(self.db_path)
        os.environ["PHALANX_ARTIFACTS"] = str(self.art)
        old_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_init_db(SimpleNamespace())
        finally:
            db.out = old_out
        self.db = PhalanxDB()
        self.events = []

    def tearDown(self):
        os.environ.pop("PHALANX_DB", None)
        os.environ.pop("PHALANX_ARTIFACTS", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cap_dict(self, action, args):
        result = {}
        old_out = db.out
        db.out = lambda data, table=False: result.update(data) if isinstance(data, dict) else None
        try:
            action(args)
        finally:
            db.out = old_out
        return result

    def _make_artifact_run(self):
        return self._cap_dict(db.cmd_run_create, SimpleNamespace(
            objective="artifact test", workspace="w1", coordinator="hermes",
            metadata="{}", workflow_version="artifact-v1", workflow_profile="standard",
            force=True,
        ))["id"]

    def _mk(self, agent_name=None, pane="w1:p1", delivery_mode="direct"):
        return Coordinator(CoordinatorConfig(
            run_id="", coordinator="hermes",
            agent_name=agent_name, agent_kind="omp",
            pane=pane, tab="w1:t1", profile="medium",
            dispatch_mode="blocking", wait_timeout_ms=5000,
            delivery_mode=delivery_mode,
            on_event=lambda et, d: self.events.append({"type": et, **d}),
        ))

    def test_artifact_loop_basic(self):
        run_id = self._make_artifact_run()
        coord = self._mk(agent_name="dev1")
        coord.config.run_id = run_id
        constitution = Constitution(
            run_id=run_id, objective="test", workflow_profile="standard",
            tasks=[
                ConstitutionTask(id="t1", spec="do thing", role="Developer",
                                  preferred_agent=None, execution_mode="managed",
                                  deps=[], checklist_id=None, output_slots=[]),
            ],
            budget={}, gates=[], amendment_rules={},
        )
        coord._herdr = MagicMock()
        coord._herdr.agent_prompt.return_value = ""
        coord._herdr.agent_wait.return_value = ("", False)
        coord._herdr.agent_read.return_value = "done"
        coord._herdr.agent_get.return_value = {"result": {"agent": {"agent_status": "idle"}}}
        coord._db.dispatch_complete_from_output = MagicMock(return_value={"status": "completed"})
        coord._db.task_claim = MagicMock(return_value={
            "task": {"id": "t1", "status": "dispatched"},
            "dispatch": {"id": "disp_1"},
        })
        coord._db.dispatch_list = MagicMock(return_value=[])
        coord._db.run_status = MagicMock(side_effect=[
            {"status": "active", "pending_tasks": 1, "running_tasks": 0},
            {"status": "active", "pending_tasks": 0, "running_tasks": 0},
        ])
        coord.run_artifact_loop(constitution)
        coord._herdr.agent_prompt.assert_called_once()


if __name__ == "__main__":
    unittest.main()
