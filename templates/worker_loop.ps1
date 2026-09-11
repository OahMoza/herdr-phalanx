# Herdr Phalanx Agent Bus Worker Loop -- thin adapter.
# Single round. Delegates prompt construction, claim, runner invocation,
# and the immediate heartbeat to the Python Herdr Adapter via
# `db/agent_bus.py worker-loop`. The TUI Worker the Adapter delivers to
# finishes the lifecycle (heartbeat / complete / fail / cancelled) using
# the same `db/agent_bus.py` CLI.
#
# Required environment:
#   HERDR_ENV=1
#   AGENT_BUS_DB           (optional override; default ~/.herdr-phalanx/agent-bus.db)
#   AGENT_BUS_ARTIFACTS    (optional override; default ~/.herdr-phalanx/runs/agent-bus)
param(
    [Parameter(Mandatory = $true)][string]$WorkerId,
    [Parameter(Mandatory = $true)][string]$AgentName,
    [Parameter(Mandatory = $true)][string]$AgentKind,
    [string]$Profile,
    [int]$LeaseSeconds = 300,
    [string]$DbOverride,
    [string]$ArtifactsOverride
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dbScript = Join-Path $root "db/agent_bus.py"

if ($DbOverride) { $env:AGENT_BUS_DB = $DbOverride }
if ($ArtifactsOverride) { $env:AGENT_BUS_ARTIFACTS = $ArtifactsOverride }

$args = @("worker-loop",
    "--worker-id", $WorkerId,
    "--agent-kind", $AgentKind,
    "--agent-name", $AgentName,
    "--lease-seconds", "$LeaseSeconds")
if ($Profile) { $args += @("--profile", $Profile) }

$json = & python $dbScript @args 2>&1
if ($LASTEXITCODE -ne 0) { throw "agent_bus worker-loop failed: $json" }
Write-Output $json
