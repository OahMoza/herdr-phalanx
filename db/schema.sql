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
  workflow_version TEXT NOT NULL DEFAULT 'legacy-v1',          -- legacy-v1 | artifact-v1
  workflow_profile TEXT,                                       -- compact | standard | deep (artifact-v1 only)
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
  status           TEXT NOT NULL DEFAULT 'pending',            -- pending | ready | dispatched | candidate | awaiting_acceptance | completed | failed | blocked | skipped
  deps             TEXT NOT NULL DEFAULT '[]',                 -- JSON array: 依赖的 task id 列表
  parent_task_id   TEXT,                                       -- 父 task（扇出-扇入时用）
  parent_run_id    TEXT REFERENCES runs(id),                      -- 父 Run（递归委派）
  delegation_id    TEXT,                                          -- 关联委派记录
  assigned_role    TEXT,                                       -- 期望角色: Developer/QA/Architect/...
  preferred_agent  TEXT,                                       -- 期望 agent kind: omp/claudecode/hermes-coding/...
  execution_mode   TEXT NOT NULL DEFAULT 'managed' CHECK(execution_mode IN ('managed', 'raw-pane')),
  acceptance_mode  TEXT NOT NULL DEFAULT 'legacy' CHECK(acceptance_mode IN ('legacy', 'artifact')),
  delivery_mode    TEXT NOT NULL DEFAULT 'direct' CHECK(delivery_mode IN ('direct', 'bus')),
  checklist_artifact_id TEXT REFERENCES artifacts(id),
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
  agent_session_id TEXT,
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
  artifact_id   TEXT REFERENCES artifacts(id),
  checklist_artifact_id TEXT REFERENCES artifacts(id),
  reviewer_dispatch_id TEXT REFERENCES dispatches(id),
  options       TEXT NOT NULL DEFAULT '["pass","fail","escalate"]',
  resolution    TEXT,                                           -- pass | fail | escalate
  resolved_by   TEXT,                                           -- dispatcher | user | agent
  resolved_at   TEXT,
  evidence      TEXT,                                           -- JSON: {command, output, files}
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_gates_run ON gates(run_id);

