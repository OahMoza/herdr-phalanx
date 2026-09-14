---
name: herdr-agent-bus
description: "Operate the optional Herdr Agent Bus for anonymous Worker pools and reliable asynchronous work delivery: Routes, registration, enqueue/claim, leases, heartbeat, retry, DLQ, result pull/ack, callbacks, reaping, and worker-loop delivery. Use when multiple eligible Workers should compete for queued work, Producer and Worker lifetimes are decoupled, or Bus failures and late results need recovery. Not the default Phalanx orchestration path."
compatibility: "Windows for Herdr delivery; full herdr-phalanx repository clone; Python stdlib."
---

# Herdr Agent Bus

Answer: **How should anonymous Workers compete for queued work and return results reliably?**

## Repository Root

Resolve `<PHALANX_ROOT>` as defined by `herdr-phalanx`: `HERDR_PHALANX_ROOT`, then the current directory/parents, then `$HOME/herdr-phalanx`. Require `<PHALANX_ROOT>/db/agent_bus.py`; otherwise stop and request a full clone.

## Workflow

1. Read `<PHALANX_ROOT>/references/agent-bus-protocol.md` before mutating Bus state.
2. Switch to `<PHALANX_ROOT>` and operate through `python db/agent_bus.py`; never write its SQLite database directly.
3. Configure a Route and register Workers before delivery.
4. Producers enqueue with stable caller identity and retain Message IDs.
5. Workers use claim/lease/heartbeat and finish only through `complete`, `fail`, or `cancelled` with the current `(message_id, worker_id, lease_id)`.
6. Operators reap expired leases, inspect Route status, process callbacks, and explicitly requeue DLQ entries.
7. Treat raw TUI markers as advisory; Bus CLI acknowledgement is authoritative.
8. For Herdr delivery, use the existing `worker-loop` and thin PowerShell shims. Do not assemble lease prompts inline.

## Boundaries

- Agent Bus is optional infrastructure, not the default Run/Task/DAG controller.
- It does not define business decomposition, topology ownership, Gate acceptance, or Parent acceptance.
- Use `herdr-phalanx` for direct named-Worker orchestration.
- The current Bridge exists but its successful Bus result directly promotes Phalanx Task state; do not describe that as the planned Gate-only Artifact acceptance model.
- A full repository clone is required. If `<PHALANX_ROOT>/db/agent_bus.py` is absent, stop with: `Unsupported standalone Skill copy; configure the full herdr-phalanx clone.`
