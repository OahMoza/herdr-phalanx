# Herdr Phalanx Glossary

This file defines the canonical terms for the repository. Use these names in issues, specifications, code, and tests.

## Existing Phalanx terms

- **Phalanx**: the Windows-only local Skill that orchestrates coding Agents inside a single Herdr session on one machine.
- **Coordinator**: the sole state writer for one active Run. A Coordinator may delegate a bounded work package by creating a Child Run owned by another Coordinator.
- **Root Coordinator**: the Coordinator that owns the root Run and carries the Staff Chief responsibility: objective, overall approach, staffing needs, delegation boundaries, constitutional approval, and final acceptance. _Avoid_: separate PM entity.
- **Staff Chief**: the Root Coordinator operating at full intensity for framing and acceptance, then in supervision mode during long-running execution. It intervenes on hard-rule violations, amendments, deadlocks, and sampled evidence rather than scheduling every Worker.
- **Child Coordinator**: a Coordinator that owns a Child Run and may recursively delegate within its inherited authority. It chooses concrete Agents, models, intensity, Tasks, topology, and retries for its Run.
- **Execution Coordinator**: the Coordinator that owns long-running DAG execution under an accepted Constitution Artifact. It advances ready Tasks, staffs Dispatches, handles local retries and Gates, and escalates constitutional changes.
- **Workflow Profile**: the Staff Chief's declared ceremony level: `compact`, `standard`, or `deep`. Every profile retains prior acceptance criteria, explicit dependencies, single-writer Artifacts, completion evidence, and final acceptance.
- **Run**: a single orchestration namespace owned by exactly one Coordinator. All Tasks, Dispatches, Gates, and Events belong to one Run.
- **Child Run**: a Run created from a Parent Task delegation. It has its own Coordinator and never shares write ownership with its Parent Run.
- **Task**: a unit of work in a Run with frozen input Artifact and Checklist references, declared output ownership, retry policy, DAG dependencies, and an expected Role.
- **Dispatch**: one concrete attempt of a Task by a specific Worker in a specific Pane and Tab. Each retry creates a new Dispatch.
- **Delegation**: a Coordinator-to-Coordinator assignment of a bounded work package realised as a Child Run. It follows the Phalanx Artifact/DAG control plane; Agent Bus is optional transport, not part of its meaning. _Avoid_: treating an ordinary Worker prompt as delegation.
- **Delegation Contract**: the objective, acceptance criteria, required capabilities, inherited ceilings, shared remaining budget, allowed scope, and escalation conditions attached to a Delegation. Every Delegation has finite limits; a Child Coordinator may narrow but never expand them.
- **Delegation Result**: the Child Coordinator's structured result, evidence, artifacts, completed and remaining scope, and budget usage submitted to its Parent Coordinator after required Tasks and Gates are terminal and no descendant Delegations remain active. Its outcome may be succeeded, failed, cancelled, or partial. Submission never completes the Parent Task automatically; parent acceptance does.
- **Delegation Budget**: the finite Agent-call and Coordinator-call allowance shared by a delegated subtree. Descendants consume inherited remainder rather than receiving a reset budget; there is no unlimited budget.
- **Coordinator Capability**: separately verified evidence that an Agent/Profile can own a Run, manage Delegations and budgets, and perform acceptance. Ordinary execution capability does not imply it.
- **Resource Profile**: a schedulable description containing both a normalized intensity class and the verified Agent-native model and invocation parameters. Normalized intensity supports planning but does not claim equivalent capability across models.
- **Worker**: an Agent while it is executing a Dispatch without downstream delegation authority. Not a permanent roster row.
- **Agent**: a Herdr Pane process (omp, pi, claude, opencode, hermes --profile ..., etc.).
- **Pane / Tab / Workspace**: Herdr primitives. Workspace is a project; Tab groups Panes; Pane runs one Agent.
- **Role**: the expected responsibility on a Task (Developer, QA, Architect, ...). Not pre-registered; roles are declared per Task.
- **Capability observation**: a persistent record of this machine's current state for an Agent kind / profile. Levels: `declared`, `discovered`, `ready`, `verified`, `degraded`, `unknown`.
- **Execution mode**: per-Task and per-capability, either `managed` (uses `herdr agent start/prompt/read`) or `raw-pane` (explicit stateless Pane commands like `pi -p`). A managed Task can only be claimed by a verified capability of the same execution mode.
- **Delivery mode**: the Constitution's per-Task transport choice: `direct` by default for a named Herdr Worker, or `bus` for an anonymous competing Worker pool. It is independent of execution mode.
- **Event**: an append-only audit row for Run / Task / Dispatch / Capability transitions.
- **Gate**: a decision checkpoint inside a Run (design review, QA verify, smoke verify, ...).
- **TASK_COMPLETE / TASK_ASK**: the structured marker protocol a Phalanx Worker emits at the end of its TUI output. Both must bind the current `dispatch_id`.
- **Phalanx DB**: the SQLite database (`phalanx.db`) that stores Run / Task / Dispatch / Event / Gate / capability observation state. Single-writer per active Run.

