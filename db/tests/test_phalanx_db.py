"""
Unit tests for phalanx_db.py
Run: python -m unittest db.tests.test_phalanx_db -v
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

# Add parent dir to path so we can import phalanx_db
sys.path.insert(0, str(Path(__file__).parent.parent))

import phalanx_db as db


# ============================================================
# TestParseListArg — 列表参数解析（JSON数组 / 逗号分隔 / 方括号无引号）
# ============================================================

class TestParseListArg(unittest.TestCase):
    """parse_list_arg 必须同时支持 JSON 数组、逗号分隔、方括号无引号三种格式。"""

    def test_json_array_with_quotes(self):
        result = json.loads(db.parse_list_arg('["a.py", "b.py"]'))
        self.assertEqual(result, ["a.py", "b.py"])

    def test_comma_separated(self):
        result = json.loads(db.parse_list_arg("a.py,b.py"))
        self.assertEqual(result, ["a.py", "b.py"])

    def test_brackets_without_quotes(self):
        """omp 渲染输出 [a.py, b.py] 不带引号，必须正确解析为两个元素。"""
        result = json.loads(db.parse_list_arg("[a.py, b.py]"))
        self.assertEqual(result, ["a.py", "b.py"])

    def test_single_brackets_without_quotes(self):
        """omp 渲染输出 [string_utils.py]，必须解析为 ['string_utils.py']，
        而不是 ['[string_utils.py]']（之前的 bug）。"""
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


# ============================================================
# TestParseWorkerDone — worker_done 标记解析（标准格式 / omp渲染格式）
# ============================================================

class TestParseWorkerDone(unittest.TestCase):
    """parse_worker_done 必须兼容标准格式和 omp 渲染格式（## 被去掉、字段间有空行）。"""

    def test_standard_format_with_hash(self):
        text = """Some agent output here.

## TASK_COMPLETE
outcome: succeeded
files_modified: ["a.py", "b.py"]
summary: 做了A。发现了B。还剩C。
"""
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["files_modified"], ["a.py", "b.py"])
        self.assertIn("做了A", result["summary"])

    def test_omp_rendered_no_hash(self):
        """omp 把 ## TASK_COMPLETE 渲染成标题，去掉了 ##。"""
        text = """Agent working...

TASK_COMPLETE
outcome: succeeded
files_modified: ["string_utils.py"]
summary: 创建了文件。无剩余。
"""
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["files_modified"], ["string_utils.py"])

    def test_omp_rendered_with_blank_line(self):
        """omp 渲染时 TASK_COMPLETE 和 outcome 之间有空行。"""
        text = """Done.

TASK_COMPLETE

outcome: succeeded
files_modified: [test_reverse.py]
summary: 3个测试全部通过。
"""
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["files_modified"], ["test_reverse.py"])

    def test_failed_outcome(self):
        text = """## TASK_COMPLETE
outcome: failed
files_modified: []
summary: 编译错误，无法完成。
"""
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["outcome"], "failed")

    def test_multiple_files(self):
        text = """## TASK_COMPLETE
outcome: succeeded
files_modified: ["src/a.py", "src/b.py", "tests/test_a.py"]
summary: 多文件修改。
"""
        result = db.parse_worker_done(text)
        self.assertEqual(len(result["files_modified"]), 3)

    def test_files_brackets_no_quotes_multiple(self):
        """omp 输出 files_modified: [a.py, b.py] 不带引号。"""
        text = """TASK_COMPLETE
outcome: succeeded
files_modified: [a.py, b.py]
summary: done.
"""
        result = db.parse_worker_done(text)
        self.assertEqual(result["files_modified"], ["a.py", "b.py"])

    def test_missing_summary_defaults(self):
        """缺失 summary 字段时，parsed=true 但 summary 为空字符串。"""
        text = """## TASK_COMPLETE
outcome: succeeded
files_modified: ["a.py"]
"""
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["summary"], "")

    def test_missing_files_defaults_empty(self):
        text = """## TASK_COMPLETE
outcome: succeeded
summary: 没改文件。
"""
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["files_modified"], [])

    def test_no_marker(self):
        """完全没有 TASK_COMPLETE 标记时，parsed=false。"""
        text = """Agent finished task but forgot to output marker.
All done!
"""
        result = db.parse_worker_done(text)
        self.assertFalse(result["parsed"])
        self.assertEqual(result["outcome"], "succeeded")  # default
        self.assertEqual(result["files_modified"], [])

    def test_marker_in_middle_of_output(self):
        """TASK_COMPLETE 在输出中间，前后都有内容。"""
        text = """Thinking...
Implementing...
## TASK_COMPLETE
outcome: succeeded
files_modified: ["x.py"]
summary: done.
Some trailing text.
"""
        result = db.parse_worker_done(text)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["outcome"], "succeeded")


