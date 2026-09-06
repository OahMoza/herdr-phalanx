# Runtime Agent Capability Discovery

## Purpose

This note defines evidence that a local Windows Phalanx coordinator can collect before it assigns work to an Agent. It distinguishes Herdr support, local machine discovery, managed-agent readiness, and verified worker completion.

## Evidence Pipeline

1. Discover Herdr version and supported Agent kinds with `herdr --version` and `herdr agent start --help`.
2. Discover the Windows command that will run with `Get-Command -All <command>`. Record command type, resolved path, and version or help output. A script or shim can behave differently from a native executable.
3. Discover Herdr integration state with `herdr integration status` and diagnose recognition with `herdr agent explain --json` when needed.
4. Discover profile-based Agents from their native CLI, for example `hermes profile list`.
5. Verify managed capability in an isolated pane: `agent start`, wait for readiness, send a minimal prompt, wait for a settled Herdr state, read output, and parse a valid `TASK_COMPLETE` report.

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

- Agent kind and optional profile.
- Resolved executable path, command type, version, and help signature.
- Herdr version and integration or detection evidence.
- Discovery and verification timestamps.
- Native launch arguments, including permission mode when applicable.
- Startup, lifecycle, output-read, parser, and smoke-Dispatch result.
- Failure signature and evidence location when verification fails.

The record is a machine observation. A cached successful verification is advisory and must be refreshed after an executable, PATH, Herdr, integration, profile, or configuration change.

## Control Surface Boundary

- Use managed-agent capability only after a Worker passes the managed smoke path.
- If an Agent cannot pass Herdr lifecycle detection, it can still be used for explicitly stateless work through the raw-pane path: `pane run` and `pane wait-output`.
- Do not report raw-pane capability as managed-agent capability.

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
