# Herdr Phalanx — 事件驱动协调器循环参考脚本
# 替代旧版 sleep 5s + agent list 轮询
# 核心：用 herdr agent wait --until 阻塞等待，多 agent 并行用后台 job
# 用法：这是参考模板，dispatcher(Hermes) 在运行时按此逻辑执行，不一定逐行调用

param(
    [Parameter(Mandatory=$true)][string]$RunId,
    [int]$WaitTimeoutMs = 900000  # 15 分钟
)

$ErrorActionPreference = "Stop"
$dbScript = "E:\WorkSpace\github\herdr-phalanx\db\phalanx_db.py"
$preamble = Get-Content "E:\WorkSpace\github\herdr-phalanx\templates\worker_done_preamble.md" -Raw

function Invoke-Db($args) {
    & python $dbScript @args 2>&1 | ConvertFrom-Json
}

function Wait-AnyAgent($agentNames) {
    <# 并行等待多个 agent，返回第一个完成的 agent 名 #>
    $jobs = @()
    foreach ($name in $agentNames) {
        $jobs += Start-Job -ScriptBlock {
            param($name, $timeout)
            herdr agent wait $name --until idle,done,blocked --timeout $timeout 2>&1
        } -ArgumentList $name, $WaitTimeoutMs
    }
    $done = $jobs | Wait-Job -Any
    $result = $done | Receive-Job
    $jobs | Stop-Job -PassThru | Remove-Job -Force
    return $result
}

# === 协调器主循环 ===
while ($true) {
    # 1. 查 Run 状态，判断是否完成
    $runStatus = Invoke-Db @("run-status", "--run", $RunId)
    if ($runStatus.status -eq "completed" -or $runStatus.pending_tasks -eq 0 -and $runStatus.running_tasks -eq 0) {
        Write-Output "Run $RunId 完成: $($runStatus.completed_tasks)/$($runStatus.total_tasks) tasks"
        break
    }

    # 2. 查 ready 队列（依赖已满足的 pending task）
    $readyTasks = Invoke-Db @("task-ready", "--run", $RunId)

    # 3. 对每个 ready task 派活（按 Grid Topology 分配 pane）
    foreach ($task in $readyTasks) {
        # 3a. 按田字格规则分配 pane（见 SKILL.md Grid Topology 段）
        #     $paneId = Allocate-Pane -Workspace $workspaceId
        # 3b. 启动 agent（带 bypass 参数，见 P8）
        #     herdr agent start $agentName --kind $kind --pane $paneId -- --auto-approve
        # 3c. 写 dispatch 记录
        $disp = Invoke-Db @("dispatch-start", "--task", $task.id, "--agent-name", $agentName,
                             "--agent-kind", $kind, "--pane", $paneId, "--tab", $tabId)
        # 3d. 派活（注入 worker_done preamble + task spec），用 --wait 阻塞等完成
        $prompt = "$preamble`n`n## 任务`n$($task.spec)"
        herdr agent prompt $agentName $prompt --wait --timeout $WaitTimeoutMs 2>&1 | Out-Null
    }

    # 4. 收集当前 running 的 dispatch
    $runningDisps = Invoke-Db @("dispatch-list", "--run", $RunId) | Where-Object { $_.status -eq "running" }
    if ($runningDisps.Count -eq 0) {
        Write-Output "没有 running 的 dispatch，继续下一轮"
        continue
    }

    # 5. 事件驱动：等第一个完成的 agent（替代 sleep 轮询）
    $agentNames = $runningDisps | ForEach-Object { $_.agent_name }
    $completedAgent = Wait-AnyAgent $agentNames

    # 6. 读输出，解析 ## TASK_COMPLETE 标记
    $output = herdr agent read $completedAgent --source recent-unwrapped --lines 200 2>&1 | Out-String
    if ($output -match "## TASK_COMPLETE\s*\noutcome:\s*(\w+)\s*\nfiles_modified:\s*(\[.*?\])\s*\nsummary:\s*(.+)") {
        $outcome = $Matches[1]
        $files = $Matches[2]
        $summary = $Matches[3].Trim()
    } else {
        # 没解析到标记，降级为读 agent 状态判断
        $agentState = herdr agent get $completedAgent 2>&1 | ConvertFrom-Json
        $outcome = if ($agentState.status -eq "idle" -or $agentState.status -eq "done") { "succeeded" } else { "failed" }
        $files = "[]"
        $summary = "未检测到 TASK_COMPLETE 标记，agent 状态: $($agentState.status)"
    }

    # 7. 写 dispatch 完成记录
    $completedDisp = $runningDisps | Where-Object { $_.agent_name -eq $completedAgent }
    Invoke-Db @("dispatch-complete", "--dispatch", $completedDisp.id, "--outcome", $outcome,
                "--files", $files, "--summary", $summary) | Out-Null

    Write-Output "Agent $completedAgent 完成: outcome=$outcome"
    # 循环继续：查 ready 队列 → 派新活 → 等完成
}
