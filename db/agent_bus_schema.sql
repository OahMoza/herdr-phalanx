-- ============================================================
-- Herdr Phalanx — Agent Bus Schema
-- Independent local N:N queue infrastructure.
-- Multi-writer safe via lease triple and short BEGIN IMMEDIATE.
-- SQLite 3.53+ (Python 3.14+ stdlib sqlite3)
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 1000;

-- ------------------------------------------------------------
-- bus_routes: agent_kind + optional profile 消费通道配置
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bus_routes (
  agent_kind       TEXT NOT NULL,
  profile          TEXT,
  max_in_flight    INTEGER NOT NULL CHECK(max_in_flight > 0),
  default_lease_s  INTEGER NOT NULL CHECK(default_lease_s > 0),
  enabled          INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (agent_kind, profile)
);

-- ------------------------------------------------------------
-- bus_messages: 通信单元，id 即 correlation_id
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bus_messages (
  id                TEXT PRIMARY KEY,
  caller_id         TEXT NOT NULL,
  agent_kind        TEXT NOT NULL,
  profile           TEXT,
  status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending', 'leased', 'succeeded', 'dead', 'cancelled', 'archived')),
  priority          INTEGER NOT NULL DEFAULT 5,
  payload           TEXT NOT NULL,
  result            TEXT,
  worker_id         TEXT,
  lease_id          TEXT,
  lease_until       TEXT,
  attempts          INTEGER NOT NULL DEFAULT 0,
  max_attempts      INTEGER NOT NULL DEFAULT 3,
  cancel_requested  INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
  raw_report_path   TEXT,
  raw_report_sha256 TEXT,
  callback_name     TEXT,
  callback_payload  TEXT,
  result_acked_at   TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_bus_claim
  ON bus_messages(agent_kind, profile, status, priority DESC, created_at ASC);

CREATE INDEX IF NOT EXISTS idx_bus_results
  ON bus_messages(caller_id, status, updated_at);

CREATE INDEX IF NOT EXISTS idx_bus_lease_expiry
  ON bus_messages(status, lease_until);

-- ------------------------------------------------------------
-- bus_events: append-only 审计轨迹 + Producer 增量游标
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bus_events (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id        TEXT,
  event_type        TEXT NOT NULL,
  actor_id          TEXT,
  payload           TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bus_events_message
  ON bus_events(message_id, id);

-- ------------------------------------------------------------
-- bus_workers: 可选可观测注册，不影响 max_in_flight
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bus_workers (
  worker_id         TEXT PRIMARY KEY,
  agent_kind        TEXT NOT NULL,
  profile           TEXT,
  agent_name        TEXT,
  pane_id           TEXT,
  tab_id            TEXT,
  status            TEXT NOT NULL DEFAULT 'ready',
  last_seen_at      TEXT NOT NULL DEFAULT (datetime('now')),
  metadata          TEXT
);

-- ------------------------------------------------------------
-- bus_callbacks: Operator 受控注册表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bus_callbacks (
  name              TEXT PRIMARY KEY,
  kind              TEXT NOT NULL,
  command_template  TEXT NOT NULL,
  enabled           INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------
-- callback_deliveries: 独立于 Message 状态
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS callback_deliveries (
  id                TEXT PRIMARY KEY,
  message_id        TEXT NOT NULL,
  callback_name     TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending', 'delivered', 'failed', 'dead')),
  attempts          INTEGER NOT NULL DEFAULT 0,
  next_attempt_at   TEXT NOT NULL DEFAULT (datetime('now')),
  last_error        TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  delivered_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_callback_deliveries_pending
  ON callback_deliveries(status, next_attempt_at);

-- ------------------------------------------------------------
-- View: bus_route_status — route 容量与积压可观测
-- ------------------------------------------------------------
DROP VIEW IF EXISTS bus_route_status;
CREATE VIEW bus_route_status AS
SELECT
  r.agent_kind,
  r.profile,
  r.max_in_flight,
  r.enabled,
  (SELECT COUNT(*) FROM bus_messages m
     WHERE m.agent_kind = r.agent_kind
       AND (r.profile IS NULL AND m.profile IS NULL OR m.profile = r.profile)
       AND m.status = 'pending')     AS pending_count,
  (SELECT COUNT(*) FROM bus_messages m
     WHERE m.agent_kind = r.agent_kind
       AND (r.profile IS NULL AND m.profile IS NULL OR m.profile = r.profile)
       AND m.status = 'leased')      AS leased_count,
  (SELECT COUNT(*) FROM bus_messages m
     WHERE m.agent_kind = r.agent_kind
       AND (r.profile IS NULL AND m.profile IS NULL OR m.profile = r.profile)
       AND m.status = 'dead')        AS dead_count,
  (SELECT COUNT(*) FROM bus_workers w
     WHERE w.agent_kind = r.agent_kind
       AND (r.profile IS NULL AND w.profile IS NULL OR w.profile = r.profile)
       AND w.last_seen_at >= datetime('now', '-5 minutes')) AS active_workers
FROM bus_routes r;
