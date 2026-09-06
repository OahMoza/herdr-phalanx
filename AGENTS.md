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
- The executable component is `db/phalanx_db.py`: a Python standard-library SQLite CLI implementing Run -> Task -> Dispatch orchestration. Its schema is `db/schema.sql`; keep CLI behavior and schema changes compatible.
- `templates/worker_done_preamble.md` defines the structured completion contract consumed by `parse_worker_done()`. Use the CLI's `parse-worker-done` or `dispatch-complete-from-output` rather than adding coordinator-side regex parsing; OMP can render the marker without `##` and emit unquoted file lists.
- `templates/coordinator_loop.ps1` is a thin Herdr-and-Phalanx adapter. It must use Herdr only to prompt, wait, and read; it must use the CLI to parse and persist results. Do not add inline worker-report parsing or direct SQLite writes.

## Verification

- Run the full automated suite with `python -m unittest db.tests.test_phalanx_db -v`.
- Run a focused test with `python -m unittest db.tests.test_phalanx_db.TestParseWorkerDone.test_omp_rendered_no_hash -v`.
- Keep tests isolated by setting `PHALANX_DB` to a temporary path. The CLI otherwise persists state at `~/.herdr-phalanx/phalanx.db`; use `PHALANX_DB` for any manual or destructive test scenario.
- Initialize a manual database with `python db/phalanx_db.py init-db`; CLI output is JSON by default and is intended for orchestration scripts.

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
