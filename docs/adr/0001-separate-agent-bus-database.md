# ADR 0001: Separate Agent Bus Database from Phalanx Database

## Status

Accepted. 2026-09-10.

## Context

Phalanx coordinates one active Run with a single Coordinator that owns Run / Task / Dispatch state in `phalanx.db`. The Coordinator is the only writer for an active Run, and `BEGIN IMMEDIATE` is used everywhere to keep transitions atomic under that single-writer assumption.

A new requirement (issue #14, future issue from PRD #22) introduces an N:N producer / worker layer. Many Producers enqueue Messages, many same-Route Workers compete for them, and results must route back to the calling Producer. This is a multi-writer problem at the row level: two TUI Workers can race to claim the same pending Message, and the bus must let exactly one win.

Mixing this with `phalanx.db` would force the bus to use the same single-writer contract and would require every bus write to go through the active Run's Coordinator, which contradicts "many Producers, many Workers" and would couple unrelated Producers through Run ownership.

## Decision

Agent Bus is implemented in its own SQLite database at `~/.herdr-phalanx/agent-bus.db` (overridable via `AGENT_BUS_DB`). The Phalanx database and the Bus database are **separate files** with **separate schemas**, **separate CLI processes**, and **separate writer contracts**:

- Phalanx Run / Task / Dispatch remain single-writer-per-active-Run, owned by one Coordinator.
- Bus Messages are multi-writer at the row level. Atomicity comes from row-level guards (`status`, `worker_id`, `lease_id`) inside short `BEGIN IMMEDIATE` transactions, not from a single owner process.

The two layers communicate through a future Bridge module. The Bridge is **not** part of this ADR; it will be specified separately in `references/agent-bus-phalanx-bridge.md`.

## Consequences

- Bus implementation can evolve independently of Phalanx, including schema, writer contract, and CLI surface.
- Existing Phalanx behavior is preserved: no change to `db/phalanx_db.py`, `db/schema.sql`, `db/tests/test_phalanx_db.py`, or `templates/coordinator_loop.ps1` semantics.
- The same `correlation_id` is used for both layers' attempts, but each layer keeps its own correlation namespace: `dispatch_id` for Phalanx, `message.id` for the Bus. A future bridge will translate between them.
- Tests for the Bus use `AGENT_BUS_DB` to point at temporary databases, mirroring how Phalanx tests use `PHALANX_DB`.

## Alternatives considered

- **Reuse `phalanx.db`**: rejected. It would require every Bus write to be authorised by the active Run's Coordinator, collapsing the bus into a per-Run feature and breaking the "many Producers, independent Workers" goal.
- **External queue (RabbitMQ / NATS / Redis)**: rejected for this milestone. The PRD scopes Agent Bus as a single-machine local infrastructure. The current SQLite solution is the smallest infra that satisfies N:N with 1:1 result routing on one machine. Migration to NATS JetStream remains an open future path with a documented subject / reply mapping.
