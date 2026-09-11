# Agent Bus Protocol

This document is the single source of truth for the Agent Bus CLI surface,
the Message state machine, and the lease contract. The implementation is
split across three layers (see ADR 0003):

| Layer       | File                       | Responsibility                                 |
| ----------- | -------------------------- | ---------------------------------------------- |
| Core        | `db/agent_bus_core.py`     | lease state machine, persistence, artifacts    |
| Herdr Adapter | `db/herdr_adapter.py`    | lease prompt template, Herdr TUI runner         |
| CLI adapter | `db/agent_bus.py`          | argparse, JSON I/O, error/exit mapping         |

The schema lives in `db/agent_bus_schema.sql`. The Core owns all
business logic; the CLI adapter is a thin shell over it.

## Concepts

- **Agent Bus**: local N:N queue infrastructure for coding Agents. Independent
  of Phalanx Run / Task / Dispatch.
- **Message**: a unit of work. `id` is the stable `correlation_id`. Retries
  do not change the id.
- **Producer**: any caller that enqueues Messages, identified by `caller_id`.
- **Route**: `agent_kind + optional profile`. Has hard `max_in_flight` and a
  default lease duration.
- **Bus Worker**: a TUI Agent that competes for Messages on a Route.
- **Lease**: temporary exclusive claim. Identified by `lease_id`.
- **Attempt**: a single execution attempt. Each Attempt has an immutable
  raw report artifact under `runs/agent-bus/<message-id>/attempt-<n>/raw.txt`.

## Trust boundary

The bus is single-machine local. `worker_id` is an audit identifier, not a
cryptographic identity. The triple `(id, worker_id, lease_id)` is required
for all lease-bound operations and prevents stale leases from mutating
Message state.

## Storage

- Bus DB: `~/.herdr-phalanx/agent-bus.db` (override via `AGENT_BUS_DB`).
- Artifacts: `~/.herdr-phalanx/runs/agent-bus/` (override via
  `AGENT_BUS_ARTIFACTS`).
- SQLite is configured with WAL and `busy_timeout`. Writer transactions use
  `BEGIN IMMEDIATE` and a bounded retry on `database is locked` with
  exponential backoff (50/100/200/400/800 ms, up to 5 attempts).

## Payload envelope

```json
{
  "instruction": "string (required, non-empty)",
  "context":      { "object" },
  "workspace":    "absolute Windows path (optional)",
  "files":        [ "string" ],
  "constraints":  { "object" },
  "metadata":     { "object" }
}
```

`instruction` is the only required field. `workspace`, if present, must be
an existing Windows local absolute path. The Bus does not switch directories,
create workspaces, or interpret `constraints`.

## Message state machine

```
pending
  ├─► leased ─► succeeded          (complete)
  │      │   └─► archived          (result-ack)
  │      ├─► cancelled             (cancelled, only when cancel_requested=1)
  │      ├─► pending               (reap when lease expired and attempts < max_attempts)
  │      └─► dead                 (fail when attempts >= max_attempts, or reap when lease expired and attempts == max_attempts)
  ├─► cancelled                     (cancel --caller --id on pending)
  └─► dead                         (only via fail during lease, or reap when expired lease is already at max_attempts)
```

`archived` is reached from `succeeded` or `cancelled` via `result-ack`.
`result_acked_at` is the marker; archived Messages do not appear in the
default `result-list`.

## Commands

### Init and configuration

```powershell
python db/agent_bus.py init-db

python db/agent_bus.py route-set `
  --agent-kind omp `
  --max-in-flight 4 `
  --default-lease-seconds 300

python db/agent_bus.py route-set `
  --agent-kind hermes `
  --profile testing `
  --max-in-flight 2 `
  --default-lease-seconds 300

python db/agent_bus.py route-status
python db/agent_bus.py route-delete --agent-kind omp
```

### Worker registration

Optional and observable only; does not affect `max_in_flight`.

```powershell
python db/agent_bus.py worker-register `
  --worker-id omp-worker-1 `
  --agent-kind omp `
  --agent-name omp-dev-1 `
  --pane w1:p3 `
  --tab w1:t2

python db/agent_bus.py worker-heartbeat --worker-id omp-worker-1
python db/agent_bus.py worker-list
```

### Producer

