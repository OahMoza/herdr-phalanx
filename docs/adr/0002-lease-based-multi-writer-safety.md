# ADR 0002: Lease-Based Multi-Writer Safety in Agent Bus

## Status

Accepted. 2026-09-10.

## Context

Agent Bus must support many Producers and many Workers writing concurrently on a single Windows machine. The bus uses SQLite with WAL. SQLite allows multiple concurrent readers and a single writer at a time, but does not provide row-level locking across writers — concurrent transactions can race on `UPDATE ... WHERE id = ?` if both writers target the same row.

A naïve design would add a single bus-server process that serialises all writes. That introduces a daemon to operate, monitor, and restart, and conflicts with the project's "no daemon" stance (see PRD #22 Out of Scope).

## Decision

Agent Bus uses **lease-based row-level guards** inside short `BEGIN IMMEDIATE` transactions. There is no bus-server process.

- A Message in `pending` state has no `worker_id` and no `lease_id`.
- `claim` transitions one `pending` Message to `leased` only inside a `BEGIN IMMEDIATE` block that also checks the Route's active lease count against `max_in_flight`. The transaction sets `worker_id`, `lease_id`, `lease_until`, and increments `attempts`.
- `heartbeat`, `complete`, `fail`, `cancelled` all require the triple `(id, worker_id, lease_id)` and a current `status = leased`. Stale leases cannot mutate Message state.
- SQLite-level busy time is handled by a small bounded retry (up to 5 exponential backoff attempts: 50/100/200/400/800 ms) inside the CLI before returning a clear error.
- Late `complete` / `fail` / `cancelled` calls from a lease that has already been reaped are recorded as `late_result` Events but never overwrite the current Message state.
- `reap --once|--drain` is the Operator-owned tool to return expired leases to `pending` (or `dead` once `attempts` reaches `max_attempts`).

## Consequences

- The Bus has no daemon to manage. Producer / Worker / Operator each invoke the CLI explicitly.
- The Bus is correct under concurrent claim: at most one claim transaction can transition a given Message to `leased`.
- The lease is the only authority to mutate a leased Message. There is no other path.
- Long-running tasks require the Worker to call `heartbeat` periodically (default lease 5 minutes; recommend heartbeat every 60 seconds). A Worker that crashes simply lets its lease expire and `reap` recycles the Message.
- The Project's "single-machine local" trust boundary is preserved: `worker_id` is an audit identifier and `agent-bus.db` filesystem permissions are the trust boundary.

## Alternatives considered

- **Single bus-server process serialising all writes**: rejected. Adds operational burden (start, monitor, restart) and conflicts with the project's stance on local, daemon-free infrastructure.
- **Network queue (RabbitMQ / NATS)**: out of scope for this milestone; documented in PRD #22 Further Notes as a future path with a defined subject / reply mapping.
