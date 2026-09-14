---
name: herdr-runtime-init
description: "Discover and verify the local Herdr scheduling environment: available coding agents, profiles, models, invocation intensity, execution mode, versions, and current capability evidence. Use when initializing or refreshing the local agent roster, choosing models or reasoning strength, checking which agents are callable, or diagnosing stale/degraded capability observations. Requires Windows and HERDR_ENV=1 before Herdr control commands."
compatibility: "Windows; full herdr-phalanx repository clone; Python stdlib and Herdr installed."
---

# Herdr Runtime Init

Answer: **Which local Agents can be scheduled now, with which Profile, model, intensity, and execution mode?**

## Repository Root

Resolve `<PHALANX_ROOT>` as defined by `herdr-phalanx`: `HERDR_PHALANX_ROOT`, then the current directory/parents, then `$HOME/herdr-phalanx`. Require `<PHALANX_ROOT>/db/phalanx_db.py`; otherwise stop and request a full clone.

## Workflow

1. Require `HERDR_ENV=1` before any Herdr control command.
2. Read `<PHALANX_ROOT>/references/agent-capability-discovery.md`.
3. For shell and launch context, read `<PHALANX_ROOT>/references/shell-conventions.md`.
4. For current candidates only, read `<PHALANX_ROOT>/references/agent-roster.md` and relevant model-catalog entries under `<PHALANX_ROOT>/wiki/`; treat Wiki as historical evidence, never current verification.
5. Switch to `<PHALANX_ROOT>`, query real Herdr/local command JSON, and record executable/config/Herdr/model/native-parameter evidence through `python db/phalanx_db.py capability-record ...`; omit secrets from evidence and config hashes.
6. Compute/store the canonical fingerprint. Use `capability-list --current --fingerprint <current>` to expose `effective_level`; fingerprint mismatch or expiry is `stale`.
7. Run active Worker smoke verification only when scheduling requires `verified`; safe discovery must not start every Agent.
8. Before actual reservation/dispatch, check transient auth/readiness, provider/model availability, and immediately observable quota/service failures. Do not turn that transient check into durable verification.
9. Return the catalog with Agent kind, Profile, capability scope, model/native invocation, normalized intensity, execution mode, stored/effective level, fingerprint, version, and observed time.

Existing runtime limitation: Coordinator Capability has a reserved independent scope but its full behavioral smoke is not implemented yet. Report it as unschedulable; do not infer it from Worker verification.

## Boundaries

- Use `herdr-phalanx` to create and execute Run/Task/Dispatch work.
- Use `herdr-agent-bus` only for anonymous competing Worker pools and Bus operations.
- A full repository clone is required. If `<PHALANX_ROOT>/db/phalanx_db.py` is absent, stop with: `Unsupported standalone Skill copy; configure the full herdr-phalanx clone.`
