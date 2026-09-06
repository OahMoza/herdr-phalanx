# Herdr CLI Quick Reference

> 本 skill 所有 herdr 命令的事实基础。最后核实：2026-09-06，herdr v0.8.2。已安装的 `herdr --skill` 是当前命令语法的权威来源。

## 版本与安装

- 本机版本：herdr 0.8.2
- 官网：herdr.dev
- GitHub：edheltzel/herdr

## 核心概念

- **Workspace**：项目层，ID 形态 `wN`（如 w1）
- **Tab**：workspace 内的标签页
- **Pane**：终端面板，ID 形态 `<workspace>:p<N>`（如 w1:p3）或纯 pane_id
- **Agent**：pane 里跑着的 coding agent 进程，有唯一 live name
- **Lifecycle 状态**：`idle` / `working` / `blocked` / `unknown`

## Agent Kinds（herdr agent start --help 实测）

```
pi, claude, codex, gemini, cursor, devin, agy, cline, omp, mastracode,
opencode, copilot, kimi, kiro, droid, amp, grok, hermes, kilo, qodercli,
qwen, maki
```

`-- <agent-args...>` 后的参数透传给 agent 二进制。

## 常用命令

### Workspace

```bash
herdr workspace list              # 列出所有 workspace
herdr workspace create <name>     # 创建 workspace
```

### Tab（v0.8.2 实测）

```bash
herdr tab list --workspace <wN>   # 列出某 workspace 的 tab
herdr tab create --workspace <wN> --cwd <path> --no-focus
# 返回 .result.root_pane.pane_id（新 tab 的初始 pane）和 .result.tab.tab_id
herdr tab focus <tab_id>          # 聚焦 tab（注意：不接受 --no-focus）
herdr tab close <tab_id>          # 关闭 tab（其内 pane 一并回收）
herdr tab rename <tab_id> <label>
```

### Pane

```bash
# 完整 split 语法（v0.8.2 实测）：[PANE_ID] 为位置参数=被切的源 pane
herdr pane split <源PANE_ID> --direction right|down --ratio 0.5 --cwd "$PWD" --no-focus
# 返回 JSON，新 pane_id 在 .result.pane.pane_id；源 pane 保留并收缩
# --ratio <FLOAT>：切分比例（田字格用 0.5 均分）；--direction 仅 right/down

herdr pane focus <pane_id>        # 把 pane 设为 caller（focus 需 --direction，见 P4）
herdr pane layout --pane <id>     # 看该 pane 所在 tab 的几何（rect 坐标 + splits 树）
herdr pane list                   # 列出所有 pane（含 tab_id/agent/agent_status/cwd）
herdr pane current                # 当前 caller pane
herdr pane close <pane_id>        # 关闭 pane
herdr pane run <pane_id> '<cmd>'  # 在指定 pane 执行命令（注意 P4：非 caller 不可靠）
herdr pane wait-output <pane_id>  # 等待 pane 输出
herdr pane read <pane_id>         # 读 pane 终端文本
```

### Agent

```bash
herdr agent start <name> --kind <kind> --pane <pane_id> [-- <args>]
# 启动阻塞时返回 agent_not_ready，名字仍有效
# --timeout <MS>：等待就绪（默认 30000，最大 300000）

herdr agent wait <name> --until idle   # 等 agent 就绪
herdr agent prompt <name> "<text>" --wait --timeout <MS>  # 派活
herdr agent read <name>                  # 读 agent 输出
herdr agent get <name>                   # 查 agent 状态
herdr agent list                         # 列出所有 agent
```

### Integration

```bash
herdr integration install <kind>   # 安装指定 agent 的 integration（lifecycle hooks）
# 已确认：pi / omp / claude code / codex / opencode / hermes / qoder 有 direct integration
```

## 关键约束（实测沉淀）

1. **控制面选择**：对等待任务的受管 Worker 使用 `herdr agent prompt`。对显式 Pane 中的普通命令使用 `herdr pane run`；在现有非 caller Pane 上运行前，先 focus 该 Pane。
2. **agent start 超时 30s**：名字会被回收，不是 agent_not_ready 状态，需要重新 start。
3. **所有 pane 拓扑变更默认 --no-focus**：不抢用户焦点。
4. **ID 来自 JSON 响应**：不靠记忆或示例。
5. **不主动 close pane / stop server**：违反 C4/C5。

## State Authority（herdr 官方文档）

| Agent | State authority | Integration role |
|---|---|---|
| Pi | lifecycle hooks when installed; otherwise screen manifest | state and session |
| OMP | lifecycle hooks when installed | state and session |
| Claude Code | screen manifest | session |
| Codex | screen manifest | session |
| GitHub Copilot CLI | screen manifest | session |

> 未安装 integration 时，herdr 用 screen manifest 检测 idle，可能存在 TUI 提示行格式不匹配导致超时的问题。

## Phalanx DB 编排状态数据库（v0.6.0 新增）

herdr 原生没有任务编排层（Run/Task/Dispatch/DAG/worker_done），Phalanx skill 用 sqlite 外挂实现。

- **DB 路径**：默认 `~/.herdr-phalanx/phalanx.db`，环境变量 `PHALANX_DB` 覆盖
- **Schema**：`db/schema.sql`（6 表 2 view）
- **CLI**：`python db/phalanx_db.py <command>`

### 常用命令

```bash
python db/phalanx_db.py init-db
python db/phalanx_db.py run-create --objective "..." --workspace w1
python db/phalanx_db.py run-status --run <id>
python db/phalanx_db.py task-add --run <id> --spec "..." --deps <task_id> --role Developer
python db/phalanx_db.py task-ready --run <id>
python db/phalanx_db.py task-claim --task <id> --coordinator <name> --kind omp --agent-name dev1 --pane w1:p3
python db/phalanx_db.py dispatch-complete-from-output --dispatch <id> --coordinator <name> --text "<worker output>"
python db/phalanx_db.py dispatch-block --dispatch <id> --coordinator <name> --state settled --reason "missing report" --evidence '{}'
python db/phalanx_db.py event-log --run <id> --limit 20
```

### 事件驱动等待（替代 sleep 轮询）

```bash
herdr agent wait <name> --until idle,done,blocked --timeout <MS>
herdr pane wait-output <pane_id> --match "## TASK_COMPLETE" --timeout <MS>
```

多 agent 并行等待用 PowerShell `Start-Job` + `Wait-Job -Any`，参考 `templates/coordinator_loop.ps1`。
