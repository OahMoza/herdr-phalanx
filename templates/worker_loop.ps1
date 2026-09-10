# Herdr Phalanx Agent Bus Worker Loop -- single round.
# Atomically claims one Message for the given Route, copies its payload into a
# structured prompt and forwards the prompt to a waiting Herdr TUI Worker via
# `herdr agent prompt`, then exits. The TUI Worker is responsible for the
# rest of the lifecycle (heartbeat, complete/fail/cancelled via agent_bus.py).
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
    [Parameter(Mandatory = $true)][string]$PaneId,
    [Parameter(Mandatory = $true)][string]$TabId,
    [int]$LeaseSeconds = 300,
    [string]$DbOverride,
    [string]$ArtifactsOverride
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dbScript = Join-Path $root "db/agent_bus.py"
$preamblePath = Join-Path $root "templates/agent_bus_worker_preamble.md"

if ($DbOverride) { $env:AGENT_BUS_DB = $DbOverride }
if ($ArtifactsOverride) { $env:AGENT_BUS_ARTIFACTS = $ArtifactsOverride }

$preamble = Get-Content -LiteralPath $preamblePath -Raw

function Invoke-Bus([string[]]$Arguments) {
    $json = & python $dbScript @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "agent_bus command failed: $json" }
    if (-not $json) { return $null }
    return ($json | ConvertFrom-Json)
}

function Get-StringField($obj, $name) {
    if ($null -eq $obj) { return "" }
    $prop = $obj.PSObject.Properties[$name]
    if ($null -eq $prop) { return "" }
    return [string]$prop.Value
}

$claimArgs = @("claim", "--worker-id", $WorkerId, "--agent-kind", $AgentKind,
    "--lease-seconds", "$LeaseSeconds")
if ($Profile) { $claimArgs += @("--profile", $Profile) }
$claim = Invoke-Bus $claimArgs
if (-not $claim -or -not $claim.id) {
    Write-Output '{"claimed":false,"reason":"no pending message or route full"}'
    exit 0
}

$messageId = $claim.id
$leaseId = $claim.lease_id
$attempt = $claim.attempts

$payload = $claim.payload
if (-not $payload) { $payload = @{} }
$payloadJson = $payload | ConvertTo-Json -Compress -Depth 6

$promptLines = @(
    $preamble.TrimEnd(),
    "",
    "## Message",
    "- correlation_id: $messageId",
    "- lease_id: $leaseId",
    "- attempt: $attempt",
    "- route: $AgentKind$(if ($Profile) { "/$Profile" })",
    "",
    "## Payload",
    $payloadJson
)
$prompt = ($promptLines -join "`n")

& herdr agent prompt $AgentName $prompt | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "herdr agent prompt failed for $AgentName"
}

& python $dbScript worker-heartbeat --worker-id $WorkerId | Out-Null

Write-Output (ConvertTo-Json -Compress -Depth 4 @{
    claimed     = $true
    message_id  = $messageId
    lease_id    = $leaseId
    worker_id   = $WorkerId
    agent_name  = $AgentName
    pane_id     = $PaneId
    tab_id      = $TabId
    attempt     = $attempt
})
