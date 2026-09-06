-- ============================================================
-- Herdr Phalanx — Orchestration State Database Schema
-- Run/Task/Dispatch three-layer model + DAG deps + events
-- SQLite 3.53+ (Python 3.14+ stdlib sqlite3)
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- Run: 一次编排会话（命名空间 + 所有 task/dispatch 的归属）
-- 对应 Orca 的 Run。一个 workspace 可以有多个 Run（不同任务批次）。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
  id            TEXT PRIMARY KEY,                              -- 如 run_20260905_001
  objective     TEXT NOT NULL,                                 -- 本次编排的目标描述
  workspace_id  TEXT,                                          -- 关联的 herdr workspace (wN)
  coordinator   TEXT NOT NULL DEFAULT 'hermes',                -- active Run 的唯一写入者
  status        TEXT NOT NULL DEFAULT 'active',                -- active | completed | failed | aborted
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at  TEXT,
  metadata      TEXT                                            -- JSON: {tab_ids, dispatcher, ...}
);

-- ------------------------------------------------------------
-- Task: 工作项定义（可重试，有 DAG 依赖）
-- 对应 Orca 的 Task。一个 Task 可以被 dispatch 多次（重试）。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
  id               TEXT PRIMARY KEY,                           -- 如 task_001
  run_id           TEXT NOT NULL REFERENCES runs(id),
  spec             TEXT NOT NULL,                              -- 任务描述（派给 agent 的 prompt 主体）
  status           TEXT NOT NULL DEFAULT 'pending',            -- pending | ready | dispatched | completed | failed | blocked | skipped
  deps             TEXT NOT NULL DEFAULT '[]',                 -- JSON array: 依赖的 task id 列表
  parent_task_id   TEXT,                                       -- 父 task（扇出-扇入时用）
  assigned_role    TEXT,                                       -- 期望角色: Developer/QA/Architect/...
  preferred_agent  TEXT,                                       -- 期望 agent kind: omp/claudecode/hermes-coding/...
  result           TEXT,                                       -- JSON: {outcome, summary, files_modified}
  retry_count      INTEGER NOT NULL DEFAULT 0,
  max_retries      INTEGER NOT NULL DEFAULT 3,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_run     ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);

-- ------------------------------------------------------------
-- Dispatch: Task 的一次具体执行尝试（分配给某个 agent/pane）
-- 对应 Orca 的 Dispatch。每次重试产生新的 Dispatch。
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dispatches (
  id              TEXT PRIMARY KEY,                            -- 如 disp_001
  task_id         TEXT NOT NULL REFERENCES tasks(id),
  run_id          TEXT NOT NULL REFERENCES runs(id),
  agent_name      TEXT,                                        -- herdr agent live name (如 dev1)
  agent_kind      TEXT,                                        -- omp | claudecode | hermes-coding | ...
  pane_id         TEXT,                                        -- herdr pane id (如 w1:p3)
  tab_id          TEXT,                                        -- herdr tab id (如 w1:t3)
  workspace_id    TEXT,
  status          TEXT NOT NULL DEFAULT 'running',             -- running | completed | failed | blocked | abandoned
  outcome         TEXT,                                        -- succeeded | failed (status=completed/failed 时)
  files_modified  TEXT NOT NULL DEFAULT '[]',                 -- JSON array: 修改的文件路径
  summary         TEXT,                                        -- 3句话总结: 做了什么/发现了什么/还剩什么
  failure_reason  TEXT,
  started_at      TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at    TEXT,
  metadata        TEXT                                         -- JSON: {model, effort, bypass_flags, ...}
);

CREATE INDEX IF NOT EXISTS idx_dispatches_task    ON dispatches(task_id);
CREATE INDEX IF NOT EXISTS idx_dispatches_run     ON dispatches(run_id);
CREATE INDEX IF NOT EXISTS idx_dispatches_status  ON dispatches(status);

-- ------------------------------------------------------------
-- Event: 事件日志（append-only，审计 + 调试 + WikiSkill Raw Layer 数据源）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT,
  task_id      TEXT,
  dispatch_id  TEXT,
  event_type   TEXT NOT NULL,                                  -- run_created | task_created | task_ready | dispatch_started | worker_done | worker_failed | blocked | escalation | gate_passed | gate_failed | run_completed | ...
  payload      TEXT,                                            -- JSON
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_run    ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_type   ON events(event_type);

-- ------------------------------------------------------------
-- Gate: 决策门记录（设计评审/实现验收/QA验证/集成测试/冒烟验证）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gates (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  task_id       TEXT REFERENCES tasks(id),
  gate_type     TEXT NOT NULL,                                 -- design_review | impl_acceptance | qa_verify | integration_test | smoke_test | custom
  question      TEXT NOT NULL,
  options       TEXT NOT NULL DEFAULT '["pass","fail","escalate"]',
  resolution    TEXT,                                           -- pass | fail | escalate
  resolved_by   TEXT,                                           -- dispatcher | user | agent
  resolved_at   TEXT,
  evidence      TEXT,                                           -- JSON: {command, output, files}
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gates_run ON gates(run_id);

-- ------------------------------------------------------------
-- View: ready_tasks — 自动计算依赖已全部满足的 task
-- 协调器循环用这个 view 决定哪些 task 可以派活
-- ------------------------------------------------------------
DROP VIEW IF EXISTS ready_tasks;
CREATE VIEW ready_tasks AS
SELECT
  t.id,
  t.run_id,
  t.spec,
  t.assigned_role,
  t.preferred_agent,
  t.retry_count,
  t.max_retries,
  json_extract(t.deps, '$') AS deps_array
FROM tasks t
WHERE t.status = 'pending'
  AND NOT EXISTS (
    SELECT 1
    FROM json_each(t.deps) dep
    JOIN tasks dep_task ON dep_task.id = dep.value
    WHERE dep_task.status != 'completed'
  );

-- ------------------------------------------------------------
-- View: run_summary — Run 的汇总统计
-- ------------------------------------------------------------
DROP VIEW IF EXISTS run_summary;
CREATE VIEW run_summary AS
SELECT
  r.id,
  r.objective,
  r.coordinator,
  r.status,
  r.created_at,
  r.completed_at,
  COUNT(t.id)                                          AS total_tasks,
  SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) AS completed_tasks,
  SUM(CASE WHEN t.status = 'failed'    THEN 1 ELSE 0 END) AS failed_tasks,
  SUM(CASE WHEN t.status = 'dispatched' OR t.status = 'running' THEN 1 ELSE 0 END) AS running_tasks,
  SUM(CASE WHEN t.status = 'pending' OR t.status = 'ready' THEN 1 ELSE 0 END) AS pending_tasks
FROM runs r
LEFT JOIN tasks t ON t.run_id = r.id
GROUP BY r.id;
