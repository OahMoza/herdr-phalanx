# Herdr Phalanx Glossary

This file defines the canonical terms for the repository. Use these names in issues, specifications, code, and tests.

## Existing Phalanx terms

- **Phalanx**: the Windows-only local Skill that orchestrates coding Agents inside a single Herdr session on one machine.
- **Coordinator**: the sole state writer for an active Run. Phalanx supports exactly one Coordinator per active Run at a time.
- **Run**: a single orchestration namespace. All Tasks, Dispatches, Gates, and Events belong to one Run.
- **Task**: a unit of work in a Run; retryable, with DAG dependencies and an expected Role.
- **Dispatch**: one concrete attempt of a Task by a specific Worker in a specific Pane and Tab. Each retry creates a new Dispatch.
- **Worker**: an Agent while it is executing a Dispatch. Not a permanent roster row.
- **Agent**: a Herdr Pane process (omp, pi, claude, opencode, hermes --profile ..., etc.).
- **Pane / Tab / Workspace**: Herdr primitives. Workspace is a project; Tab groups Panes; Pane runs one Agent.
- **Role**: the expected responsibility on a Task (Developer, QA, Architect, ...). Not pre-registered; roles are declared per Task.
- **Capability observation**: a persistent record of this machine's current state for an Agent kind / profile. Levels: `declared`, `discovered`, `ready`, `verified`, `degraded`, `unknown`.
- **Execution mode**: per-Task and per-capability, either `managed` (uses `herdr agent start/prompt/read`) or `raw-pane` (explicit stateless Pane commands like `pi -p`). A managed Task can only be claimed by a verified capability of the same execution mode.
- **Event**: an append-only audit row for Run / Task / Dispatch / Capability transitions.
- **Gate**: a decision checkpoint inside a Run (design review, QA verify, smoke verify, ...).
- **TASK_COMPLETE / TASK_ASK**: the structured marker protocol a Phalanx Worker emits at the end of its TUI output. Both must bind the current `dispatch_id`.
- **Phalanx DB**: the SQLite database (`phalanx.db`) that stores Run / Task / Dispatch / Event / Gate / capability observation state. Single-writer per active Run.

## Agent Bus terms

Agent Bus is the independent local N:N communication infrastructure introduced by the PRD published in issue #22. It is **not** part of the Phalanx Run / Task / Dispatch layer; it lives next to it.

- **Agent Bus**: the local message-bus infrastructure. Owns `bus_messages`, `bus_routes`, `bus_events`, `bus_workers`, `bus_callbacks`, `callback_deliveries`. Backed by a separate SQLite file (`agent-bus.db`).
- **Bus DB**: the SQLite file for Agent Bus, separate from Phalanx DB.
- **Message**: a unit of work in Agent Bus. Has a stable `id` that serves as the `correlation_id`. Retried attempts do not change the id.
- **Producer**: any caller that enqueues Messages via the Bus CLI. Identified by `caller_id`. Pulls only its own results.
- **Route**: `agent_kind + optional profile`. A Route has a hard `max_in_flight` concurrency cap, a default lease duration, and an enabled flag.
- **Bus Worker**: a TUI Agent that consumes Messages through the Bus CLI. It claims, heartbeats, and finalises its own Messages; it never delegates parsing to a separate reader service.
- **Lease**: a temporary exclusive claim on a Message. Identified by `lease_id`. The triple `(id, worker_id, lease_id)` is required for `heartbeat`, `complete`, `fail`, `cancelled`.
- **Attempt**: a single execution attempt of a Message, identified by `attempts` count. Each Attempt produces an immutable raw report artifact under `runs/agent-bus/<message-id>/attempt-<n>/raw.txt`.
- **Result**: the structured payload returned by `complete`. Stored on `bus_messages.result`; not the same as the TUI text.
- **Callback delivery**: an optional, retry-able notification derived from a succeeded Message. Stored in `callback_deliveries`. Failure does **not** revert the Message.
- **DLQ**: `dead` Messages that exceeded `max_attempts`. Must be explicitly requeued by an Operator or Producer to re-enter `pending`.
- **Late result**: a `complete` / `fail` / `cancelled` call arriving after the lease expired and `reap` moved the Message on. Recorded as an Event; never overwrites the active lease's state.
- **Bus CLI**: the Python standard-library SQLite CLI that exposes the Bus surface. Sole path that mutates Message state.
- **Trust boundary**: single-machine local. `worker_id` is an audit identifier, not a cryptographic identity. `agent-bus.db` filesystem permissions are the operational trust boundary.

## Cross-layer terms

- **Bridge**: a future module that projects Phalanx Task creation into Bus Message enqueue, and Bus Message completion back into Phalanx Dispatch / Task state. The bridge is **explicitly out of scope** for the Agent Bus PRD and will be specified in `references/agent-bus-phalanx-bridge.md`.
