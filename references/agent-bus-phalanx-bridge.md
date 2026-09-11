# Phalanx ↔ Agent Bus Bridge

Implements a bidirectional projection between the Phalanx orchestration DB
(runs / tasks / dispatches) and the Agent Bus (messages / routes / workers).

## Module

`db/phalanx_bus_bridge.py` — standalone, no modifications to either Core.

## Data flow

```
Phalanx Coordinator          Bridge                    Agent Bus
 ───────────────          ──────────                  ──────────
     │                        │                           │
     │  task-add              │                           │
     │  (execution_mode)      │                           │
     │───────────────────────>│                           │
     │                        │                           │
     │  dispatch-start        │                           │
     │───────────────────────>│                           │
     │                        │  bridge_task_to_message() │
     │                        │──────────────────────────>│
     │                        │   enqueue()               │
     │                        │<──────────────────────────│
     │                        │   msg_id                  │
     │                        │                           │
     │                        │  INSERT bus_message_map   │
     │                        │  (task_id, dispatch_id,   │
     │                        │   message_id, mode)       │
     │                        │                           │
     │                        │              worker_loop_once()
     │                        │<──────────────────────────│
     │                        │   claim → deliver →       │
     │                        │   complete/fail           │
     │                        │                           │
     │                        │  on_message_complete()    │
     │                        │  (driven by poll or       │
     │                        │   callback)               │
     │                        │                           │
     │  UPDATE dispatches     │                           │
     │  UPDATE tasks          │                           │
     │  INSERT events         │                           │
     │<───────────────────────│                           │
```

## Field mapping

### Task → Message (projection)

| Phalanx `tasks` field      | Bus `bus_messages` payload field              | Notes                                  |
|----------------------------|-----------------------------------------------|----------------------------------------|
| `tasks.spec`               | `payload.instruction`                         | Agent prompt body                      |
| `tasks.id`                 | `payload.bridge.task_id`                      | Correlation                            |
| `tasks.run_id`             | `payload.bridge.run_id`                       | Audit trail                            |
| `tasks.preferred_agent`    | `bus_messages.agent_kind`                     | Route selection                        |
| `tasks.execution_mode`     | `payload.bridge.mode`                         | Envelope shape switch                  |
| `tasks.assigned_role`      | `payload.context.assigned_role`               | Managed only                           |
| *(dispatch_id)*            | `payload.bridge.dispatch_id`                  | Managed only                           |
| *(hardcoded)*              | `bus_messages.caller_id = "phalanx-bridge"`   | Result-pull identity                   |
| *(hardcoded)*              | `bus_messages.max_attempts = 3`               | Override per-call                      |

### Message → Dispatch (promotion)

| Bus `bus_messages` result  | Phalanx `dispatches` field                    |
|----------------------------|-----------------------------------------------|
| `result.outcome`           | `dispatches.outcome`, `dispatches.status`     |
| `result.summary`           | `dispatches.summary`                          |
| `result.files_modified`    | `dispatches.files_modified`                   |
| *(terminal timestamp)*     | `dispatches.completed_at`                     |

### Dispatch status mapping

| Bus outcome     | Dispatch status | Task status (success) | Task status (failure) |
|-----------------|-----------------|-----------------------|-----------------------|
| `succeeded`     | `completed`     | `completed`           | —                     |
| `failed`        | `failed`        | —                     | `pending` or `failed`*|

\* Task stays `pending` when `retry_count < max_retries`; otherwise `failed`.

## Execution-mode envelope differences

### managed

```json
{
  "instruction": "<tasks.spec>",
  "bridge":   { "source": "phalanx", "task_id": "...", "dispatch_id": "...", "run_id": "...", "mode": "managed" },
  "context":  { "assigned_role": "Developer", "preferred_agent": "omp" },
  "constraints": { "require_task_complete": true }
}
```

The Worker is expected to emit a `TASK_COMPLETE` marker so the Bridge can
parse structured results.

### raw-pane

```json
{
  "instruction": "<tasks.spec>",
  "bridge": { "source": "phalanx", "task_id": "...", "run_id": "...", "mode": "raw-pane" }
}
```

No `TASK_COMPLETE` contract; the Bridge treats whatever the agent returns
as the result.

## Conflict handling

### Dispatch already terminal

If `on_message_complete` finds the target Dispatch in a terminal status
(`completed`, `failed`, `blocked`, `abandoned`), it **refuses to overwrite**
and returns `{"updated": false, "reason": "dispatch already terminal"}`.
This prevents a late Bus result from clobbering a state-machine transition
the Coordinator already made.

### Message has no mapping

If the Bus Message id is not found in `bus_message_map`, the Bridge returns
`{"updated": false, "reason": "no bus_message_map entry"}`.  This is a
no-op — the Message was not projected by the Bridge.

## Idempotency

`bridge_task_to_message` checks `bus_message_map` before every enqueue:

1. Look up the most recent mapping row for the Task id.
2. If a mapping exists **and** the corresponding Bus Message is still live
   (status not in `succeeded`, `dead`, `cancelled`), return the existing
   mapping without enqueueing again.
3. If the mapping's Message is terminal (or missing), fall through and
   enqueue a fresh Message.

This guarantees at-most-one live Message per Task, even if the Coordinator
retries the projection after a crash.

## Transaction boundaries

- **Phalanx DB**: the bridge opens its own connection, runs the mapping
  INSERT / Dispatch UPDATE / Task UPDATE / Event INSERT in a single
  transaction, and commits.  The Coordinator's transaction is independent.
- **Agent Bus DB**: the bridge delegates to `AgentBus.enqueue()`, which
  manages its own `BEGIN IMMEDIATE` transaction.
- **No cross-DB transaction**: the two databases are never bound together.
  If the Phalanx commit fails after the Bus enqueue succeeded, the Bus
  Message will simply never be pulled (it remains `pending` until reaped).

## Safety rules

- The bridge is the **only** writer of `bus_message_map`.  The Coordinator
  may read it for traceability but never mutates it.
- The bridge never writes `bus_messages` rows directly — it always goes
  through `AgentBus.enqueue()`.
- The bridge never writes `dispatches` rows directly — it only UPDATEs
  existing rows through the documented status mapping.
- All Bus-side evidence (raw reports, attempt artifacts, callback
  deliveries) stays in the Bus.  The Bridge only promotes the structured
  result into Phalanx.

## Out of scope

- A reverse bridge (a Phalanx Task triggered by a Bus event without a
  Coordinator present).
- Multi-Coordinator arbitration for the same Run.
- Automatic retry of failed Bus Messages (handled by the Bus reaper).
