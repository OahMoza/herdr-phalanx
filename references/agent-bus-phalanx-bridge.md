# Phalanx ↔ Agent Bus Bridge (design only)

The Agent Bus PRD (#22) does **not** implement a bridge between Phalanx Run /
Task / Dispatch and the Agent Bus. This document captures the intended design
so the next phase has a written contract.

## Goal

Make Phalanx Task creation enqueue an Agent Bus Message, and Bus Message
completion project back into Phalanx Dispatch / Task state, without changing
the single-Coordinator ownership of an active Run.

## Boundaries preserved

- Phalanx Run / Task / Dispatch stay exactly as they are. The Coordinator
  remains the sole writer for an active Run.
- Agent Bus Messages stay exactly as they are. `correlation_id` (Message id)
  is a separate namespace from Phalanx `dispatch_id`.

## Mapping (sketch)

- A Phalanx Task `task_xxx` is mapped 1:N to Bus Messages `msg_yyy` whenever
  the Coordinator decides to dispatch it through the bus instead of a direct
  managed Worker.
- The bridge records `task_xxx -> msg_yyy` in a small `bus_message_map` table
  inside the Phalanx DB. The bridge owns this table; the Coordinator does not.
- The bus message's `caller_id` is the Phalanx Coordinator identity
  (`phalanx-bridge`), so the bridge can pull results back.
- The bus message's `worker_id` recorded by the bus is the live TUI Agent
  identity, which is recorded into the Dispatch's `agent_name` / `agent_kind`
  when the bridge completes the Dispatch.

## Required Coordinator changes

- The Coordinator acquires a one-time option `-DispatchBackend bus`. When set,
  `task-claim` invokes the bridge to enqueue an Agent Bus Message instead of
  writing the Dispatch row directly.
- After enqueue, the Coordinator polls `result-list --caller
  phalanx-bridge --after-event <last>` (or runs the explicit supervisor) and
  completes the Dispatch via `dispatch-complete-from-output` when a Message
  result returns.
- Bus Dispatch state remains in the bus, not in Phalanx, until the bridge
  promotes the result. The Dispatch row is created with status `running`,
  `worker_name = <bus worker>`, and `agent_kind = <bus worker kind>`.

## Required Bus changes

- None. The bus exposes everything the bridge needs as it stands today:
  `enqueue`, `result-list`, `result-get`, `cancel`, `requeue`.

## Safety rules

- The bridge is the only writer of `bus_message_map`. Phalanx Coordinator
  reads it for traceability but does not mutate it.
- Bridge runs inside the Phalanx Coordinator process and uses the same
  Coordinator identity as the bus caller.
- Bus completion does not directly write Phalanx `dispatches`. It goes
  through `dispatch-complete-from-output` so the Phalanx state machine stays
  consistent.
- All evidence from the bus (raw reports, attempt artifacts, callback
  deliveries) remains in the bus. The bridge only records the structured
  result into Phalanx.

## Out of scope here

- Implementation.
- A reverse bridge (a Phalanx Task triggered by a bus event without a
  Coordinator present).
- Multi-Coordinator arbitration for the same Run.
