# Herdr Phalanx Coordinator adapter.
# Herdr controls Agent processes. Phalanx CLI owns all Run, Task, Dispatch, and Event state.
param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [Parameter(Mandatory = $true)][string]$Coordinator,
    [Parameter(Mandatory = $true)][string]$AgentName,
    [Parameter(Mandatory = $true)][string]$AgentKind,
    [Parameter(Mandatory = $true)][string]$PaneId,
    [string]$TabId,
    [string]$Profile,
    [int]$WaitTimeoutMs = 900000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dbScript = Join-Path $root "db/phalanx_db.py"
$preamblePath = Join-Path $root "templates/worker_done_preamble.md"
$preamble = Get-Content -LiteralPath $preamblePath -Raw

function Invoke-Phalanx([string[]]$Arguments) {
    $json = & python $dbScript @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Phalanx command failed: $json" }
    return $json | ConvertFrom-Json
}

function ConvertTo-Evidence([hashtable]$Value) {
    return $Value | ConvertTo-Json -Compress -Depth 4
}

function Block-Dispatch($DispatchId, $State, $Reason, $Output) {
    $evidence = ConvertTo-Evidence @{ agent = $AgentName; state = $State; output = $Output }
    Invoke-Phalanx @("dispatch-block", "--dispatch", $DispatchId, "--coordinator", $Coordinator,
        "--state", $State, "--reason", $Reason, "--evidence", $evidence) | Out-Null
}

# This adapter uses an already started, waiting managed Agent. Start it with herdr agent start
# before invoking this script, then use this adapter to claim, prompt, wait, read, and persist.
while ($true) {
    $run = Invoke-Phalanx @("run-status", "--run", $RunId)
    if ($run.status -ne "active" -or ($run.pending_tasks -eq 0 -and $run.running_tasks -eq 0)) { break }

    $readyTasks = Invoke-Phalanx @("task-ready", "--run", $RunId)
    if (-not $readyTasks) { break }

    foreach ($task in $readyTasks) {
        $claimArgs = @("task-claim", "--task", $task.id, "--coordinator", $Coordinator,
            "--kind", $AgentKind, "--agent-name", $AgentName, "--pane", $PaneId)
        if ($TabId) { $claimArgs += @("--tab", $TabId) }
        if ($Profile) { $claimArgs += @("--profile", $Profile) }
        $claim = Invoke-Phalanx $claimArgs

        $prompt = "$preamble`n`n## Task`n$($task.spec)"
        herdr agent prompt $AgentName $prompt
        if ($LASTEXITCODE -ne 0) {
            Block-Dispatch $claim.dispatch.id "blocked" "agent prompt failed" ""
            continue
        }

        $waitOutput = herdr agent wait $AgentName --until idle,done,blocked,unknown --timeout $WaitTimeoutMs 2>&1 | Out-String
        $timedOut = $LASTEXITCODE -ne 0
        $output = herdr agent read $AgentName 2>&1 | Out-String
        $agent = herdr agent get $AgentName 2>&1 | ConvertFrom-Json
        $state = $agent.status

        if ($timedOut) {
            Block-Dispatch $claim.dispatch.id "timeout" "timeout after output inspection" $output
        } elseif ($state -eq "blocked") {
            Block-Dispatch $claim.dispatch.id "blocked" "Herdr reported blocked" $output
        } elseif ($state -eq "unknown") {
            Block-Dispatch $claim.dispatch.id "unknown" "Herdr reported unknown" $output
        } else {
            try {
                Invoke-Phalanx @("dispatch-complete-from-output", "--dispatch", $claim.dispatch.id,
                    "--coordinator", $Coordinator, "--text", $output) | Out-Null
            } catch {
                Block-Dispatch $claim.dispatch.id "settled" "missing valid TASK_COMPLETE report" $output
            }
        }
    }
}
