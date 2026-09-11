# ADR 0003: Separate AgentBus Core from Herdr Adapter

## Status

Accepted. 2026-09-11.

## Context

The Agent Bus grew inside a single 1114-line CLI (`db/agent_bus.py`)
that mixed four orthogonal responsibilities:

1. **Business logic** — route registration, enqueue / claim / heartbeat
   / complete / fail / cancel / requeue / reap, callback enqueue and
   delivery.
2. **Persistence** — SQLite connection management, `BEGIN IMMEDIATE`,
   `busy_timeout` retry, WAL journaling, row-to-dict serialisation.
3. **Adapter duties** — PowerShell-shaped lease prompt construction,
   `subprocess.run("powershell ...", shell=True)` callback execution,
   Herdr TUI `agent prompt` invocation.
4. **CLI surface** — argparse parsing, JSON I/O, error-to-exit mapping.

The mix made the bus non-portable: any code path that touched
`shell=True` or PowerShell-shaped prompts forced a Windows-only runtime
even though the lease state machine, claim arbitration, and callback
registry are platform-agnostic. Tests had to live end-to-end via
`subprocess.run` because there was no in-process seam.

## Decision

Split the bus into three layers:

* **Core (`db/agent_bus_core.py`)** — a single `AgentBus` class that hides
  SQLite, the lease state machine, the artifact filesystem, the callback
  subprocess adapter, and prompt construction behind one business-action
  interface. The Core has no knowledge of Herdr TUI, PowerShell, or
  argv layout.
* **Herdr Adapter (`db/herdr_adapter.py`)** — owns the Herdr-specific
  surface: the restricted lease prompt template, the `HerdrCommanderRunner`
  that shells out to `herdr agent prompt` with a fixed argv list
  (`shell=False`), and the future Windows-only entry points.
* **CLI adapter (`db/agent_bus.py`)** — the same Python script we have
  always had, but slimmed down to argument parsing, JSON I/O, error
  handling, and a `worker-loop` subcommand that wires the Core to the
  Herdr Adapter. Every previous CLI subcommand name and JSON shape is
  preserved.

The Core accepts an injected **Command Runner** for callback execution
(`SubprocessCommandRunner` for argv mode, `ShellTemplateCommandRunner` for
legacy `--command-template` mode). The Herdr Adapter also accepts an
injected Command Runner for prompt delivery (production:
`HerdrCommanderRunner`; tests: `FakeCommandRunner`).

PowerShell templates (`templates/worker_loop.ps1`,
`templates/agent_bus_supervisor.ps1`) shrink to thin shims that call
`python db/agent_bus.py worker-loop` / `reap` / `route-status`. They no
longer assemble lease prompts inline.

## Consequences

* Core is portable Python with stdlib only. A future Linux / WSL port
  would reuse it unchanged and ship a different Herdr Adapter.
* Tests for the lease state machine, prompt construction, and adapter
  contract run in-process. `db/tests/test_herdr_adapter.py` injects a
  fake runner and asserts on the captured arguments without spawning
  `herdr` or PowerShell.
* Two new tests files: `test_herdr_adapter.py` (5 tests) covering the
  lease prompt envelope and the Core's `worker_loop_once`. All
  previous 42 Agent Bus tests still pass through the CLI adapter.
* `worker-loop` is a new CLI subcommand. It is the only place that
  shells out to Herdr from inside the bus; the rest of the CLI stays
  JSON-in / JSON-out.
* Callback model now has two parallel paths:
  * **New**: `--executable PATH --arguments '[json-array]'`. Core
    validates that the executable is an absolute path that exists and
    runs it via `SubprocessCommandRunner` (`shell=False`). Stdout /
    stderr are persisted as artifacts.
  * **Legacy**: `--kind command --command-template "..."`. Core stores
    the template and runs it via `ShellTemplateCommandRunner`
    (`shell=True`). Kept for backwards compatibility with existing
    scripts.
* Schema gained two columns on `bus_callbacks`:
  `executable TEXT` and `arguments_json TEXT`. Existing rows migrate
  in place (no rebuild). `command_template` is preserved for the
  legacy path.

## Alternatives considered

* **Keep everything in `db/agent_bus.py`**: rejected. The deepening
  audit identified the 1114-line module as a "wide" module with at
  least three hidden axes (persistence / business / Herdr). Any
  future cross-platform port would require editing the same file
  three different ways.
* **Split into two packages (`agent_bus_core`, `herdr_adapter`) under
  a new namespace**: rejected for this milestone. The current
  `db/` layout keeps the CLI's `db/agent_bus.py` import path
  unchanged, which preserves every existing test fixture and external
  script invocation. Renaming can come later if the layers grow.
* **Drop the legacy `--command-template` path entirely**: rejected.
  Existing scripts (e.g. the test fixtures under
  `db/tests/test_agent_bus.py::TestCallback`) rely on it, and the
  threat model for the new executable path doesn't justify breaking
  the legacy path in the same refactor. The new path is the
  recommended surface; the legacy path is documented as deprecated.