## Agent Bus terms

Agent Bus is the independent local N:N communication infrastructure introduced by the PRD published in issue #22. It is **not** part of the Phalanx Run / Task / Dispatch layer; it lives next to it.

- **Agent Bus**: an optional local N:N queue for anonymous Worker pools, competing consumption, reliable asynchronous delivery, and operational recovery. It is outside the primary Artifact/DAG workflow and does not decide decomposition or acceptance. Owns `bus_messages`, `bus_routes`, `bus_events`, `bus_workers`, `bus_callbacks`, `callback_deliveries`; backed by a separate SQLite file (`agent-bus.db`).
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
- **Bus Worker record**: a row in `bus_workers` that captures a TUI Agent's identity and capabilities for Bus consumption. Coordinator Routes and ordinary Worker Routes are distinct; only a Bus Worker with verified Coordinator Capability may claim a Delegation. Records identity, route, launch context, versions, and running statistics.

## Agent Bus implementation layers (ADR 0003)

The Agent Bus is split into three layers; new code should respect the seams.

- **AgentBus Core** (`db/agent_bus_core.py`): the deep module. Hides SQLite, lease state machine, artifact filesystem, callback subprocess, and prompt construction behind a single `AgentBus` class. Portable Python (stdlib only). Knows nothing about Herdr.
- **Herdr Adapter** (`db/herdr_adapter.py`): owns the only place that knows Herdr's `agent prompt` command. Exposes `build_lease_prompt(msg, agent_kind, profile, python_executable, db_module)` (pure function) and `HerdrCommanderRunner` (subprocess wrapper using a fixed argv list with `shell=False`). The Adapter is the only layer that talks to Herdr.
- **Bus CLI adapter** (`db/agent_bus.py`): the thin argparse + JSON I/O shell. Every command delegates to the Core. Same command names and response JSON shape as before the refactor; adds the `worker-loop` subcommand.
- **Command Runner** (`db/callback_runner.py`): a small protocol `(run(args, cwd=None) -> CommandResult)` with two implementations. `SubprocessCommandRunner` uses argv + `shell=False`; `ShellTemplateCommandRunner` uses the legacy `shell=True` template path. The Core picks the runner from the row's shape (`executable` + `arguments_json` vs `command_template`). Tests inject a fake.
- **Lease Prompt**: the restricted envelope the Herdr Adapter sends to a TUI Worker when a Message is leased. Lists exactly four allowed commands (`heartbeat-message`, `complete`, `fail`, `cancelled`) and explicitly forbids `enqueue`, `claim`, `route-set`, `route-delete`, `worker-register`. The Core claims the lease before construction and heartbeats immediately after the Adapter returns.
- **Executable Callback**: the modern callback registration path. Stored as `bus_callbacks.executable` + `bus_callbacks.arguments_json` (JSON array of argv entries). Delivered by `SubprocessCommandRunner`. The `shell=True` template path remains as `ShellTemplateCommandRunner` for backward compatibility.
- **worker-loop**: the new Bus CLI subcommand that wires Core → Herdr Adapter → TUI Worker in a single round trip. Sole integration point between the Bus and Herdr; not a daemon.

## Shell conventions

- **Shell detection**: Skill uses `scripts/detect-shell.ps1` to find the user's shell. Prefers PowerShell 7 (`pwsh`), falls back to PowerShell 5.1. Never hardcoded.
- **OMP shellPath**: `~/.omp/agent/settings.json` `shellPath` should be set to the detected shell path on Windows.

## Cross-layer terms

