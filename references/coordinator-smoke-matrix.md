# Coordinator Smoke Matrix

Run these checks only in an isolated Herdr Workspace and with a temporary `PHALANX_DB` path. Do not close user-owned Pane, Tab, Workspace, session, or server resources.

| Case | Setup | Required evidence |
|---|---|---|
| One Worker success | Start one managed Worker, claim one Task, and send a harmless prompt. | Parsed `TASK_COMPLETE`, completed Task and Dispatch, `worker_done` Event, verified capability. |
| Parallel Workers | Start two managed Workers in separate Panes and claim two independent Tasks. | Two independent Dispatches, correct Agent/Pane/Tab values, two Events. |
| Dependent Task | Create Task B with Task A as a dependency; complete A from a parsed report. | B appears in `task-ready` only after A completes. |
| Missing report | Send a harmless prompt that ends without `TASK_COMPLETE`. | Task and Dispatch are `blocked`; Event and captured output evidence exist. |

For every case: first check `herdr --version`, `herdr workspace list`, and `herdr agent list`; use IDs from command JSON; use explicit timeouts; save the command output and `event-log --run <run-id>` as the smoke evidence.
