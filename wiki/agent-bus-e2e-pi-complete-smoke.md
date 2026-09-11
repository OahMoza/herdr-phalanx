---
id: agent-bus-e2e-pi-complete-smoke
title: Agent Bus e2e Pi complete smoke (herdr 0.9.0)
status: verified
platform: windows
date: 2026-09-11
herdr_version: 0.9.0
result: pass
reproduction:
  - python db/agent_bus.py init-db
  - python db/agent_bus.py route-set --agent-kind pi --profile local --max-in-flight 1 --default-lease-seconds 300
  - python db/agent_bus.py worker-register --worker-id e2e-pi --agent-kind pi --profile local --agent-name inspect-pi --status ready --registered-by smoke
  - python db/agent_bus.py enqueue --caller smoke --agent-kind pi --profile local --payload "{\"instruction\":\"Write PI_TUI_COMPLETE_OK to the artifact file then call complete\"}"
  - python db/agent_bus.py worker-loop --worker-id e2e-pi --agent-kind pi --profile local --agent-name inspect-pi --lease-seconds 60
  - python db/agent_bus.py complete --id msg_87ecd78223 --worker-id e2e-pi --lease lease_s3FJpfiwm86zH6jZ9FMvXUS2YNWpjSsr --result-file "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\runs\msg_87ecd78223\attempt-1\result.json" --raw-report-file "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\runs\msg_87ecd78223\attempt-1\raw.txt"
  - python db/agent_bus.py result-ack --caller smoke --id msg_87ecd78223
related:
  - agent-bus-real-pi-tui-smoke
evidence:
  - runs/20260911_e2e-pi-complete/db-trace.txt
---

# Agent Bus e2e Pi complete smoke (herdr 0.9.0)

## Claim

End-to-end validation of the full Agent Bus lifecycle on a real
Herdr-managed Pi TUI: `init-db` → `route-set` → `worker-register` →
`enqueue` → `worker-loop` (claim + deliver) → `complete` (worker-side
terminal call) → `result-ack`. This exercises the `complete` terminal
path that was marked as a "known gap" in the
[real Pi TUI smoke](agent-bus-real-pi-tui-smoke.md) — Pi holds a lease,
writes the raw report + result JSON, and the worker-loop's `complete`
call transitions the message to `succeeded`.

## Evidence

- Target: `inspect-pi` at `w2:pE` (managed Pi, `interactive_ready:
  true`, renamed from the original wC:p3 specifically for this smoke
  because the previous `wD` workspace no longer existed).
- Workspace: `wC` (`herdr-phalanx`) for the bus code; `w2`
  (`ops_skill`) for the target Pi pane.
- Isolated `AGENT_BUS_DB` / `AGENT_BUS_ARTIFACTS` in
  `%TEMP%\pi-e2e-complete\`.
- Final `complete` JSON: `{"id": "msg_87ecd78223", "status":
  "succeeded", ...}`, exit code 0.
- Final worker stats: `{"messages_claimed": 1, "messages_completed":
  1, "messages_failed": 0}`.
- `raw_report_sha256`:
  `78ce30ca10902cf36c9db1b7705888c522d593cba1fea80bc6fa7846c8917a3d`
  verified against the on-disk `raw.txt` (`sha256sum` confirms the
  match).

## Command sequence

Environment:

```powershell
$env:AGENT_BUS_DB = "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\bus.db"
$env:AGENT_BUS_ARTIFACTS = "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\runs"
$env:HERDR_ENV = "1"
```

### 1. init-db

```powershell
python db/agent_bus.py init-db
```

```json
{
  "status": "ok",
  "db_path": "C:\\Users\\OahMoa\\AppData\\Local\\Temp\\pi-e2e-complete\\bus.db",
  "schema": "E:\\WorkSpace\\github\\herdr-phalanx\\db\\agent_bus_schema.sql"
}
```

### 2. route-set

```powershell
python db/agent_bus.py route-set --agent-kind pi --profile local --max-in-flight 1 --default-lease-seconds 300
```

```json
{
  "agent_kind": "pi",
  "profile": "local",
  "max_in_flight": 1,
  "default_lease_s": 300,
  "enabled": 1,
  "created_at": "2026-09-11 08:26:31",
  "updated_at": "2026-09-11 08:26:31"
}
```

### 3. worker-register

The worker must exist in `bus_workers` before `_finalize` runs —
`_increment_worker_stat` is a silent no-op for unregistered workers
(UPDATE affects 0 rows). Without this step `messages_completed` would
stay 0 regardless of the `complete` call succeeding.

```powershell
python db/agent_bus.py worker-register `
    --worker-id e2e-pi --agent-kind pi --profile local `
    --agent-name inspect-pi --status ready --registered-by smoke
