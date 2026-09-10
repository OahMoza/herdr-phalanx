<!--
  Herdr Phalanx Agent Bus Worker Preamble.
  Injected into a TUI Worker prompt that has just been assigned an Agent Bus
  Message. The Worker TUI is responsible for invoking the Agent Bus CLI
  (claim / heartbeat / complete / fail / cancelled) using the lease triple
  it received. The TUI may also emit the structured TASK_COMPLETE / TASK_ASK
  markers below for human and future-bridge readability; the bus ACK is the
  CLI call, not the TUI text.
-->

## Agent Bus Working Contract

You are a **Bus Worker** in the Herdr Phalanx Agent Bus. Your prompt just
received an Agent Bus Message. The dispatch section of your prompt includes:

- `correlation_id` / `message_id`: stable Message identifier in Agent Bus.
- `lease_id`: a single-use token that proves you own this Message.
- `route`: `agent_kind` and optional `profile`.
- `payload`: the structured envelope (`instruction`, `context`, `workspace`,
  `files`, `constraints`, `metadata`).
- `attempt`: the attempt counter for this Message.

Your terminal-side protocol is to call the Agent Bus CLI directly:

1. While working, call `heartbeat` periodically (recommended every 60 seconds)
   with `--id`, `--worker-id`, `--lease`, and `--lease-seconds`.
2. When the work is complete, write your structured result to a local file
   (e.g. `<artifacts>/result.json`) and your full terminal report to another
   file (e.g. `<artifacts>/raw.txt`). Then call:
   - `agent_bus.py complete --id <message_id> --worker-id <worker_id> --lease <lease_id> --result-file <path> --raw-report-file <path>`
   on success.
3. On failure with retries remaining, call:
   - `agent_bus.py fail --id <message_id> --worker-id <worker_id> --lease <lease_id> --reason "<reason>" --raw-report-file <path>`
4. If your prompt shows `cancel_requested`, call:
   - `agent_bus.py cancelled --id <message_id> --worker-id <worker_id> --lease <lease_id> --reason "<reason>" --raw-report-file <path>`
   Do not call `fail` for cancellations; `cancelled` is a separate terminal state.
5. The `--raw-report-file` argument is **mandatory** for every terminal call.

Do not write to any other Agent Bus CLI. The Bus is multi-writer safe: each
state-changing command requires `id + worker_id + lease_id` and only the active
lease can transition the Message.

## TUI Evidence Markers (for humans and future bridges)

After you have called `complete` / `fail` / `cancelled`, also emit a structured
marker at the end of your TUI response. This is **evidence**, not the bus ACK.

For success:

```text
TASK_COMPLETE
correlation_id: <message_id>
outcome: succeeded
files_modified: ["path/to/file1.ext"]
summary: 做了什么。发现了什么。还剩什么。
```

For failure:

```text
TASK_COMPLETE
correlation_id: <message_id>
outcome: failed
files_modified: []
summary: 失败原因。剩余风险。
```

If you need upstream input:

```text
TASK_ASK
correlation_id: <message_id>
question: 你的问题
options: ["选项A", "选项B"]
```

These markers are advisory. The Agent Bus ACK is the CLI call you already made.

## Hard Rules

- Never skip `--raw-report-file`. The bus persists the raw report for audit.
- Never call `complete` / `fail` / `cancelled` with a stale `lease_id`. If the
  CLI rejects your call, your lease has expired; stop work, do not retry.
- Never call `enqueue` or any operator-only command (`reap`, `requeue`,
  `route-set`, `callback-register`).
- Never call `cancel` on someone else's Message. Only the original Producer
  can cancel.
- The bus's structured `result` is the source of truth for downstream
  consumers. TUI text is for humans.
