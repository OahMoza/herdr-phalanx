# Herdr Phalanx Dispatch Reader.
# A background worker for one Dispatch. The Coordinator launches it without waiting and it
# blocks on Herdr lifecycle on its own. It then parses the Worker output through the Phalanx CLI
# and persists the result. It never calls dispatch-block for blocked/unknown on its own: those
# states are recorded as evidence-backed blocked via the Coordinator-owned `dispatch-block`
# command so the Run owner remains the single writer.
param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$Coordinator,
    [Parameter(Mandatory = $true)][string]$AgentName,
    [Parameter(Mandatory = $true)][string]$DispatchId,
    [Parameter(Mandatory = $true)][string]$TabId,
    [Parameter(Mandatory = $true)][string]$PaneId,
    [int]$WaitTimeoutMs = 900000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dbScript = Join-Path $root "db/phalanx_db.py"

function Invoke-Phalanx([string[]]$Arguments) {
    $json = & python $dbScript @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Phalanx command failed: $json" }
    return $json | ConvertFrom-Json
}

function ConvertTo-Evidence([hashtable]$Value) {
    return $Value | ConvertTo-Json -Compress -Depth 4
}

function Block-Dispatch($State, $Reason, $Output) {
    $evidence = ConvertTo-Evidence @{ agent = $AgentName; state = $State; output = $Output }
    Invoke-Phalanx @("dispatch-block", "--dispatch", $DispatchId, "--coordinator", $Coordinator,
        "--state", $State, "--reason", $Reason, "--evidence", $evidence) | Out-Null
}

$waitOutput = herdr agent wait $AgentName --until idle --until done --until blocked --until unknown --timeout $WaitTimeoutMs 2>&1 | Out-String
$timedOut = $LASTEXITCODE -ne 0

$output = herdr agent read $AgentName 2>&1 | Out-String
$agent = (herdr agent get $AgentName 2>&1 | ConvertFrom-Json).result.agent
$state = $agent.agent_status

if ($timedOut) {
    Block-Dispatch "timeout" "timeout after output inspection" $output
} elseif ($state -eq "blocked") {
    Block-Dispatch "blocked" "Herdr reported blocked" $output
} elseif ($state -eq "unknown") {
    Block-Dispatch "unknown" "Herdr reported unknown" $output
} else {
    try {
        Invoke-Phalanx @("dispatch-complete-from-output", "--dispatch", $DispatchId,
            "--coordinator", $Coordinator, "--text", $output) | Out-Null
    } catch {
        try {
            Invoke-Phalanx @("dispatch-ask-from-output", "--dispatch", $DispatchId,
                "--coordinator", $Coordinator, "--text", $output) | Out-Null
        } catch {
            Block-Dispatch "settled" "missing valid TASK_COMPLETE report" $output
        }
    }
}