- **Artifact**: an immutable, versioned work product stored as a file and referenced by Tasks and Gates. Phalanx DB stores identity, version, path, hash, producer, status, and acceptance metadata; accepted content is superseded rather than overwritten.
- **Artifact Owner**: the single Task allowed to write a candidate Artifact. Parallel work produces separate Artifacts that an explicit integration Task may combine. _Avoid_: concurrent edits to a shared output file.
- **Constitution Artifact**: the accepted execution contract containing the Task list, dependency graph, frozen Checklists, staffing capabilities, intensity classes, scope, budget, and amendment rules for long-running work. _Avoid_: mutable planning scratchpad.
- **Checklist**: the versioned acceptance contract frozen before its Task begins. A changed Checklist is a Constitution Amendment and invalidates affected acceptance evidence.
- **Constitution Amendment**: a proposed new Constitution Artifact version required when objective, scope, Checklist, dependency structure, or budget must change. The Staff Chief accepts or rejects it through a Gate and records the change.
- **Artifact/DAG Workflow**: the primary Phalanx execution model in which accepted Artifacts define inputs, Tasks define dependencies, and Gates control promotion. Agent Bus is not required.
- **Requirements Lead**: the specialist Role that turns the original request into a candidate requirements Artifact. It is a Worker unless explicitly granted Delegation authority. _Avoid_: PM-A, Child PM.
- **Independent Reviewer**: a read-only Worker that evaluates a candidate Artifact against its frozen Checklist and records pass, fail, or unknown with evidence. The owning Coordinator decides promotion and disputes.
- **Blocking Question**: a Dispatch-bound request for a decision that cannot be derived from accepted Artifacts or delegated authority. It blocks that Task and its dependent descendants while independent ready Tasks continue.
- **Relay**: a low-authority deterministic messenger that writes a durable Coordinator Inbox, immediately wakes the owner for urgent events, and batches normal events for safe-point review. A lightweight Agent may summarize content, but the Relay never decomposes work, accepts Artifacts, mutates business state, or manages budgets.
- **Coordinator Inbox**: the durable, cursor-based delivery record for Relay notifications. It prevents duplicate delivery without adopting Agent Bus lease and Route semantics.
- **Red Team**: an independent read-only challenger that produces a Findings Artifact against accepted requirements, architecture, Constitution, and execution evidence. Standard workflows use it before final delivery; deep workflows also use it to pressure-test the Constitution. It does not repair the work it evaluates.
- **Formal Evidence Set**: the accepted Artifacts, Gate evidence, resolved Findings, amendment log, and remaining risks used for final delivery. Raw Agent transcripts are diagnostic evidence, not default authoring input.
- **Delivery Artifact**: the authoritative final account of decisions, implementation, verification, and remaining risks.
- **Briefing Artifact**: an audience-oriented explanation derived only from the Formal Evidence Set. It cannot introduce conclusions absent from the Delivery Artifact.
- **Topology Allocation**: an exclusive set of Herdr Workspace, Tab, and Pane resources assigned to one Coordinator. A Coordinator may operate only inside its allocation.
- **Topology Allocator**: the shared infrastructure authority that grants Topology Allocations while preserving global grid and ownership constraints. It is not a user-facing orchestration role.
- **Accepted Dependency**: a Task dependency satisfied only by an accepted Artifact or terminal accepted result. Candidate Artifacts never unlock downstream Tasks; speculative execution is outside the workflow.
- **Coordinator Wake Cycle**: the event-driven unit in which an Execution Coordinator reads new state, dispatches every ready Task allowed by the parallelism budget, then returns to waiting.
- **DAG Deadlock**: a deterministic state with unfinished Tasks but no running Dispatch, pending decision or Gate, declared external wait, or ready Task. Time thresholds detect unresponsive Workers, not DAG deadlock.
- **Cooperative Cancellation**: recursive cancellation that stops new work, propagates to descendants, preserves evidence, and acknowledges settlement. Unresponsive descendants become abandoned for Operator review; cancellation does not imply destructive Herdr resource shutdown.
- **Bridge**: the implemented optional projection between Phalanx Task / Dispatch state and Agent Bus Message / Result state. It is used only when a Task explicitly selects Bus delivery and never promotes a Bus result into parent acceptance.
- **Capability Registry**: the machine-wide set of current Capability observations produced by runtime initialization and shared read-only across Coordinators. Evidence binds the Agent executable and version, model/profile configuration fingerprint, Herdr version, launch arguments, execution mode, and observation time; environment change or maximum age requires refresh.
- **Intensity Class**: the portable scheduling category `low`, `medium`, or `high`, stored alongside the Agent's verified native invocation parameters. It supports planning without claiming equivalent reasoning power across models.
- **Parent Acceptance**: the explicit decision by a Parent Coordinator to accept a Delegation Result against its acceptance criteria. Bus success and Child Run completion never substitute for it.
- **Run Ownership**: the exclusive authority of one Coordinator to mutate a Run and direct its Workers. Ancestors communicate through the owning Coordinator and never operate descendants directly; ownership transfer requires an explicit, evidenced transition.
