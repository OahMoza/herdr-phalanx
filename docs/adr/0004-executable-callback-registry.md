# ADR 0004: Executable Callback Registry Replaces Shell Command Templates

## Status

Accepted. 2026-09-11.

## Context

The original Agent Bus stored callback invocations as a `command_template`
TEXT column in `bus_callbacks`. Delivery executed the template via
`subprocess.run(template, shell=True)`. This made callback delivery:

1. **Shell-injection-prone** — every `{message_id}` placeholder expansion
   happened after the shell parsed the string. Anything that could
   contain a space, a `&`, or a `;` was a foothold for arbitrary
   command execution.
2. **Windows-only by convention** — PowerShell-friendly templates are
   not portable to other shells. A future Linux / WSL port would
   re-introduce the shell-quoting problem from scratch.
3. **Hard to test** — there was no seam between the bus and the OS
   process. Tests either skipped callback delivery or relied on a
   real `powershell.exe` install.

## Decision

Callbacks are now registered with two parallel shapes:

* **New (recommended)**: `--executable PATH --arguments '["..."]'`.
  The Core validates that the executable is an absolute path that
  exists, then runs it via `SubprocessCommandRunner`
  (`subprocess.run([exe, *args], shell=False)`). Stdout / stderr land
  in the message's artifact directory.
* **Legacy**: `--kind command --command-template "..."`. The Core
  stores the template and runs it via `ShellTemplateCommandRunner`
  (`shell=True`). Kept for backwards compatibility but documented as
  deprecated.

The two surfaces share the same `bus_callbacks` row. Schema:

```sql
CREATE TABLE bus_callbacks (
  name              TEXT PRIMARY KEY,
  kind              TEXT NOT NULL,
  command_template  TEXT NOT NULL DEFAULT '',
  executable        TEXT,
  arguments_json    TEXT,
  enabled           INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
  ...
);
```

The Core decides the runner from the row shape:

* `executable` non-null and points at an existing file →
  `SubprocessCommandRunner`.
* `command_template` non-empty and `executable` is null →
  `ShellTemplateCommandRunner`.
* Neither → delivery fails immediately with `dead` status.

`init-db` adds `executable` and `arguments_json` columns to existing
databases via `ALTER TABLE`. Existing `command_template` rows keep
working; new callbacks should register the executable shape.

## Consequences

* No shell metacharacter expansion for new callbacks. The argv list
  goes straight to `subprocess.run` with `shell=False`. The Core
  cannot accidentally execute a callback that contains `; rm -rf /`.
* Callback execution is testable in-process: tests inject
  `SubprocessCommandRunner` (or a fake) and assert on the argv list
  captured at delivery time.
* Future Linux / WSL Adapter just calls `SubprocessCommandRunner`. No
  shell-string path needed.
* Two new tests cover the path in `db/tests/test_agent_bus.py`
  indirectly through the CLI; the unit seam is in
  `db/tests/test_herdr_adapter.py`.

## Alternatives considered

* **Drop `command_template` entirely on migration**: rejected for
  this milestone. The test suite (`TestCallback`) and existing
  external scripts depend on it. Breaking them in the same refactor
  would multiply the diff and obscure the layering change. The
  legacy path stays until a future ADR retires it.
* **Always run via `shell=False` with the template split into argv
  by the caller**: rejected. The existing test fixtures build shell
  strings (`powershell -NoProfile ...`) that have no clean argv
  decomposition. Forcing callers to split would require a separate
  template engine and is a bigger redesign than this milestone
  needs.
* **Embed a whitelist of allowed executables**: deferred. The new
  shape gives the right primitive (absolute path + argv). Whitelisting
  belongs in the Adapter / orchestration layer, not in the bus Core.
