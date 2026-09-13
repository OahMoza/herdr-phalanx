"""
Unit tests for phalanx_db.py
Run: python -m unittest db.tests.test_phalanx_db -v
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import phalanx_db as db


class TestParseListArg(unittest.TestCase):
    def test_json_array_with_quotes(self):
        result = json.loads(db.parse_list_arg('["a.py", "b.py"]'))
        self.assertEqual(result, ["a.py", "b.py"])

    def test_comma_separated(self):
        result = json.loads(db.parse_list_arg("a.py,b.py"))
        self.assertEqual(result, ["a.py", "b.py"])

    def test_brackets_without_quotes(self):
        result = json.loads(db.parse_list_arg("[a.py, b.py]"))
        self.assertEqual(result, ["a.py", "b.py"])

    def test_single_brackets_without_quotes(self):
        result = json.loads(db.parse_list_arg("[string_utils.py]"))
        self.assertEqual(result, ["string_utils.py"])

    def test_empty_string(self):
        result = json.loads(db.parse_list_arg(""))
        self.assertEqual(result, [])

    def test_empty_array(self):
        result = json.loads(db.parse_list_arg("[]"))
        self.assertEqual(result, [])

    def test_single_item_no_brackets(self):
        result = json.loads(db.parse_list_arg("a.py"))
        self.assertEqual(result, ["a.py"])

    def test_with_spaces_around_commas(self):
        result = json.loads(db.parse_list_arg("a.py, b.py , c.py"))
        self.assertEqual(result, ["a.py", "b.py", "c.py"])

    def test_none_input(self):
        result = json.loads(db.parse_list_arg(None))
        self.assertEqual(result, [])

    def test_json_array_with_chinese_paths(self):
        result = json.loads(db.parse_list_arg('["src/登录.py", "docs/说明.md"]'))
        self.assertEqual(result, ["src/登录.py", "docs/说明.md"])


class TestParseWorkerDone(unittest.TestCase):
    def test_standard_format_with_hash(self):
        text = "## TASK_COMPLETE\noutcome: succeeded\nsummary: 做了A。"
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["outcome"], "succeeded")
        self.assertIn("做了A", result["summary"])

    def test_omp_rendered_no_hash(self):
        text = "TASK_COMPLETE\noutcome: succeeded\nsummary: Did stuff."
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["outcome"], "succeeded")

    def test_omp_rendered_with_blank_line(self):
        text = "TASK_COMPLETE\n\noutcome: succeeded\nsummary: Did stuff."
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])

    def test_uses_last_completion_report(self):
        text = "## TASK_COMPLETE\noutcome: succeeded\nsummary: first.\n\n## TASK_COMPLETE\noutcome: succeeded\nsummary: second."
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["summary"], "second.")

    def test_missing_task_complete(self):
        text = "Some output without any marker."
        result = db.parse_worker_done(text)
        self.assertFalse(result["parsed"])


class TestRunLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "phalanx.db"
        os.environ["PHALANX_DB"] = str(self.db_path)
        old_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_init_db(SimpleNamespace())
        finally:
            db.out = old_out

    def tearDown(self):
        os.environ.pop("PHALANX_DB", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _capture(self, action, args):
        result = {}
        old_out = db.out
        def capture(data, table=False):
            if isinstance(data, dict):
                result.update(data)
            elif isinstance(data, list):
                result["_list"] = data
        db.out = capture
        try:
            action(args)
        finally:
            db.out = old_out
        return result

    def _create_run(self, **kwargs):
        defaults = dict(
            objective="test run", workspace="w1", coordinator="hermes",
            metadata="{}", workflow_version="legacy-v1", workflow_profile=None,
        )
        defaults.update(kwargs)
        return self._capture(db.cmd_run_create, SimpleNamespace(**defaults))["id"]

    def _create_task(self, run_id, spec="do something", **kwargs):
        defaults = dict(
            run=run_id, spec=spec, deps="[]",
            role="Developer", agent=None, preferred_agent=None,
            execution_mode="managed", acceptance_mode="legacy",
            delivery_mode="direct", max_retries=3,
            checklist_artifact_id=None, coordinator="hermes",
        )
        defaults.update(kwargs)
        return self._capture(db.cmd_task_add, SimpleNamespace(**defaults))["id"]

    def _record_capability(self, kind, profile, roles, agent_name, pane):
        self._capture(db.cmd_capability_record, SimpleNamespace(
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
        conn = db.get_db()
        evidence = json.dumps({"roles": roles, "agent_name": agent_name, "pane_id": pane, "tab_id": "w1:t1"})
        conn.execute(
            "UPDATE capability_observations SET evidence=? WHERE agent_kind=? AND profile IS ?",
            (evidence, kind, profile),
        )
        conn.commit()
        conn.close()

    def _claim_task(self, run_id, task_id, kind, agent_name, pane):
        conn = db.get_db()
        existing = conn.execute(
            "SELECT 1 FROM capability_observations WHERE agent_kind=? AND profile IS ?",
            (kind, "medium"),
        ).fetchone()
        conn.close()
        if not existing:
            self._record_capability(kind, "medium", ["Developer", "QA", "Architect"], agent_name, pane)
        return self._capture(db.cmd_task_claim, SimpleNamespace(
            task=task_id, coordinator="hermes", kind=kind,
            agent_name=agent_name, pane=pane, tab=None, profile="medium",
        ))

    def test_run_create_and_status(self):
        run_id = self._create_run()
        result = self._capture(db.cmd_run_status, SimpleNamespace(run=run_id))
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["total_tasks"], 0)

    def test_run_create_rejects_artifact_workflow_until_enabled(self):
        with self.assertRaisesRegex(ValueError, "force"):
            self._capture(db.cmd_run_create, SimpleNamespace(
                objective="future run", workspace=None, coordinator="hermes-main",
                metadata="{}", workflow_version="artifact-v1", workflow_profile="standard",
            ))

    def test_run_create_rejects_profile_for_legacy_workflow(self):
        with self.assertRaisesRegex(ValueError, "only valid for artifact-v1"):
            self._capture(db.cmd_run_create, SimpleNamespace(
                objective="legacy run", workspace=None, coordinator="hermes-main",
                metadata="{}", workflow_version="legacy-v1", workflow_profile="compact",
            ))

    def test_run_create_with_force_artifact_v1(self):
        run_id = self._create_run(workflow_version="artifact-v1", workflow_profile="standard", force=True)
        result = self._capture(db.cmd_run_status, SimpleNamespace(run=run_id))
        self.assertEqual(result["status"], "active")

    def test_task_add_and_ready(self):
        run_id = self._create_run()
        task_id = self._create_task(run_id)
        ready = self._capture(db.cmd_task_ready, SimpleNamespace(run=run_id, table=False))
        self.assertEqual(len(ready.get("_list", [])), 1)

    def test_task_claim_and_dispatch(self):
        run_id = self._create_run()
        task_id = self._create_task(run_id)
        claim = self._claim_task(run_id, task_id, "omp", "local", "omp-w1")
        self.assertIn("dispatch", claim)
        self.assertEqual(claim["task"]["status"], "dispatched")

    def test_dispatch_complete_from_output(self):
        run_id = self._create_run()
        task_id = self._create_task(run_id)
        claim = self._claim_task(run_id, task_id, "omp", "local", "omp-w1")
        dispatch_id = claim["dispatch"]["id"]
        output = f"## TASK_COMPLETE\ndispatch_id: {dispatch_id}\noutcome: succeeded\nsummary: done."
        result = self._capture(db.cmd_dispatch_complete_from_output, SimpleNamespace(
            dispatch=dispatch_id, coordinator="hermes", text=output, file=None,
        ))
        self.assertIn("status", result)

    def test_run_status_with_tasks(self):
        run_id = self._create_run()
        task_id = self._create_task(run_id, "t1", role="Developer")
        self._claim_task(run_id, task_id, "omp", "local", "omp-w1")
        result = self._capture(db.cmd_run_status, SimpleNamespace(run=run_id))
        self.assertEqual(result["scheduler_state"], "running")

    def test_run_complete(self):
        run_id = self._create_run()
        result = self._capture(db.cmd_run_complete, SimpleNamespace(run=run_id, coordinator="hermes"))
        self.assertEqual(result["status"], "completed")

    def test_run_abort(self):
        run_id = self._create_run()
        result = self._capture(db.cmd_run_abort, SimpleNamespace(run=run_id, coordinator="hermes"))
        self.assertEqual(result["status"], "aborted")


if __name__ == "__main__":
    unittest.main()