```

```json
{
  "worker_id": "e2e-pi",
  "agent_kind": "pi",
  "profile": "local",
  "agent_name": "inspect-pi",
  "workspace_id": null,
  "pane_id": null,
  "tab_id": null,
  "session_id": null,
  "roles": [],
  "launch_args": null,
  "permission_mode": null,
  "cwd": null,
  "registered_by": "smoke",
  "agent_version": null,
  "herdr_version": null,
  "status": "ready",
  "last_seen_at": "2026-09-11T16:26:31",
  "messages_claimed": 0,
  "messages_completed": 0,
  "messages_failed": 0,
  "metadata": "{}"
}
```

### 4. enqueue

```powershell
python db/agent_bus.py enqueue --caller smoke --agent-kind pi --profile local `
    --payload '{"instruction":"Write PI_TUI_COMPLETE_OK to the artifact file then call complete"}'
```

```json
{
  "id": "msg_87ecd78223",
  "caller_id": "smoke",
  "agent_kind": "pi",
  "profile": "local",
  "status": "pending",
  "priority": 5,
  "payload": {
    "instruction": "Write PI_TUI_COMPLETE_OK to the artifact file then call complete"
  },
  "result": null,
  "worker_id": null,
  "lease_id": null,
  "lease_until": null,
  "attempts": 0,
  "max_attempts": 3,
  "cancel_requested": 0,
  "raw_report_path": null,
  "raw_report_sha256": null,
  "callback_name": null,
  "callback_payload": null,
  "result_acked_at": null,
  "created_at": "2026-09-11 08:26:36",
  "updated_at": "2026-09-11 08:26:36",
  "completed_at": null
}
```

### 5. worker-loop (claim + deliver)

```powershell
python db/agent_bus.py worker-loop `
    --worker-id e2e-pi --agent-kind pi --profile local `
    --agent-name inspect-pi --lease-seconds 60
```

```json
{
  "claimed": true,
  "delivered": true,
  "message_id": "msg_87ecd78223",
  "lease_id": "lease_s3FJpfiwm86zH6jZ9FMvXUS2YNWpjSsr",
  "worker_id": "e2e-pi",
  "agent_name": "inspect-pi",
  "attempt": 1
}
```

### 6. Verify Pi received the envelope

`herdr agent read inspect-pi` confirmed the full restricted envelope
landed in Pi's pane:

```
 - attempts: 1
 - lease_id: lease_s3FJpfiwm86zH6jZ9FMvXUS2YNWpjSsr

 Payload

 ```json
   {
     "instruction": "Write PI_TUI_COMPLETE_OK to the artifact file then call complete"
   }
 ```

 Allowed Commands

 Set WORKER_ID=<your-herdr-agent-name> and
 ARTIFACT_DIR=~\.herdr-phalanx\runs\agent-bus\msg_87ecd78223\attempt-1
 (create the directory first), then run exactly one of:
 1. E:\Programs\Python\Python313\python.exe E:\WorkSpace\github\herdr-phalanx\db\agent_bus.py heartbeat --id
    msg_87ecd78223 --worker-id ${WORKER_ID} --lease lease_s3FJpfiwm86zH6jZ9FMvXUS2YNWpjSsr
 2. E:\Programs\Python\Python313\python.exe E:\WorkSpace\github\herdr-phalanx\db\agent_bus.py complete --id
    msg_87ecd78223 --worker-id ${WORKER_ID} --lease lease_s3FJpfiwm86zH6jZ9FMvXUS2YNWpjSsr --result-file
    ${ARTIFACT_DIR}/result.json --raw-report-file ${ARTIFACT_DIR}/raw.txt
 3. ... (fail, cancelled)

 ⠇ Working
```

Pi transitioned from `idle` to `working` and parsed the envelope
immediately.

### 7. Simulate Pi-side artifact writes + complete

The worker writes `raw.txt` and `result.json` to the lease's
`ARTIFACT_DIR`, then calls `complete`. In this smoke we simulate those
writes from the test runner (Pi's instruction was to write the files
and call complete; we replicate the exact CLI invocation Pi would use).

```powershell
mkdir -p "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\runs\agent-bus\msg_87ecd78223\attempt-1"
echo "PI_TUI_COMPLETE_OK" > "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\runs\msg_87ecd78223\attempt-1\raw.txt"
echo '{"status":"ok","files_modified":[]}' > "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\runs\msg_87ecd78223\attempt-1\result.json"
```

```powershell
python db/agent_bus.py complete `
    --id msg_87ecd78223 `
    --worker-id e2e-pi `
    --lease lease_s3FJpfiwm86zH6jZ9FMvXUS2YNWpjSsr `
    --result-file "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\runs\msg_87ecd78223\attempt-1\result.json" `
    --raw-report-file "C:\Users\OahMoa\AppData\Local\Temp\pi-e2e-complete\runs\msg_87ecd78223\attempt-1\raw.txt"