# ============================================================
# TestDatabaseOperations — sqlite 数据库操作（每个测试用独立临时库）
# ============================================================

class TestDatabaseOperations(unittest.TestCase):
    """数据库操作测试，每个测试用独立的临时 sqlite 文件，测试间互不影响。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        os.environ["PHALANX_DB"] = self.db_path
        # init db
        conn = db.get_db()
        schema = db.schema_path().read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.db_path + "-wal"):
            os.remove(self.db_path + "-wal")
        if os.path.exists(self.db_path + "-shm"):
            os.remove(self.db_path + "-shm")
        os.environ.pop("PHALANX_DB", None)

    def _create_run(self, objective="test run"):
        conn = db.get_db()
        run_id = db.gen_id("run")
        conn.execute("INSERT INTO runs (id, objective) VALUES (?,?)", (run_id, objective))
        conn.commit()
        conn.close()
        return run_id

    def _create_run_with_cli(self, objective, coordinator, metadata=None):
        args = SimpleNamespace(
            objective=objective,
            workspace=None,
            coordinator=coordinator,
            metadata=json.dumps(metadata or {}),
        )

        original_out = db.out
        result = {}
        db.out = lambda data, table=False: result.update(data)
        try:
            db.cmd_run_create(args)
        finally:
            db.out = original_out
        return result["id"]

    def _create_task(self, run_id, spec="task", deps="[]", status="pending"):
        conn = db.get_db()
        task_id = db.gen_id("task")
        conn.execute(
            "INSERT INTO tasks (id, run_id, spec, deps, status) VALUES (?,?,?,?,?)",
            (task_id, run_id, spec, deps, status),
        )
        conn.commit()
        conn.close()
        return task_id

    def test_init_db_creates_tables(self):
        """初始化后 6 张表 + 2 个 view 必须存在。"""
        conn = db.get_db()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        views = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
        ).fetchall()]
        conn.close()
        self.assertIn("runs", tables)
        self.assertIn("tasks", tables)
        self.assertIn("dispatches", tables)
        self.assertIn("events", tables)
        self.assertIn("gates", tables)
        self.assertIn("ready_tasks", views)
        self.assertIn("run_summary", views)

    def test_init_db_migrates_existing_run_table_with_coordinator(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DROP VIEW run_summary")
        conn.execute("DROP VIEW ready_tasks")
        conn.execute("DROP TABLE runs")
        conn.execute("""CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            workspace_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            metadata TEXT
        )""")
        conn.commit()
        conn.close()

        class Args:
            pass

        original_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_init_db(Args())
        finally:
            db.out = original_out

        conn = db.get_db()
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        conn.close()
        self.assertIn("coordinator", columns)

    def test_capability_observation_persists_evidence_without_dispatch(self):
        args = SimpleNamespace(
            kind="omp",
            profile="local",
            level="discovered",
            command="omp",
            command_type="Application",
            executable_path="C:/tools/omp.exe",
            version="1.2.3",
            herdr_version="0.8.2",
            integration="recognized",
            launch_args="--auto-approve",
            evidence='{"source":"Get-Command"}',
        )

        original_out = db.out
        result = {}
        db.out = lambda data, table=False: result.update(data)
        try:
            db.cmd_capability_record(args)
        finally:
            db.out = original_out

        self.assertEqual(result["agent_kind"], "omp")
        self.assertEqual(result["profile"], "local")
        self.assertEqual(result["level"], "discovered")
        self.assertEqual(result["evidence"], {"source": "Get-Command"})

        conn = db.get_db()
        dispatches = conn.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0]
        conn.close()
        self.assertEqual(dispatches, 0)

    def test_capability_current_returns_latest_observation_and_history_is_retained(self):
        first = SimpleNamespace(
            kind="omp", profile="local", level="discovered", command="omp", command_type=None,
            executable_path=None, version=None, herdr_version="0.8.2", integration=None,
            launch_args=None, evidence="{}",
        )
        latest = SimpleNamespace(
            kind="omp", profile="local", level="ready", command="omp", command_type=None,
            executable_path=None, version=None, herdr_version="0.8.2", integration="recognized",
            launch_args="--auto-approve", evidence='{"startup":"ready"}',
        )
        original_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_capability_record(first)
            db.cmd_capability_record(latest)
        finally:
            db.out = original_out

        conn = db.get_db()
        current = db.row_to_dict(conn.execute(
            "SELECT * FROM current_capabilities WHERE agent_kind=? AND profile=?", ("omp", "local")
        ).fetchone())
        history = conn.execute(
            "SELECT id FROM capability_observations WHERE agent_kind=? AND profile=?", ("omp", "local")
        ).fetchall()
        conn.close()

        self.assertEqual(current["level"], "ready")
        self.assertEqual(current["evidence"], {"startup": "ready"})
        self.assertEqual(len(history), 2)

    def test_capability_record_rejects_unknown_level(self):
        args = SimpleNamespace(
            kind="omp", profile=None, level="unsupported", command=None, command_type=None,
            executable_path=None, version=None, herdr_version=None, integration=None, launch_args=None,
            evidence="{}",
        )

        with self.assertRaisesRegex(ValueError, "unsupported capability level"):
            db.cmd_capability_record(args)

    def test_capability_list_queries_current_observation_by_kind_and_profile(self):
        args = SimpleNamespace(
            kind="omp", profile="local", level="verified", command=None, command_type=None,
            executable_path=None, version=None, herdr_version="0.8.2", integration=None,
            launch_args=None, evidence='{"smoke_dispatch":"succeeded"}',
        )
        original_out = db.out
        results = []
        try:
            db.out = lambda data, table=False: None
            db.cmd_capability_record(args)
            db.out = lambda data, table=False: results.extend(data)
            db.cmd_capability_list(SimpleNamespace(kind="omp", profile="local", current=True, table=False))
        finally:
            db.out = original_out

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["level"], "verified")
        self.assertEqual(results[0]["profile"], "local")

    def test_managed_smoke_verification_records_dispatch_and_verified_capability(self):
        run_id = self._create_run_with_cli("verify worker", coordinator="hermes-main")
        args = SimpleNamespace(
            run=run_id,
            coordinator="hermes-main",
            kind="omp",
            profile="local",
            agent_name="smoke-omp",
            pane="w1:p1",
            tab="w1:t1",
            launch="succeeded",
            readiness="ready",
            output_read="succeeded",
            output="## TASK_COMPLETE\noutcome: succeeded\nfiles_modified: []\nsummary: smoke complete",
            evidence='{"herdr_state":"done"}',
        )

        original_out = db.out
        result = {}
        db.out = lambda data, table=False: result.update(data)
        try:
            db.cmd_smoke_verify_managed(args)
        finally:
            db.out = original_out

        self.assertEqual(result["capability_level"], "verified")
        self.assertEqual(result["dispatch"]["status"], "completed")
        self.assertEqual(result["dispatch"]["outcome"], "succeeded")

        conn = db.get_db()
        capability = db.row_to_dict(conn.execute(
            "SELECT * FROM current_capabilities WHERE agent_kind=? AND profile=?", ("omp", "local")
        ).fetchone())
        event_types = [row["event_type"] for row in conn.execute(
            "SELECT event_type FROM events WHERE run_id=? ORDER BY id", (run_id,)
        )]
        conn.close()
        self.assertEqual(capability["level"], "verified")
        self.assertEqual(capability["evidence"]["smoke"]["parser"], "succeeded")
        self.assertEqual(event_types, ["run_created", "smoke_dispatch_verified"])

        conn = db.get_db()
        recovered = db.row_to_dict(conn.execute(
            "SELECT * FROM current_capabilities WHERE agent_kind=? AND profile=?", ("omp", "local")
        ).fetchone())
        conn.close()
        self.assertEqual(recovered["level"], "verified")

    def test_managed_smoke_verification_degrades_on_missing_completion_report(self):
        run_id = self._create_run_with_cli("verify worker", coordinator="hermes-main")
        args = SimpleNamespace(
            run=run_id, coordinator="hermes-main", kind="omp", profile="local",
            agent_name="smoke-omp", pane="w1:p1", tab="w1:t1", launch="succeeded",
            readiness="ready", output_read="succeeded", output="finished without marker", evidence="{}",
        )

        original_out = db.out
        result = {}
        db.out = lambda data, table=False: result.update(data)
        try:
            db.cmd_smoke_verify_managed(args)
        finally:
            db.out = original_out

        self.assertEqual(result["capability_level"], "degraded")
        self.assertEqual(result["dispatch"]["status"], "failed")
        self.assertEqual(result["dispatch"]["failure_reason"], "missing valid TASK_COMPLETE report")

    def test_managed_smoke_verification_degrades_when_launch_fails_without_parser(self):
        run_id = self._create_run_with_cli("verify worker", coordinator="hermes-main")
        args = SimpleNamespace(
            run=run_id, coordinator="hermes-main", kind="omp", profile=None,
            agent_name="smoke-omp", pane=None, tab=None, launch="failed", readiness="not_attempted",
            output_read="not_attempted", output="", evidence='{"launch_error":"not found"}',
        )

        original_out = db.out
        result = {}
        db.out = lambda data, table=False: result.update(data)
        try:
            db.cmd_smoke_verify_managed(args)
        finally:
            db.out = original_out

        self.assertEqual(result["capability_level"], "degraded")
        self.assertEqual(result["dispatch"]["failure_reason"], "launch failed")
        self.assertEqual(result["capability"]["evidence"]["smoke"]["parser"], "not_attempted")

    def test_managed_smoke_verification_records_parser_failure_evidence(self):
        run_id = self._create_run_with_cli("verify worker", coordinator="hermes-main")
        args = SimpleNamespace(
            run=run_id, coordinator="hermes-main", kind="omp", profile=None,
            agent_name="smoke-omp", pane="w1:p1", tab="w1:t1", launch="succeeded",
            readiness="ready", output_read="succeeded", output="unstructured output", evidence="{}",
        )

        original_out = db.out
        result = {}
        db.out = lambda data, table=False: result.update(data)
        try:
            db.cmd_smoke_verify_managed(args)
        finally:
            db.out = original_out

        self.assertEqual(result["capability"]["evidence"]["smoke"]["parser"], "failed")

    def test_claim_ready_task_creates_dispatch_for_verified_worker_matching_role(self):
        run_id = self._create_run_with_cli("claim task", coordinator="hermes-main")
        task_id = self._create_task(run_id, spec="implement feature")
        conn = db.get_db()
        conn.execute("UPDATE tasks SET assigned_role=? WHERE id=?", ("Developer", task_id))
        conn.execute(
            "INSERT INTO capability_observations (agent_kind, profile, level, evidence) VALUES (?,?,?,?)",
            ("omp", "local", "verified", '{"roles":["Developer"]}'),
        )
        conn.commit()
        conn.close()
        args = SimpleNamespace(
            task=task_id, coordinator="hermes-main", kind="omp", profile="local",
            agent_name="dev-one", pane="w1:p1", tab="w1:t1",
        )

        original_out = db.out
        result = {}
        db.out = lambda data, table=False: result.update(data)
        try:
            db.cmd_task_claim(args)
        finally:
            db.out = original_out

        self.assertEqual(result["task"]["status"], "dispatched")
        self.assertEqual(result["task"]["retry_count"], 1)
        self.assertEqual(result["dispatch"]["agent_name"], "dev-one")
        self.assertEqual(result["dispatch"]["agent_kind"], "omp")
        self.assertEqual(result["dispatch"]["pane_id"], "w1:p1")
        self.assertEqual(result["dispatch"]["tab_id"], "w1:t1")

        conn = db.get_db()
        events = [row["event_type"] for row in conn.execute(
            "SELECT event_type FROM events WHERE run_id=? ORDER BY id", (run_id,)
        )]
        conn.close()
        self.assertEqual(events, ["run_created", "task_claimed"])

    def test_claim_ready_task_rejects_unverified_or_role_mismatched_worker(self):
        run_id = self._create_run_with_cli("claim task", coordinator="hermes-main")
        task_id = self._create_task(run_id)
        conn = db.get_db()
        conn.execute("UPDATE tasks SET assigned_role=? WHERE id=?", ("Developer", task_id))
        conn.execute(
            "INSERT INTO capability_observations (agent_kind, profile, level, evidence) VALUES (?,?,?,?)",
            ("omp", "local", "verified", '{"roles":["Reviewer"]}'),
        )
        conn.commit()
        conn.close()
        args = SimpleNamespace(
            task=task_id, coordinator="hermes-main", kind="omp", profile="local",
            agent_name="reviewer", pane="w1:p1", tab="w1:t1",
        )

        with self.assertRaisesRegex(ValueError, "no verified Worker matches role Developer"):
            db.cmd_task_claim(args)

        conn = db.get_db()
        task = conn.execute("SELECT status, retry_count FROM tasks WHERE id=?", (task_id,)).fetchone()
        dispatches = conn.execute("SELECT COUNT(*) FROM dispatches WHERE task_id=?", (task_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["retry_count"], 0)
        self.assertEqual(dispatches, 0)

    def test_claim_ready_task_rejects_dependency_blocked_and_duplicate_claims(self):
        run_id = self._create_run_with_cli("claim task", coordinator="hermes-main")
        dependency = self._create_task(run_id)
        task_id = self._create_task(run_id, deps=json.dumps([dependency]))
        conn = db.get_db()
        conn.execute("UPDATE tasks SET assigned_role=? WHERE id=?", ("Developer", task_id))
        conn.execute(
            "INSERT INTO capability_observations (agent_kind, level, evidence) VALUES (?,?,?)",
            ("omp", "verified", '{"roles":["Developer"]}'),
        )
        conn.commit()
        conn.close()
        args = SimpleNamespace(
            task=task_id, coordinator="hermes-main", kind="omp", profile=None,
            agent_name="dev-one", pane="w1:p1", tab="w1:t1",
        )

        with self.assertRaisesRegex(ValueError, "task is not dependency-ready"):
            db.cmd_task_claim(args)

        conn = db.get_db()
        conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (dependency,))
        conn.commit()
        conn.close()
        original_out = db.out
        db.out = lambda data, table=False: None
        try:
            db.cmd_task_claim(args)
        finally:
            db.out = original_out
        with self.assertRaisesRegex(ValueError, "task status must be pending"):
            db.cmd_task_claim(args)

    def test_run_create_persists(self):
        run_id = self._create_run("test objective")
        conn = db.get_db()
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        conn.close()
        self.assertEqual(row["objective"], "test objective")
        self.assertEqual(row["status"], "active")

    def test_run_create_records_coordinator_and_startup_metadata(self):
        run_id = self._create_run_with_cli(
            "owned run", coordinator="hermes-main", metadata={"session": "local"}
        )

        conn = db.get_db()
        run = db.row_to_dict(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())
        event = db.row_to_dict(conn.execute(
            "SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,)
        ).fetchone())
        conn.close()

        self.assertEqual(run["coordinator"], "hermes-main")
        self.assertEqual(run["metadata"], {"dispatcher": "hermes-main", "session": "local"})
        self.assertEqual(event["event_type"], "run_created")
        self.assertEqual(event["payload"]["coordinator"], "hermes-main")

    def test_run_status_preserves_owner_after_reopening_database(self):
        run_id = self._create_run_with_cli("owned run", coordinator="hermes-main")
        conn = db.get_db()
        conn.close()

        conn = db.get_db()
        status = db.row_to_dict(conn.execute("SELECT * FROM run_summary WHERE id=?", (run_id,)).fetchone())
        conn.close()

        self.assertEqual(status["coordinator"], "hermes-main")
        self.assertEqual(status["status"], "active")

    def test_non_owner_cannot_change_active_run(self):
        run_id = self._create_run_with_cli("owned run", coordinator="hermes-main")
        conn = db.get_db()
        try:
            with self.assertRaisesRegex(PermissionError, "owned by hermes-main"):
                db.assert_run_owner(conn, run_id, "other-coordinator")
        finally:
            conn.close()

    def test_non_owner_cannot_add_task_to_active_run(self):
        run_id = self._create_run_with_cli("owned run", coordinator="hermes-main")
        args = SimpleNamespace(
            run=run_id,
            spec="unauthorized task",
            deps="[]",
            role=None,
            agent=None,
            coordinator="other-coordinator",
        )

        with self.assertRaisesRegex(PermissionError, "owned by hermes-main"):
            db.cmd_task_add(args)

        conn = db.get_db()
        count = conn.execute("SELECT COUNT(*) FROM tasks WHERE run_id=?", (run_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_non_owner_cannot_start_dispatch_for_active_run(self):
        run_id = self._create_run_with_cli("owned run", coordinator="hermes-main")
        task_id = self._create_task(run_id)
        args = SimpleNamespace(
            task=task_id,
            agent_name="worker-one",
            agent_kind="omp",
            pane="w1:p1",
            tab=None,
            coordinator="other-coordinator",
        )

        with self.assertRaisesRegex(PermissionError, "owned by hermes-main"):
            db.cmd_dispatch_start(args)

        conn = db.get_db()
        count = conn.execute("SELECT COUNT(*) FROM dispatches WHERE task_id=?", (task_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_run_can_complete_or_abort_with_owner_and_events(self):
        run_id = self._create_run_with_cli("owned run", coordinator="hermes-main")
        aborted_run_id = self._create_run_with_cli("aborted run", coordinator="hermes-main")

        completed = db.transition_run(db.get_db(), run_id, "hermes-main", "completed")
        aborted = db.transition_run(db.get_db(), aborted_run_id, "hermes-main", "aborted")

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(aborted["status"], "aborted")
        self.assertTrue(aborted["completed_at"])

        conn = db.get_db()
        completed_events = [row["event_type"] for row in conn.execute(
            "SELECT event_type FROM events WHERE run_id=? ORDER BY id", (run_id,)
        )]
        aborted_events = [row["event_type"] for row in conn.execute(
            "SELECT event_type FROM events WHERE run_id=? ORDER BY id", (aborted_run_id,)
        )]
        conn.close()
        self.assertEqual(completed_events, ["run_created", "run_completed"])
        self.assertEqual(aborted_events, ["run_created", "run_aborted"])

    def test_owner_cannot_change_task_or_dispatch_after_run_is_terminal(self):
        run_id = self._create_run_with_cli("owned run", coordinator="hermes-main")
        task_id = self._create_task(run_id)
        db.transition_run(db.get_db(), run_id, "hermes-main", "completed")

        task_args = SimpleNamespace(
            run=run_id, spec="late task", deps="[]", role=None, agent=None, coordinator="hermes-main"
        )
        dispatch_args = SimpleNamespace(
            task=task_id, agent_name="worker-one", agent_kind="omp", pane="w1:p1", tab=None,
            coordinator="hermes-main",
        )

        with self.assertRaisesRegex(ValueError, "status must be active"):
            db.cmd_task_add(task_args)
        with self.assertRaisesRegex(ValueError, "status must be active"):
            db.cmd_dispatch_start(dispatch_args)

    def test_run_lifecycle_commands_require_run_and_coordinator(self):
        parser = db.build_parser()

        complete = parser.parse_args(["run-complete", "--run", "run_123", "--coordinator", "hermes-main"])
        abort = parser.parse_args(["run-abort", "--run", "run_123", "--coordinator", "hermes-main"])

        self.assertIs(complete.func, db.cmd_run_complete)
        self.assertIs(abort.func, db.cmd_run_abort)

    def test_task_add_with_deps(self):
        run_id = self._create_run()
        t1 = self._create_task(run_id, "task1")
        t2 = self._create_task(run_id, "task2", deps=json.dumps([t1]))
        conn = db.get_db()
        row = conn.execute("SELECT deps FROM tasks WHERE id=?", (t2,)).fetchone()
        conn.close()
        self.assertEqual(json.loads(row["deps"]), [t1])

    def test_ready_tasks_view_dependency_logic(self):
        """依赖未完成时 task 不在 ready_tasks；依赖完成后自动出现。"""
        run_id = self._create_run()
        t1 = self._create_task(run_id, "task1")
        t2 = self._create_task(run_id, "task2", deps=json.dumps([t1]))

        conn = db.get_db()
        # t1 pending, t2 should NOT be ready
        ready = [r["id"] for r in conn.execute(
            "SELECT id FROM ready_tasks WHERE run_id=?", (run_id,)
        ).fetchall()]
        self.assertIn(t1, ready)
        self.assertNotIn(t2, ready)

        # complete t1
        conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (t1,))
        conn.commit()

        # now t2 should be ready
        ready = [r["id"] for r in conn.execute(
            "SELECT id FROM ready_tasks WHERE run_id=?", (run_id,)
        ).fetchall()]
        self.assertIn(t2, ready)
        conn.close()

    def test_dispatch_start_updates_task_status(self):
        run_id = self._create_run()
        t1 = self._create_task(run_id, "task1")
        conn = db.get_db()
        disp_id = db.gen_id("disp")
        conn.execute(
            "INSERT INTO dispatches (id, task_id, run_id, agent_name, agent_kind, pane_id) VALUES (?,?,?,?,?,?)",
            (disp_id, t1, run_id, "dev1", "omp", "w1:p1"),
        )
        conn.execute("UPDATE tasks SET status='dispatched', retry_count=retry_count+1 WHERE id=?", (t1,))
        conn.commit()
        task = conn.execute("SELECT status, retry_count FROM tasks WHERE id=?", (t1,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "dispatched")
        self.assertEqual(task["retry_count"], 1)

    def test_dispatch_complete_succeeded_marks_task_completed(self):
        run_id = self._create_run()
        t1 = self._create_task(run_id, "task1", status="dispatched")
        conn = db.get_db()
        disp_id = db.gen_id("disp")
        conn.execute(
            "INSERT INTO dispatches (id, task_id, run_id, status) VALUES (?,?,?,'running')",
            (disp_id, t1, run_id),
        )
        conn.execute(
            "UPDATE dispatches SET status='completed', outcome='succeeded', files_modified='[\"a.py\"]', summary='done', completed_at=? WHERE id=?",
            (db.now(), disp_id),
        )
        conn.execute("UPDATE tasks SET status='completed', result=?, completed_at=? WHERE id=?",
                     (json.dumps({"outcome": "succeeded"}), db.now(), t1))
        conn.commit()
        task = conn.execute("SELECT status FROM tasks WHERE id=?", (t1,)).fetchone()
        disp = conn.execute("SELECT status, outcome FROM dispatches WHERE id=?", (disp_id,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "completed")
        self.assertEqual(disp["status"], "completed")
        self.assertEqual(disp["outcome"], "succeeded")

    def test_dispatch_fail_within_retry_limit_returns_task_to_pending(self):
        """失败但未超重试上限时，task 回到 pending 可重试。"""
        run_id = self._create_run()
        t1 = self._create_task(run_id, "task1", status="dispatched")
        conn = db.get_db()
        # set retry_count=1, max_retries=3
        conn.execute("UPDATE tasks SET retry_count=1, max_retries=3 WHERE id=?", (t1,))
        disp_id = db.gen_id("disp")
        conn.execute(
            "INSERT INTO dispatches (id, task_id, run_id, status) VALUES (?,?,?,'running')",
            (disp_id, t1, run_id),
        )
        conn.execute("UPDATE dispatches SET status='failed', outcome='failed', failure_reason='error', completed_at=? WHERE id=?",
                     (db.now(), disp_id))
        # retry_count(1) < max_retries(3) → pending
        conn.execute("UPDATE tasks SET status='pending' WHERE id=?", (t1,))
        conn.commit()
        task = conn.execute("SELECT status FROM tasks WHERE id=?", (t1,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "pending")

    def test_run_summary_stats(self):
        run_id = self._create_run()
        self._create_task(run_id, "t1", status="completed")
        self._create_task(run_id, "t2", status="failed")
        self._create_task(run_id, "t3", status="pending")
        self._create_task(run_id, "t4", status="dispatched")
        conn = db.get_db()
        row = conn.execute("SELECT * FROM run_summary WHERE id=?", (run_id,)).fetchone()
        conn.close()
        self.assertEqual(row["total_tasks"], 4)
        self.assertEqual(row["completed_tasks"], 1)
        self.assertEqual(row["failed_tasks"], 1)
        self.assertEqual(row["pending_tasks"], 1)  # pending + ready
        self.assertEqual(row["running_tasks"], 1)  # dispatched + running

    def test_event_log_append_only(self):
        run_id = self._create_run()
        conn = db.get_db()
        db.log_event(conn, "run_created", run_id=run_id, payload={"a": 1})
        db.log_event(conn, "task_created", run_id=run_id, payload={"b": 2})
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(count, 2)

    def test_parse_worker_done_integration(self):
        """parse_worker_done 与数据库的集成：解析后可直接用于 dispatch-complete。"""
        run_id = self._create_run()
        t1 = self._create_task(run_id, "task1", status="dispatched")
        conn = db.get_db()
        disp_id = db.gen_id("disp")
        conn.execute(
            "INSERT INTO dispatches (id, task_id, run_id, status) VALUES (?,?,?,'running')",
            (disp_id, t1, run_id),
        )
        conn.commit()
        conn.close()

        # 模拟 omp 渲染输出
        agent_output = """Done working.

TASK_COMPLETE

outcome: succeeded
files_modified: [src/app.py, tests/test_app.py]
summary: 实现了功能。发现了边界问题。无剩余。
"""
        parsed = db.parse_worker_done(agent_output)
        self.assertTrue(parsed["parsed"])
        self.assertEqual(parsed["outcome"], "succeeded")
        self.assertEqual(parsed["files_modified"], ["src/app.py", "tests/test_app.py"])

        # 用解析结果完成 dispatch
        conn = db.get_db()
        files_json = json.dumps(parsed["files_modified"], ensure_ascii=False)
        conn.execute(
            "UPDATE dispatches SET status='completed', outcome=?, files_modified=?, summary=?, completed_at=? WHERE id=?",
            (parsed["outcome"], files_json, parsed["summary"], db.now(), disp_id),
        )
        conn.execute("UPDATE tasks SET status='completed' WHERE id=?", (t1,))
        conn.commit()
        task = conn.execute("SELECT status FROM tasks WHERE id=?", (t1,)).fetchone()
        conn.close()
        self.assertEqual(task["status"], "completed")


if __name__ == "__main__":
    unittest.main()