```powershell
python db/agent_bus.py enqueue `
  --caller main-coordinator `
  --agent-kind omp `
  --priority 8 `
  --payload-file .\message.json `
  --callback-name notify-local `
  --callback-payload '{"channel":"main"}'

python db/agent_bus.py result-list `
  --caller main-coordinator `
  --after-event 42

python db/agent_bus.py result-get `
  --caller main-coordinator `
  --id msg_abc123

python db/agent_bus.py result-ack `
  --caller main-coordinator `
  --id msg_abc123

python db/agent_bus.py result-history `
  --caller main-coordinator `
  --id msg_abc123

python db/agent_bus.py cancel `
  --caller main-coordinator `
  --id msg_abc123

python db/agent_bus.py requeue `
  --caller main-coordinator `
  --id msg_abc123
```

All result-pulling commands require `--caller`. The caller must own the
Message id; other callers cannot see it.

### Worker

```powershell
python db/agent_bus.py claim `
  --worker-id omp-worker-1 `
  --agent-kind omp `
  --lease-seconds 300

python db/agent_bus.py heartbeat `
  --id msg_abc123 `
  --worker-id omp-worker-1 `
  --lease lease_xyz

python db/agent_bus.py complete `
  --id msg_abc123 `
  --worker-id omp-worker-1 `
  --lease lease_xyz `
  --result-file .\result.json `
  --raw-report-file .\worker-report.txt

python db/agent_bus.py fail `
  --id msg_abc123 `
  --worker-id omp-worker-1 `
  --lease lease_xyz `
  --reason "tests failed" `
  --raw-report-file .\worker-report.txt

python db/agent_bus.py cancelled `
  --id msg_abc123 `
  --worker-id omp-worker-1 `
  --lease lease_xyz `
  --reason "producer cancelled during test execution" `
  --raw-report-file .\worker-report.txt
```

`complete`, `fail`, and `cancelled` all require `--raw-report-file`. The CLI
copies the raw report into `runs/agent-bus/<message-id>/attempt-<n>/raw.txt`,
computes SHA-256, and persists it.

### Operator

```powershell
python db/agent_bus.py reap --once
python db/agent_bus.py reap --drain

python db/agent_bus.py callback-register `
  --name notify-local `
  --kind command `
  --command-template 'python E:\WorkSpace\github\herdr-phalanx\notify.py --message-id {message_id}'

python db/agent_bus.py callback-status
python db/agent_bus.py callback-deliver --once
python db/agent_bus.py callback-deliver --drain
```

`reap --once` returns the first reaped Message and stops; `--drain` keeps
reaping until no expired leases remain. `callback-register` and
`callback-deliver` are Operator-only commands.

## Claim semantics

```text
BEGIN IMMEDIATE;
active := SELECT COUNT(*) FROM bus_messages
          WHERE agent_kind = :kind AND profile IS :profile
            AND status = 'leased' AND lease_until > :now;
IF active >= max_in_flight THEN return NULL;
candidate := SELECT id FROM bus_messages
             WHERE agent_kind = :kind AND profile IS :profile
               AND status = 'pending'
               AND cancel_requested = 0
             ORDER BY priority DESC, created_at ASC
             LIMIT 1;
IF NOT candidate THEN return NULL;
lease_id := <random 32-char token>;
UPDATE bus_messages SET status='leased', worker_id=:worker_id,
       lease_id=:lease_id, lease_until=:lease_until,
       attempts=attempts+1
   WHERE id = candidate AND status = 'pending';
RETURN row;
COMMIT;
```

`heartbeat`, `complete`, `fail`, and `cancelled` all require:

```text
status = 'leased'
worker_id = ?
lease_id = ?
```

Late completions (after `reap`) do not overwrite the active lease. They are
recorded as `late_result` Events and the raw report artifact is preserved.

## Result routing

Results are routed by `caller_id`:

- `result-list`, `result-get`, `result-ack`, `result-history` always require
  `--caller`.
- The cursor for `result-list` is `bus_events.id`, which is append-only and
  monotonic. Producers persist their `next_cursor` and resume after a crash.
- `result-ack` sets `result_acked_at`; archived Messages are excluded from
  the default pull but remain queryable via `result-history`.

## Callback delivery

A callback is identified by `callback_name` only; the invocation lives in
`bus_callbacks` and is Operator-managed. Two registration shapes:

| Shape           | CLI flags                                | Runner                       |
| --------------- | ---------------------------------------- | ---------------------------- |
| Executable      | `--executable PATH --arguments '[]'`     | `SubprocessCommandRunner` (`shell=False`) |
| Legacy template | `--kind command --command-template "..."`| `ShellTemplateCommandRunner` (`shell=True`) |

A successful `complete` with a registered `callback_name` (and a
non-null `executable` or `command_template`) creates a
`callback_deliveries` row in `pending`.