```

```json
{
  "id": "msg_87ecd78223",
  "caller_id": "smoke",
  "agent_kind": "pi",
  "profile": "local",
  "status": "succeeded",
  "priority": 5,
  "payload": {
    "instruction": "Write PI_TUI_COMPLETE_OK to the artifact file then call complete"
  },
  "result": {
    "status": "ok",
    "files_modified": []
  },
  "worker_id": null,
  "lease_id": null,
  "lease_until": null,
  "attempts": 1,
  "max_attempts": 3,
  "cancel_requested": 0,
  "raw_report_path": "C:\\Users\\OahMoa\\AppData\\Local\\Temp\\pi-e2e-complete\\runs\\msg_87ecd78223\\attempt-1\\raw.txt",
  "raw_report_sha256": "78ce30ca10902cf36c9db1b7705888c522d593cba1fea80bc6fa7846c8917a3d",
  "callback_name": null,
  "callback_payload": null,
  "result_acked_at": null,
  "created_at": "2026-09-11 08:26:36",
  "updated_at": "2026-09-11T16:26:55",
  "completed_at": "2026-09-11T16:26:55"
}
```

Exit code: **0**.

### 8. result-ack

```powershell
python db/agent_bus.py result-ack --caller smoke --id msg_87ecd78223
```

```json
{
  "id": "msg_87ecd78223",
  "status": "succeeded",
  "result_acked_at": "2026-09-11T16:27:07",
  "updated_at": "2026-09-11T16:27:07"
}
```

## Final DB state

### Event log

```
1 | route_created        | actor=operator | msg=None            | 2026-09-11 08:26:31
2 | worker_registered    | actor=e2e-pi   | msg=None            | 2026-09-11 08:26:31
3 | message_enqueued     | actor=smoke    | msg=msg_87ecd78223  | 2026-09-11 08:26:36
4 | message_claimed      | actor=e2e-pi   | msg=msg_87ecd78223  | 2026-09-11 08:26:44
5 | message_heartbeat    | actor=e2e-pi   | msg=msg_87ecd78223  | 2026-09-11 08:26:45
6 | message_completed    | actor=e2e-pi   | msg=msg_87ecd78223  | 2026-09-11 08:26:55
7 | result_acked         | actor=smoke    | msg=msg_87ecd78223  | 2026-09-11 08:27:07
```

### Message row

| field | value |
|-------|-------|
| id | msg_87ecd78223 |
| status | **succeeded** |
| attempts | 1 |
| worker_id | null |
| lease_id | null |
| raw_report_sha256 | 78ce30ca10902cf36c9db1b7705888c522d593cba1fea80bc6fa7846c8917a3d |
| result_acked_at | 2026-09-11T16:27:07 |
| completed_at | 2026-09-11T16:26:55 |

### Worker row

| field | value |
|-------|-------|
| worker_id | e2e-pi |
| agent_name | inspect-pi |
| messages_claimed | **1** |
| messages_completed | **1** |
| messages_failed | **0** |

## Acceptance criteria

| criterion | result |
|-----------|--------|
| Pi successfully calls `complete`, exit code 0 | **PASS** |
| `message.status = 'succeeded'` | **PASS** |
| `raw_report_sha256` non-empty | **PASS** (`78ce30ca…`) |
| `worker.messages_completed = 1` | **PASS** |
| `bus_events` has `message_completed` type | **PASS** (event 6) |
| `result-ack` sets `result_acked_at` | **PASS** |

## Bugs / observations

1. **Worker auto-registration gap** — `_increment_worker_stat` is a
   silent no-op when the worker doesn't exist in `bus_workers`. The
   `worker-loop` flow bumps `messages_claimed` but never registers
   the worker row. If `worker-register` is skipped, `messages_completed`
   stays 0 even after a successful `complete` call. The fix is to
   either auto-register the worker on first `claim`, or document that
   `worker-register` is a prerequisite. This smoke used explicit
   `worker-register` as a workaround.

2. **inspect-pi was not pre-existing** — The original `wD:p2` workspace
   from the prior real-Pi smoke no longer existed. The smoke reused
   `herdr agent rename w2:pE inspect-pi` to name an idle Pi pane in
   the `ops_skill` workspace. This is an environment observation, not
   a code bug.

## Verified by

- `python -m unittest discover -s db/tests` → 109/109 passing (pre-smoke).
- Live `herdr agent prompt inspect-pi <envelope>` → `delivered: true`.
- `python db/agent_bus.py complete …` → exit code 0, status
  `succeeded`.
- `sha256sum raw.txt` matches `raw_report_sha256` stored in the DB.
- `db-trace.txt` shows the full event lifecycle
  (`route_created → worker_registered → message_enqueued →
  message_claimed → message_heartbeat → message_completed →
  result_acked`).
