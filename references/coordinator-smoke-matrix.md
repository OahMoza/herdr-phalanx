# Coordinator Smoke Matrix

Run checks only in an isolated Herdr Workspace and with a temporary `PHALANX_DB` path. Do not close user-owned Pane, Tab, Workspace, session, or server resources.

## Required Matrix

| Case | Setup | Required evidence | Current evidence |
|---|---|---|---|
| Managed Worker success | Start one managed Worker, claim one managed Task, and send a harmless prompt. | Matching parsed `TASK_COMPLETE`, completed Task and Dispatch, `worker_done` Event. | Verified with OMP, Claude, OpenCode, and Hermes profiles. |
| Adapter success | Run `templates/coordinator_loop.ps1` against a ready managed Worker. | Adapter claims, prompts with `dispatch_id`, waits, reads, and completes a Dispatch through the CLI. | Verified with Hermes default: `run_c2db01d6`, final `disp_4060b17a`. |
| Non-blocking dispatch | Pass `-DispatchMode non-blocking`; the Coordinator returns after submitting the prompt and a background reader finishes the Dispatch. | Coordinator claims, prompts, and returns before the Worker settles. The reader writes the Dispatch result through the CLI. The Run owner remains the only SQLite writer. | Reader extracted to `templates/reader.ps1`; real Herdr smoke pending. |
| Worker unavailable | Remove or lose the Worker after task claim. | Task and Dispatch become `blocked`; captured evidence and `dispatch_blocked` Event exist. | Verified in `run_c2db01d6`. |
| Timeout after inspection | Worker does not settle within the explicit timeout. | Task and Dispatch are `blocked`, not completed; output and state evidence are retained. | Verified in `run_c2db01d6`. |
| Retry after uncertainty | Resolve a blocked Dispatch with `retry`, then create a new Dispatch. | Old Dispatch remains terminal; new Dispatch has a new ID; Task can complete. | Verified in `run_c2db01d6`. |
| Missing report | Send a harmless prompt that ends without `TASK_COMPLETE`. | Task and Dispatch are `blocked`; Event and captured output evidence exist. | Unit-tested; repeat as a real smoke case. |
| Parsed Worker failure | Worker emits matching `TASK_COMPLETE` with `outcome: failed`. | Dispatch fails; Task returns to pending while retries remain. | Unit-tested; repeat as a real smoke case. |
| Worker question | Worker emits matching `TASK_ASK`; Coordinator records an answer. | `worker_asked` and `worker_question_answered` Events; Dispatch resumes only after the answer. | Unit-tested; repeat as a real smoke case. |
| Parallel Workers | Start two managed Workers in separate Panes and claim two independent Tasks. | Independent Dispatches with correct Agent/Pane/Tab values and Events. | Planned real smoke. |
| Dependent Task | Create Task B with Task A as a dependency; complete A from a parsed report. | B appears in `task-ready` only after A completes. | Unit-tested; repeat as a real smoke case. |
| Restart recovery | Reopen the same database during an active Run. | Owner, Tasks, Dispatches, capabilities, and Events remain queryable and resumable. | Unit-tested; repeat as a real smoke case. |

For every case: first check `herdr --version`, `herdr workspace list`, and `herdr agent list`; use IDs from command JSON; use explicit timeouts; save command output and `event-log --run <run-id>` as smoke evidence. A real smoke failure is evidence, not a reason to infer success.
