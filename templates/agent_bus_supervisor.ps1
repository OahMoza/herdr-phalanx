# Herdr Phalanx Agent Bus Supervisor -- thin adapter.
# One short idempotent pass: reap expired leases, observe Route status,
# then claim and deliver one pending Message to a named TUI Worker. Exits
# after the pass. All real work delegates to `db/agent_bus.py`:
#   * `reap`                    -- reclaim expired leases
#   * `route-status`            -- report per-Route backlog (best effort)
#   * `worker-loop` (via the new CLI subcommand) -- claim + deliver + heartbeat
#
# Required environment:
#   HERDR_ENV=1
#   AGENT_BUS_DB          (optional)
#   AGENT_BUS_ARTIFACTS   (optional)
param(
    [Parameter(Mandatory = $true)][string]$WorkerId,
    [Parameter(Mandatory = $true)][string]$AgentName,
    [Parameter(Mandatory = $true)][string]$AgentKind,
    [string]$Profile,
    [string]$DbOverride,
    [string]$ArtifactsOverride
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dbScript = Join-Path $root "db/agent_bus.py"

if ($DbOverride) { $env:AGENT_BUS_DB = $DbOverride }
if ($ArtifactsOverride) { $env:AGENT_BUS_ARTIFACTS = $ArtifactsOverride }

function Invoke-BusQuiet([string[]]$Arguments) {
    try {
        $json = & python $dbScript @Arguments 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        return ($json | ConvertFrom-Json)
    } catch {
        return $null
    }
}

$reap = Invoke-BusQuiet @("reap")
$status = Invoke-BusQuiet @("route-status")

$loopArgs = @("worker-loop",
    "--worker-id", $WorkerId,
    "--agent-kind", $AgentKind,
    "--agent-name", $AgentName)
if ($Profile) { $loopArgs += @("--profile", $Profile) }

$delivery = Invoke-BusQuiet $loopArgs

Write-Output (ConvertTo-Json -Compress -Depth 4 @{
    reaped   = if ($reap) { $reap } else { @{ reaped = 0 } }
    routes   = $status
    delivery = if ($delivery) { $delivery } else { @{ claimed = $false } }
})
