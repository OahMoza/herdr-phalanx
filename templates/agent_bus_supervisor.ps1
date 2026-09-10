# Herdr Phalanx Agent Bus Supervisor.
# One short, idempotent pass: reap expired leases, report Route status, then
# attempt to claim and deliver one pending Message to a named TUI Worker.
# Exits after the pass. Invoke on a schedule, by hand, or from another script.
#
# Required environment:
#   HERDR_ENV=1
#   AGENT_BUS_DB          (optional)
#   AGENT_BUS_ARTIFACTS   (optional)
param(
    [Parameter(Mandatory = $true)][string]$WorkerId,
    [Parameter(Mandatory = $true)][string]$AgentName,
    [Parameter(Mandatory = $true)][string]$AgentKind,
    [Parameter(Mandatory = $true)][string]$PaneId,
    [Parameter(Mandatory = $true)][string]$TabId,
    [string]$Profile,
    [string]$DbOverride,
    [string]$ArtifactsOverride
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dbScript = Join-Path $root "db/agent_bus.py"
$workerLoop = Join-Path $root "templates/worker_loop.ps1"

if ($DbOverride) { $env:AGENT_BUS_DB = $DbOverride }
if ($ArtifactsOverride) { $env:AGENT_BUS_ARTIFACTS = $ArtifactsOverride }

function Invoke-Bus([string[]]$Arguments) {
    $json = & python $dbScript @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "agent_bus command failed: $json" }
    if (-not $json) { return $null }
    return ($json | ConvertFrom-Json)
}

function Invoke-BusQuiet([string[]]$Arguments) {
    try {
        $json = & python $dbScript @Arguments 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        if (-not $json) { return $null }
        return ($json | ConvertFrom-Json)
    } catch {
        return $null
    }
}

$reap = Invoke-BusQuiet @("reap")
$status = Invoke-BusQuiet @("route-status", "--table")

$loopArgs = @{
    WorkerId = $WorkerId
    AgentName = $AgentName
    AgentKind = $AgentKind
    PaneId = $PaneId
    TabId = $TabId
}
if ($Profile) { $loopArgs["Profile"] = $Profile }
if ($DbOverride) { $loopArgs["DbOverride"] = $DbOverride }
if ($ArtifactsOverride) { $loopArgs["ArtifactsOverride"] = $ArtifactsOverride }

$delivery = & $workerLoop @loopArgs 2>&1 | Out-String
$deliveryJson = $delivery.Trim()

Write-Output (ConvertTo-Json -Compress -Depth 4 @{
    reaped     = if ($reap) { $reap } else { @{ reaped = 0 } }
    routes     = $status
    delivery   = if ($deliveryJson) { ($deliveryJson | ConvertFrom-Json) } else { @{ claimed = $false } }
})