`callback-deliver --once` (or `--drain`) walks the pending queue. The Core
chooses the runner from the row shape and the callbacks never enter the
OS shell as a single command string in the executable path. Stdout and
stderr land in `runs/agent-bus/<message-id>/callback-<delivery-id>/`.

Backoff is exponential (5 / 15 / 60 / 300 s). After exhausting retries
the delivery moves to `dead`. Callback failure never reverts the
Message — Producers always retain pull-based access.

## Herdr Adapter and the worker-loop subcommand

The CLI's `worker-loop` subcommand is the single integration point
between the Core and the Herdr TUI Worker:

```text
python db/agent_bus.py worker-loop \
    --worker-id   <id> \
    --agent-kind  <kind> \
    --profile     <profile>  # optional
    --agent-name  <herdr agent name> \
    --lease-seconds 300 \
    [--herdr-bin PATH]
```

Herdr routes by Agent name, so `--pane-id` and `--tab-id` are no
longer required.

1. The Core claims the next pending Message for the route
   (`agent_kind + profile`) inside one `BEGIN IMMEDIATE` transaction.
2. The Herdr Adapter (`db/herdr_adapter.py`) builds the restricted
   lease prompt. The prompt header lists exactly four allowed commands
   (`heartbeat-message`, `complete`, `fail`, `cancelled`) and explicitly
   forbids `enqueue`, `claim`, `route-set`, `route-delete`,
   `worker-register`.
3. The Herdr Adapter calls `HerdrCommanderRunner.run_agent_prompt`,
   which is the only place that shells out to Herdr. It uses
   `subprocess.run([herdr_bin, "agent", "prompt", agent_name, prompt, --timeout, MS], shell=False)` —
   positional `<TARGET> <TEXT>` plus `--timeout`, matching the current
   `herdr agent prompt --help` shape.
4. On success the Core immediately heartbeats the lease so the prompt
   round-trip lag does not eat into the lease window.

Failure of the runner leaves the Message in `leased`; the Reaper will
reclaim it when `lease_until` expires. The new `agent_bus_supervisor.ps1`
and `worker_loop.ps1` PowerShell templates are thin shims over
`worker-loop` and no longer assemble prompts inline.

## Worker registry

`bus_workers` carries the identity and capabilities of a TUI Agent that
consumes Bus messages. Each row is a **Worker record**:

| Column | Purpose |
|---|---|
| `worker_id` | Unique audit identifier (e.g. `omp-w1`) |
| `agent_kind` | `omp`, `pi`, `claude`, ... |
| `profile` | Optional profile |
| `agent_name` | Herdr live name (e.g. `omp-dev-1`) |
| `workspace_id` | Herdr workspace the worker lives in |
| `pane_id` / `tab_id` | Topology position |
| `session_id` | Herdr agent session ID |
| `roles` | JSON array of capabilities (`["Developer", "QA"]`) |
| `launch_args` | Arguments the agent was started with |
| `permission_mode` | `bypassPermissions`, `auto-approve`, ... |
| `cwd` | Working directory |
| `registered_by` | `operator`, `supervisor`, `auto` |
| `agent_version` | Agent binary version |
| `herdr_version` | Herdr version at registration |
| `messages_claimed` / `messages_completed` / `messages_failed` | Running statistics |

`worker-register` is idempotent: re-registering an existing `worker_id`
updates all fields. `heartbeat-worker` refreshes `last_seen_at`.
`route-status` uses `last_seen_at >= now() - 5min` to count
`active_workers` per Route.

Legacy databases with the original 9-column `bus_workers` are migrated
forward by `init-db` (ALTER TABLE ADD COLUMN for each missing column).
Existing rows keep their data; new columns default to NULL/0.

## TUI markers

Bus Workers continue to emit the structured `TASK_COMPLETE` / `TASK_ASK`
markers (see `templates/agent_bus_worker_preamble.md`) for human and
future-bridge readability. These markers carry `correlation_id`. They are
advisory; the Agent Bus ACK is the CLI call, not the TUI text.

## Worker loop and supervisor

- `templates/worker_loop.ps1` is the single round: claim one Message and
  forward it to a waiting TUI Worker via `herdr agent prompt`, then exit.
- `templates/agent_bus_supervisor.ps1` runs `reap -> route-status ->
  worker_loop.ps1` for a named Worker and exits. There is no daemon.
- The Agent Bus does not poll. Every action is initiated by an explicit
  invocation of the CLI or one of the two scripts.
