---
id: agent-bus-real-pi-tui-smoke
title: Agent Bus real Pi TUI smoke (herdr 0.9.0)
status: verified
platform: windows
date: 2026-09-11
herdr_version: 0.9.0
result: pass
reproduction:
  - python db/agent_bus.py init-db
  - python db/agent_bus.py route-set --agent-kind pi --profile local --max-in-flight 1 --default-lease-seconds 300
  - python db/agent_bus.py enqueue --caller smoke --agent-kind pi --profile local --payload "{...}"
  - python db/agent_bus.py worker-loop --worker-id smoke-w1 --agent-kind pi --profile local --agent-name inspect-pi --lease-seconds 60
related:
  - ADR-0003-separate-agent-bus-core-from-herdr-adapter
  - ADR-0004-executable-callback-registry
evidence:
  - runs/20260911_real-pi-tui-smoke/README.md (locally; runs/ is gitignored)
---

# Agent Bus real Pi TUI smoke (herdr 0.9.0)

## Claim

End-to-end validation of the Agent Bus Core / Herdr Adapter split
(ADR-0003) against a real Herdr-managed Pi TUI agent, not a fake
binary. The Bus Core's `worker_loop_once` path goes through
`HerdrCommanderRunner.run_agent_prompt` which shells out to
`herdr agent prompt <name> <text>` and returns the exit-code-gated
result. Pi must accept the prompt and render the restricted lease
envelope.

## Evidence

- Target: `inspect-pi` at `wD:p2` (managed Pi, `interactive_ready: true`).
- Workspace: `wD` (`phalanx-runtime-inspect-20260908`).
- Isolated `AGENT_BUS_DB` / `AGENT_BUS_ARTIFACTS` in
  `%TEMP%\pi-smoke\`.
- Final worker-loop JSON: `{"claimed":true,"delivered":true,
  "message_id":"msg_81b07062a3",
  "lease_id":"lease_PoO3fPNHuwwvKyJnulivQ54iEhASY3kP",
  "worker_id":"smoke-w1","agent_name":"inspect-pi","attempt":2}`.
- `herdr agent read inspect-pi` confirmed the full restricted envelope
  rendered in Pi's pane:
  - `## Message` with `id`, `agent_kind`, `profile`, `attempts`,
    `lease_id`.
  - `## Payload` with the inline JSON.
  - `## Allowed Commands`: `heartbeat`, `complete`, `fail`,
    `cancelled` with flag names `--id`, `--worker-id`, `--lease`,
    `--result-file`, `--raw-report-file` matching the real CLI.
  - `## Forbidden`: `enqueue`, `claim`, `route-set`,
    `route-delete`, `worker-register`.
- Pi transitioned from `idle` to `working` and parsed the envelope
  immediately.

## Bugs found

1. **Adapter used the herdr 0.8.2 flag shape**
   (`--workspace-pane`, `--no-block`) against herdr 0.9.0. Herdr 0.9.0
   uses positional `<TARGET> <TEXT>` and rejects `--timeout` without
   `--wait`. Fixed by switching to positional args.
2. **Lease envelope taught the wrong CLI flag names**
   (`--message-id`, `--lease-id`, `--result-json`, `--report-path`).
   The real CLI uses `--id`, `--lease`, `--result-file`,
   `--raw-report-file`. Fixed in `db/herdr_adapter.py` and asserted in
   `test_herdr_adapter.py::TestBuildLeasePrompt`.
3. **Herdr 0.9.0's `--wait` gate rejects all input in this
   environment** (`agent prompt timed out before input submission`).
   Reproduced against Pi, OMP, and Hermes-managed agents. Adapter
   therefore submits without `--wait`; herdr returns as soon as the
   bracketed paste writes succeed, and delivery confirmation rides on
   the exit code. This is a herdr-side bug; the Bus contract is
   unaffected.

## Verified by

- `python -m unittest discover -s db/tests` → 109/109 passing.
- Live `herdr agent prompt inspect-pi <envelope>` → `delivered: true`.
- `db-trace.txt` shows the full event lifecycle
  (`route_created → message_enqueued → message_claimed → message_heartbeat → ...`).

## Known gaps

- Worker-side terminal calls (`heartbeat` / `complete` from inside Pi)
  were not exercised end-to-end; the smoke stopped after Pi confirmed
  receipt and parsed the envelope. Those code paths are covered by the
  unit tests with a fake runner (`test_herdr_adapter.py::TestWorkerLoopOnce`).
