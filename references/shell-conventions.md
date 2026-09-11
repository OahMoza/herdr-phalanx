# Shell Conventions

## Detection logic

The Skill does not hardcode shell paths. It detects the user's shell at runtime.

### Windows

```
1. where pwsh → PowerShell 7 (if in PATH)
2. $env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe → PowerShell 5.1 (system fallback)
```

Detection command (run inside PowerShell):

```powershell
$shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) {
    (Get-Command pwsh).Source
} else {
    "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
}
```

Or use the bundled script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/detect-shell.ps1
```

### WSL / Linux

```
1. $SHELL environment variable
2. /bin/bash (fallback)
```

## Impact areas

### OMP Agent

`~/.omp/agent/settings.json` `shellPath` should point to the detected shell.
On Windows, run `scripts/detect-shell.ps1` and set the result as `shellPath`.

### PowerShell templates

`templates/*.ps1` use PowerShell 5.1+ compatible syntax. Do not rely on
PowerShell 7-only features (e.g. `??`, simplified ternary).

### Bus Worker commands

`build_lease_prompt` generates commands using POSIX shell syntax (`${VAR}`),
because the Worker executes commands inside its TUI using whatever shell
the agent's `shellPath` points to.
