# Runtime Agent Capability Discovery

## Purpose

This note defines evidence that a local Windows Phalanx coordinator can collect before it assigns work to an Agent. It distinguishes Herdr support, local discovery, `managed` readiness, `raw-pane` capability, and verified Worker completion.

## Evidence Pipeline

1. Discover Herdr version and supported Agent kinds with `herdr --version` and `herdr agent start --help`.
2. Discover the Windows command that will run with `Get-Command -All <command>`. Record command type, resolved path, and version or help output. A script or shim can behave differently from a native executable.
3. Discover Herdr integration state with `herdr integration status` and diagnose recognition with `herdr agent explain --json` when needed.
4. Discover profile-based Agents from their native CLI, for example `hermes profile list`.
5. Verify `managed` capability in an isolated pane: `agent start`, wait for readiness, send a minimal prompt, wait for a settled Herdr state, read output, and parse a valid `TASK_COMPLETE` report.
6. Verify `raw-pane` capability separately with its explicit command and output marker. It is not evidence for managed dispatches.

A successful `agent start` proves only interactive readiness. It does not prove that a Worker can accept a Task, recover output, or complete a Dispatch. Herdr `unknown` is classification uncertainty, not completion. Herdr `blocked` requires explicit coordinator or user handling.

## Capability Levels

| Level | Meaning | Minimum evidence |
|---|---|---|
| `declared` | Herdr supports the Agent kind. | `herdr agent start --help` lists the kind. |
| `discovered` | This machine exposes an executable, profile, or integration. | Resolved command or native profile/integration output. |
| `ready` | Herdr started the Agent and detected interactive readiness. | Successful `herdr agent start`. |
| `verified` | The Worker completed a minimal smoke Dispatch and Phalanx parsed its report. | Prompt, settled state, readable output, and parsed `TASK_COMPLETE`. |
| `degraded` | Previously useful capability failed its latest smoke verification. | Failure result with timestamp and evidence. |
| `unknown` | No current evidence supports a stronger level. | Absence of usable evidence. |

## Facts To Persist Per Observation

- Agent kind, optional profile, and capability scope: `execution` or `coordinator`.
- Execution mode: `managed` or `raw-pane`.
- Resolved executable path, command type, version, and help signature.
- Herdr version and integration or detection evidence.
- Native model identifier and invocation parameters.
- Normalized planning intensity: `low`, `medium`, or `high`; retain the native value alongside it.
- Native launch arguments, permission mode, detected shell path, and redacted configuration hash.
- Canonical SHA-256 fingerprint over the fields above.
- Discovery/verification timestamp and maximum evidence age.
- Startup, lifecycle, output-read, parser, and smoke-Dispatch result.
- Failure signature and evidence location when verification fails.

The record is append-only machine evidence. Its effective level becomes `stale` when the supplied current fingerprint differs or maximum age expires; historical `verified` rows are never rewritten. A newer degraded or stale observation must not fall back to an older verified row.

Durable compatibility is not transient availability. Before reservation or dispatch, perform a lightweight current check for authentication/readiness, provider and model availability, and immediately observable quota/service failure. A compatible but unavailable Agent is not schedulable.

## Control Surface Boundary

- A managed Task can claim only a current, fingerprint-matching verified `managed` execution capability with the required Role, followed by transient availability preflight.
- Coordinator capability is a separate scope and remains unschedulable until the later Coordinator smoke contract is implemented; Worker verification never implies it.
- A `raw-pane` capability is for explicit stateless work through `pane run` and `pane wait-output`; it cannot claim a managed Task or use `coordinator_loop.ps1`.
- Pi's current supported path is `raw-pane` with `pi -p`, not `herdr agent start --kind pi`.

## Permission Evidence

- OMP `--auto-approve` sets its session approval mode to `yolo`.
- Claude Code `--permission-mode bypassPermissions` skips permission prompts but does not bypass every safeguard or policy restriction.
- OpenCode `--auto` approves requests that are not explicitly denied.

Permission mode is part of capability evidence because a Worker blocked by a permission prompt cannot complete an unattended Dispatch.

## Primary Sources

- Installed Herdr v0.8.2 CLI: `herdr --skill`, `herdr agent start --help`, `herdr agent explain --help`, and `herdr integration status --help`, run in this Herdr session on 2026-09-06.
- Herdr v0.8.2 agent automation guide: <https://herdr.dev/docs/agent-automation/>.
- Herdr agent skill guide: <https://herdr.dev/docs/agent-skill/>.
- Herdr agent launch implementation: <https://github.com/herdrdev/herdr/blob/ca1af383/src/cli/agent.rs>.
- Herdr agent startup constraints: <https://github.com/herdrdev/herdr/blob/ca1af383/src/app/agents.rs>.
- Microsoft PowerShell `Get-Command`: <https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/get-command?view=powershell-7.6>.
- OMP approval modes: <https://github.com/can1357/oh-my-pi/blob/main/docs/approval-mode.md>.
- Claude Code CLI reference: <https://docs.anthropic.com/en/docs/claude-code/cli-reference>.
- Claude Code permissions: <https://docs.anthropic.com/en/docs/claude-code/permissions>.
- OpenCode CLI: <https://opencode.ai/docs/cli/>.
- OpenCode permissions: <https://opencode.ai/docs/permissions/>.
