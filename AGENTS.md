# Herdr Phalanx Agent Guide

## Agent Skills

### Issue Tracker

Issues and specs use this repository's GitHub Issues through `gh`. See `docs/agents/issue-tracker.md`.

### Triage Labels

Use the canonical triage labels defined in `docs/agents/triage-labels.md`.

### Domain Docs

This is a single-context repository. See `docs/agents/domain.md`.

## Repository Shape

- This is a Windows-focused Herdr orchestration skill, not a package-managed application. `SKILL.md` is the primary behavior specification; `references/herdr-cli-quickref.md` is the verified Herdr v0.8.2 command reference.
- The executable components are two independent Python standard-library SQLite layers, each split into Core + CLI adapter:
  - Phalanx (`db/phalanx_db.py` with `db/schema.sql`): Run / Task / Dispatch orchestration, single-writer per active Run.
  - Agent Bus (`db/agent_bus.py` CLI adapter with `db/agent_bus_schema.sql`): N:N lease-based queue, multi-writer row-level. The bus implementation is split into:
    - `db/agent_bus_core.py` — `AgentBus` Core (hides SQLite, lease state machine, artifact filesystem, callback subprocess).
    - `db/herdr_adapter.py` — Herdr Adapter facade (lease prompt template, `HerdrCommanderRunner` invoking `herdr agent prompt` via argv + `shell=False`).
    - `db/callback_runner.py` — `SubprocessCommandRunner` (argv) and `ShellTemplateCommandRunner` (legacy shell template). Injected into the Core so tests can use a fake.
    - `db/agent_bus.py` — thin argparse + JSON I/O adapter that delegates every command to `AgentBus`. Adds the new `worker-loop` subcommand.
- The two layers share the local-glossary vocabulary in `CONTEXT.md` and the ADR index under `docs/adr/`. They do **not** share a database and do **not** share a writer contract. Phalanx is single-writer-per-active-Run; the Bus is multi-writer row-level via leases.
- `templates/worker_done_preamble.md` defines the structured completion and question contracts consumed by `parse_worker_done()` and `parse_worker_ask()`. Use CLI parsing/persistence commands rather than coordinator-side regex parsing; `TASK_COMPLETE` and `TASK_ASK` must bind the current `dispatch_id`.
- `templates/agent_bus_worker_preamble.md` defines the Bus-side completion contract that Bus Workers emit alongside their CLI ACK. Bus ACK is the CLI call; the TUI markers are advisory.
- `templates/coordinator_loop.ps1` is a thin Herdr-and-Phalanx adapter. It must use Herdr only to prompt, wait, and read; it must use the CLI to parse and persist results. Do not add inline worker-report parsing or direct SQLite writes.
- `templates/worker_loop.ps1` and `templates/agent_bus_supervisor.ps1` are the Bus equivalents and have been shrunk to thin PowerShell shims over `python db/agent_bus.py worker-loop` / `reap` / `route-status`. They do not assemble lease prompts inline and do not run as daemons.

## Verification

- Run the full automated suite with `python -m unittest discover -s db/tests -v`. It includes `test_phalanx_db.py`, `test_agent_bus.py`, and `test_herdr_adapter.py` (109 tests total).
- For focused tests, use the full dotted path; e.g. `python -m unittest db.tests.test_agent_bus.TestClaim.test_priority_then_fifo -v`.
- Keep tests isolated by setting `PHALANX_DB`, `AGENT_BUS_DB`, and `AGENT_BUS_ARTIFACTS` to temporary paths. Default databases live at `~/.herdr-phalanx/phalanx.db` and `~/.herdr-phalanx/agent-bus.db`; override for any manual or destructive scenario.
- Initialize manual databases with `python db/phalanx_db.py init-db` and `python db/agent_bus.py init-db`. CLI output is JSON by default and is intended for orchestration scripts.
- For the Agent Bus Core/Adapter seam, prefer constructing an `AgentBus` instance with explicit `database_path` and `artifacts_root`, then calling methods directly. Inject `callback_runner.SubprocessCommandRunner` or `callback_runner.ShellTemplateCommandRunner` to test callback execution; the Herdr Adapter accepts any object with `run_agent_prompt(...)` (use the `_FakeCommandRunner` in `test_herdr_adapter.py` as a template).

## Herdr Safety Rules

- Run Herdr control commands only with `HERDR_ENV=1`. Before changing topology, check `herdr --version`, `herdr workspace list`, and `herdr agent list`; obtain workspace, tab, and pane IDs from command JSON, never examples.
- Hermes is the dispatcher, not a pane worker. Keep it in its dedicated command tab; worker panes use real agent binaries.
- Preserve the 2x2 worker-grid invariant: at most four agents per worker tab, all splits use `--ratio 0.5 --no-focus`, and a fifth worker starts a new tab. Do not close workspaces, tabs, panes, or stop the Herdr server unless the user explicitly requests it.
- Use `herdr agent prompt` for a waiting managed Worker. Use `herdr pane run` only for an ordinary command in an explicit Pane; never use it against an existing non-caller Pane unless that Pane is focused first.
- Use blocking `herdr agent wait --until ...` or `herdr pane wait-output --match ...` with explicit timeouts, never sleep-polling. For multiple workers, wait in parallel with PowerShell jobs and `Wait-Job -Any`.
- Start managed workers with their required flags: OMP `-- --auto-approve`, Claude `-- --permission-mode bypassPermissions`, OpenCode `-- --auto`; Hermes profiles need no bypass flag. Pi is for stateless `pane run ... 'pi -p ...'` work, not `herdr agent start --kind pi`.

## Knowledge And Artifacts

- `runs/` is ignored raw, machine-specific trace data. Do not add it to Git.
- `wiki/` is append-only institutional memory. Preserve failed or obsolete knowledge; add a new/versioned article rather than rewriting or deleting historical entries.
- When changing `SKILL.md`, keep the frontmatter version/changelog and `Upgrade Hooks` synchronized. Changes to ontology entities or relations also require a schema version bump.
