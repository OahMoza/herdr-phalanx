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
- **Bus Worker record**: a row in `bus_workers` that captures a TUI Agent's identity and capabilities for Bus consumption. Records `worker_id`, `agent_kind`, `profile`, `agent_name`, `workspace_id`, `pane_id`, `tab_id`, `session_id`, `roles`, `launch_args`, `permission_mode`, `cwd`, `registered_by`, `agent_version`, `herdr_version`, and running statistics (`messages_claimed`, `messages_completed`, `messages_failed`). Extended in v0.7.4 to carry role, session, launch context, and version metadata for audit and debugging.

## Agent Bus implementation layers (ADR 0003)

The Agent Bus is split into three layers; new code should respect the seams.

- **AgentBus Core** (`db/agent_bus_core.py`): the deep module. Hides SQLite, lease state machine, artifact filesystem, callback subprocess, and prompt construction behind a single `AgentBus` class. Portable Python (stdlib only). Knows nothing about Herdr.
- **Herdr Adapter** (`db/herdr_adapter.py`): owns the only place that knows Herdr's `agent prompt` command. Exposes `build_lease_prompt(msg, agent_kind, profile, python_executable, db_module)` (pure function) and `HerdrCommanderRunner` (subprocess wrapper using a fixed argv list with `shell=False`). The Adapter is the only layer that talks to Herdr.
- **Bus CLI adapter** (`db/agent_bus.py`): the thin argparse + JSON I/O shell. Every command delegates to the Core. Same command names and response JSON shape as before the refactor; adds the `worker-loop` subcommand.
- **Command Runner** (`db/callback_runner.py`): a small protocol `(run(args, cwd=None) -> CommandResult)` with two implementations. `SubprocessCommandRunner` uses argv + `shell=False`; `ShellTemplateCommandRunner` uses the legacy `shell=True` template path. The Core picks the runner from the row's shape (`executable` + `arguments_json` vs `command_template`). Tests inject a fake.
- **Lease Prompt**: the restricted envelope the Herdr Adapter sends to a TUI Worker when a Message is leased. Lists exactly four allowed commands (`heartbeat-message`, `complete`, `fail`, `cancelled`) and explicitly forbids `enqueue`, `claim`, `route-set`, `route-delete`, `worker-register`. The Core claims the lease before construction and heartbeats immediately after the Adapter returns.
- **Executable Callback**: the modern callback registration path. Stored as `bus_callbacks.executable` + `bus_callbacks.arguments_json` (JSON array of argv entries). Delivered by `SubprocessCommandRunner`. The `shell=True` template path remains as `ShellTemplateCommandRunner` for backward compatibility.
- **worker-loop**: the new Bus CLI subcommand that wires Core → Herdr Adapter → TUI Worker in a single round trip. Sole integration point between the Bus and Herdr; not a daemon.

## Cross-layer terms

- **Bridge**: a future module that projects Phalanx Task creation into Bus Message enqueue, and Bus Message completion back into Phalanx Dispatch / Task state. The bridge is **explicitly out of scope** for the Agent Bus PRD and will be specified in `references/agent-bus-phalanx-bridge.md`.