-- ------------------------------------------------------------
-- Delegation: 递归 Child Run 的契约、预算和结果
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS delegations (
  id                  TEXT PRIMARY KEY,
  parent_run_id       TEXT NOT NULL REFERENCES runs(id),
  parent_task_id      TEXT REFERENCES tasks(id),
  child_run_id        TEXT NOT NULL UNIQUE REFERENCES runs(id),
  status              TEXT NOT NULL DEFAULT 'reserved'
    CHECK(status IN ('reserved', 'starting', 'active', 'result_submitted', 'accepted', 'rejected', 'cancelling', 'cancelled', 'abandoned')),
  objective           TEXT NOT NULL,
  acceptance_artifact_id TEXT REFERENCES artifacts(id),
  required_capabilities TEXT NOT NULL DEFAULT '[]',
  allowed_scope       TEXT NOT NULL DEFAULT '[]',
  escalation_conditions TEXT NOT NULL DEFAULT '[]',
  max_depth           INTEGER NOT NULL DEFAULT 0 CHECK(max_depth >= 0),
  max_parallelism     INTEGER NOT NULL DEFAULT 1 CHECK(max_parallelism > 0),
  max_intensity       TEXT NOT NULL DEFAULT 'high' CHECK(max_intensity IN ('low', 'medium', 'high')),
  result              TEXT,
  submitted_at        TEXT,
  accepted_gate_id    TEXT REFERENCES gates(id),
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_delegations_parent ON delegations(parent_run_id, status);
CREATE INDEX IF NOT EXISTS idx_delegations_child ON delegations(child_run_id);

CREATE TABLE IF NOT EXISTS topology_allocations (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(id),
  coordinator         TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'reserved'
    CHECK(status IN ('reserved', 'active', 'released', 'reconciliation_required')),
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
  released_at         TEXT,
  metadata            TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS topology_resources (
  allocation_id       TEXT NOT NULL REFERENCES topology_allocations(id),
  resource_type       TEXT NOT NULL CHECK(resource_type IN ('workspace', 'tab', 'pane')),
  resource_id         TEXT NOT NULL,
  slot                INTEGER,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  released_at         TEXT,
  PRIMARY KEY(allocation_id, resource_type, resource_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_topology_active_resource
  ON topology_resources(resource_type, resource_id)
  WHERE released_at IS NULL;

-- ------------------------------------------------------------
-- Coordinator Inbox: 持久通知投递记录
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coordinator_inbox (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id              TEXT NOT NULL REFERENCES runs(id),
  coordinator         TEXT NOT NULL,
  source_event_id     INTEGER REFERENCES events(id),
  urgency             TEXT NOT NULL DEFAULT 'normal'
    CHECK(urgency IN ('normal', 'urgent')),
  kind                TEXT NOT NULL,
  payload             TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  delivered_at        TEXT,
  acknowledged_at     TEXT,
  UNIQUE(coordinator, source_event_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_inbox_unread
  ON coordinator_inbox(coordinator, urgency, id)
  WHERE acknowledged_at IS NULL;

CREATE TABLE IF NOT EXISTS coordinator_inbox_cursors (
  coordinator         TEXT PRIMARY KEY,
  last_seen_id        INTEGER NOT NULL DEFAULT 0,
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- Amendment: 宪法/Checklist 版本变更
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS amendments (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(id),
  proposer            TEXT NOT NULL,
  reason              TEXT NOT NULL,
  impact_set          TEXT NOT NULL DEFAULT '[]',
  status              TEXT NOT NULL DEFAULT 'proposed'
    CHECK(status IN ('proposed', 'under_review', 'accepted', 'rejected', 'superseded', 'quarantined')),
  constitution        INTEGER NOT NULL DEFAULT 0 CHECK(constitution IN (0, 1)),
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at         TEXT,
  resolved_by         TEXT,
  resolution          TEXT
);

CREATE INDEX IF NOT EXISTS idx_amendments_run ON amendments(run_id, status);

-- ------------------------------------------------------------
-- Cancellation: 协作式取消请求与结算
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cancellations (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(id),
  requested_by        TEXT NOT NULL,
  status              TEXT NOT NULL DEFAULT 'requested'
    CHECK(status IN ('requested', 'propagating', 'settled', 'rejected')),
  reason              TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  settled_at          TEXT,
  acknowledgement     TEXT
);

CREATE INDEX IF NOT EXISTS idx_cancellations_run ON cancellations(run_id, status);

-- ------------------------------------------------------------
-- Red Team: 独立质疑发现
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS findings (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(id),
  artifact_id         TEXT REFERENCES artifacts(id),
  severity            TEXT NOT NULL CHECK(severity IN ('critical', 'major', 'minor', 'observation')),
  description         TEXT NOT NULL,
  evidence            TEXT NOT NULL DEFAULT '{}',
  disposition         TEXT CHECK(disposition IN ('accepted_risk', 'remediated', 'reopened', 'waivered')),
  disposition_reason  TEXT,
  disposition_by      TEXT,
  disposition_at      TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_findings_run ON findings(run_id, disposition);

-- ------------------------------------------------------------
-- Formal Evidence Set: 最终交付证据版本
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence_sets (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(id),
  version             INTEGER NOT NULL DEFAULT 1,
  artifact_ids        TEXT NOT NULL DEFAULT '[]',
  frozen_at           TEXT NOT NULL DEFAULT (datetime('now')),
  superseded_by       TEXT REFERENCES evidence_sets(id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_sets_run ON evidence_sets(run_id, version DESC);

CREATE TABLE IF NOT EXISTS run_budgets (
  run_id              TEXT NOT NULL REFERENCES runs(id),
  agent_limit         INTEGER NOT NULL CHECK(agent_limit >= 0),
  coordinator_limit   INTEGER NOT NULL CHECK(coordinator_limit >= 0),
  agent_used          INTEGER NOT NULL DEFAULT 0,
  coordinator_used    INTEGER NOT NULL DEFAULT 0,
  parallelism_limit   INTEGER NOT NULL DEFAULT 4 CHECK(parallelism_limit > 0),
  parallelism_used    INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS delegation_budgets (
  delegation_id       TEXT NOT NULL REFERENCES delegations(id),
  parent_budget_id    TEXT REFERENCES delegation_budgets(delegation_id),
  agent_limit         INTEGER NOT NULL CHECK(agent_limit >= 0),
  coordinator_limit   INTEGER NOT NULL CHECK(coordinator_limit >= 0),
  agent_used          INTEGER NOT NULL DEFAULT 0,
  coordinator_used    INTEGER NOT NULL DEFAULT 0,
  agent_reserved      INTEGER NOT NULL DEFAULT 0,
  coordinator_reserved INTEGER NOT NULL DEFAULT 0,
  parallelism_limit   INTEGER NOT NULL DEFAULT 1 CHECK(parallelism_limit > 0),
  parallelism_used    INTEGER NOT NULL DEFAULT 0,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(delegation_id)
);

-- ------------------------------------------------------------
-- Artifact authority: immutable file bodies + versioned metadata.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifacts (
  id                  TEXT PRIMARY KEY,
  run_id              TEXT NOT NULL REFERENCES runs(id),
  logical_name        TEXT NOT NULL,
  artifact_type       TEXT NOT NULL,
  version             INTEGER NOT NULL CHECK(version > 0),
  storage_key         TEXT NOT NULL UNIQUE,
  sha256              TEXT NOT NULL,
  producer_task_id    TEXT REFERENCES tasks(id),
  producer_dispatch_id TEXT REFERENCES dispatches(id),
  status              TEXT NOT NULL DEFAULT 'candidate'
    CHECK(status IN ('candidate', 'accepted', 'rejected', 'superseded', 'quarantined')),
  idempotency_key     TEXT NOT NULL,
  supersedes_artifact_id TEXT REFERENCES artifacts(id),
  accepted_gate_id    TEXT REFERENCES gates(id),
  accepted_by         TEXT,
  accepted_at         TEXT,
  metadata            TEXT NOT NULL DEFAULT '{}',
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(run_id, logical_name, version),
  UNIQUE(run_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_run_name
  ON artifacts(run_id, logical_name, version DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_producer
  ON artifacts(producer_task_id, status);

CREATE TABLE IF NOT EXISTS task_output_slots (
  task_id             TEXT NOT NULL REFERENCES tasks(id),
  logical_name        TEXT NOT NULL,
  artifact_type       TEXT NOT NULL,
  required            INTEGER NOT NULL DEFAULT 1 CHECK(required IN (0, 1)),
  accepted_artifact_id TEXT REFERENCES artifacts(id),
  PRIMARY KEY(task_id, logical_name)
);

CREATE TABLE IF NOT EXISTS task_artifact_inputs (
  task_id             TEXT NOT NULL REFERENCES tasks(id),
  upstream_task_id    TEXT REFERENCES tasks(id),
  logical_name        TEXT NOT NULL,
  artifact_id         TEXT REFERENCES artifacts(id),
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(task_id, logical_name)
);

-- ------------------------------------------------------------
-- Bridge mapping: optional Task -> Agent Bus projection identity.
-- Kept in the Phalanx schema so normal init-db is sufficient.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bus_message_map (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id        TEXT NOT NULL REFERENCES tasks(id),
  dispatch_id    TEXT REFERENCES dispatches(id),
  message_id     TEXT NOT NULL,
  execution_mode TEXT NOT NULL DEFAULT 'managed'
    CHECK(execution_mode IN ('managed', 'raw-pane')),
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(task_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_bus_message_map_task ON bus_message_map(task_id);
CREATE INDEX IF NOT EXISTS idx_bus_message_map_message ON bus_message_map(message_id);

-- ------------------------------------------------------------
-- CapabilityObservation: Coordinator 收集的本机 Agent 能力证据（仅观察，不派活）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS capability_observations (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_kind      TEXT NOT NULL,
  profile         TEXT,
  capability_scope TEXT NOT NULL DEFAULT 'execution'
    CHECK(capability_scope IN ('execution', 'coordinator')),
  level           TEXT NOT NULL CHECK(level IN ('declared', 'discovered', 'ready', 'verified', 'degraded', 'unknown')),
  command         TEXT,
  command_type    TEXT,
  execution_mode  TEXT NOT NULL DEFAULT 'managed' CHECK(execution_mode IN ('managed', 'raw-pane')),
  executable_path TEXT,
  version         TEXT,
  herdr_version   TEXT,
  integration     TEXT,
  launch_args     TEXT,
  model           TEXT,
  native_parameters TEXT NOT NULL DEFAULT '{}',
  normalized_intensity TEXT CHECK(normalized_intensity IN ('low', 'medium', 'high')),
  permission_mode TEXT,
  config_hash     TEXT,
  shell_path      TEXT,
  fingerprint     TEXT,
  max_age_s       INTEGER NOT NULL DEFAULT 86400 CHECK(max_age_s > 0),
  evidence        TEXT NOT NULL DEFAULT '{}',
  observed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_capabilities_kind_profile
  ON capability_observations(agent_kind, profile, capability_scope, execution_mode, id DESC);

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
  t.acceptance_mode,
  t.delivery_mode,
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
  )
  AND NOT EXISTS (
    SELECT 1
    FROM task_artifact_inputs tai
    WHERE tai.task_id = t.id
      AND tai.artifact_id IS NULL
  )
  AND NOT EXISTS (
    SELECT 1
    FROM task_output_slots tos
    WHERE tos.task_id = t.id
      AND tos.required = 1
      AND tos.accepted_artifact_id IS NULL
      AND EXISTS (
        SELECT 1 FROM task_artifact_inputs tai2
        WHERE tai2.task_id = t.id AND tai2.logical_name = tos.logical_name
      )
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

-- ------------------------------------------------------------
-- View: current_capabilities — 每个 kind/profile 的最新观察
-- ------------------------------------------------------------
DROP VIEW IF EXISTS current_capabilities;
CREATE VIEW current_capabilities AS
SELECT c.*
FROM capability_observations c
WHERE c.id = (
  SELECT latest.id
  FROM capability_observations latest
  WHERE latest.agent_kind = c.agent_kind
    AND latest.profile IS c.profile
    AND latest.capability_scope = c.capability_scope
    AND latest.execution_mode = c.execution_mode
  ORDER BY latest.id DESC
  LIMIT 1
);
