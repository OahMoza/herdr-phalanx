# Herdr Phalanx

Herdr Phalanx 是一个 Windows 本地任务协调器。它在 Herdr 中运行多个编码智能体，并用 SQLite 保存每次任务运行、工作项、执行记录、智能体能力和事件证据。

它解决的问题不是“如何启动很多智能体”，而是“如何可靠地分配、跟踪、完成或阻塞工作”。

```text
任务协调器
  -> 任务运行
  -> 工作项
  -> 已验证执行智能体
  -> 执行记录
  -> 完成、重试或阻塞
```

## 适用范围

- 一台 Windows 机器。
- 一个 Herdr 会话。
- 每个正在运行的任务运行只有一个任务协调器。
- 受管执行智能体使用 Herdr 的 Agent 控制面。
- 明确的无状态命令可以使用 Pane 控制面。

不支持多机器写入、网络共享 SQLite、多个任务协调器同时写同一个任务运行。

## 核心概念

| 中文术语 | 英文名称 | 含义 |
|---|---|---|
| 任务协调器 | Coordinator | 唯一的状态写入者。它分配工作、读取输出、保存结果。 |
| 任务运行 | Run | 一次完整协作工作的命名空间。例如“修复登录并完成测试”。 |
| 工作项 | Task | 任务运行中的一项可重试工作。它可以有依赖和预期职责。 |
| 执行记录 | Dispatch | 一个执行智能体对一个工作项的一次具体尝试。重试会创建新的执行记录。 |
| 智能体 | Agent | Herdr Pane 中运行的进程。 |
| 执行智能体 | Worker | 正在执行某条执行记录的智能体。它不是永久员工记录。 |
| 职责 | Role | 工作项要求的责任，例如 `Developer`、`QA`、`Reviewer`。 |
| 能力证据 | Capability observation | 本机对 Agent 可用性的记录。它不是固定员工名单。 |
| 事件 | Event | 追加式审计记录，用于恢复和排查。 |

## 用户故事

### 作为任务协调器

- 我可以创建一个带身份的任务运行，因此只有我能修改这个任务运行的工作项和执行记录。
- 我可以安全发现本机 Agent 命令、配置档、版本和 Herdr 集成状态，而不自动启动所有 Agent。
- 我可以验证一个执行智能体，因此只有完成完整冒烟执行的智能体才会被分配真实工作。
- 我可以为一个依赖已完成的工作项选择符合职责的已验证执行智能体。
- 我可以原子地领取工作项、创建执行记录、增加重试次数并写入事件，因此同一个工作项不会被重复分配。
- 我可以在重启后从 SQLite 恢复任务运行、工作项、执行记录和能力证据。

### 作为执行智能体

- 我接收包含工作内容和完成协议的任务消息。
- 我完成工作后输出 `TASK_COMPLETE`，报告成功或失败、修改文件和摘要。
- 我需要帮助时可以输出 `TASK_ASK`，由任务协调器决定下一步。
- 我不直接写 SQLite，不直接修改工作项状态。

### 作为使用者

- 我可以看到哪个执行智能体处理了哪个工作项，以及它所在的 Pane 和 Tab。
- 我可以看到工作为何成功、失败、阻塞或需要重试。
- 我不会因为 Herdr 显示 `idle`、`done`、`unknown` 或超时，就得到一个没有证据的“成功”结果。
- 我可以继续使用 2x2 Worker 网格：每个 Worker Tab 最多四个执行智能体，第五个执行智能体使用新 Tab；任务协调器不进入 Worker 网格。

## 工作流程

```text
1. 任务协调器创建任务运行
2. 记录或查询本机智能体能力证据
3. 在隔离 Pane 中完成冒烟执行，获得已验证执行智能体
4. 创建带职责和依赖的工作项
5. 为依赖已满足的工作项原子领取一个已验证执行智能体
6. 向等待任务的受管执行智能体发送工作
7. 等待 Herdr 状态变化并读取输出
8. 解析 TASK_COMPLETE
9. 成功完成、失败重试，或以证据阻塞
10. 处理下一个依赖已满足的工作项
```

重要规则：Herdr 的 `idle` 和 `done` 只表示“现在应读取输出”。它们不表示工作项成功。只有已解析的 `TASK_COMPLETE` 才能正常完成执行记录。

## 安装

此项目是一个 Skill，不是包管理应用。它需要 Python 标准库和已安装的 Herdr。

```powershell
git clone git@github.com:OahMoza/herdr-phalanx.git $HOME\herdr-phalanx
Set-Location $HOME\herdr-phalanx
python db/phalanx_db.py init-db
```

默认数据库路径是 `~/.herdr-phalanx/phalanx.db`。测试和冒烟验证应使用独立路径：

```powershell
$env:PHALANX_DB = "$env:TEMP\phalanx-smoke.db"
python db/phalanx_db.py init-db
```

在控制 Herdr 前，必须设置 `HERDR_ENV=1`，并先检查当前资源：

```powershell
$env:HERDR_ENV = "1"
herdr --version
herdr workspace list
herdr agent list
```

不要关闭用户已有的 Workspace、Tab、Pane、Herdr session 或 Herdr server。

## 快速使用

以下示例只演示持久状态。真实 Worker 冒烟验证请使用隔离 Workspace，并按 [冒烟验证矩阵](references/coordinator-smoke-matrix.md) 执行。

### 1. 创建任务运行

```powershell
$run = python db/phalanx_db.py run-create `
  --objective "修复登录功能并运行测试" `
  --workspace w9 `
  --coordinator hermes-main | ConvertFrom-Json
$run.id
```

### 2. 记录能力并验证执行智能体

安全发现只能记录本机事实，不能把 Agent 直接标记为 `verified`：

```powershell
python db/phalanx_db.py capability-record `
  --kind omp `
  --profile local `
  --level discovered `
  --evidence '{"roles":["Developer"],"source":"local command discovery"}'
```

真实派工前，必须在隔离 Pane 中完成 managed Worker 冒烟执行，并使用 `smoke-verify-managed` 保存其 launch、readiness、output-read、完成报告和证据。只有这条完整路径会将能力标记为 `verified`。参数和检查项见 [冒烟验证矩阵](references/coordinator-smoke-matrix.md)。

查询当前能力：

```powershell
python db/phalanx_db.py capability-list --kind omp --profile local --current
```

### 3. 创建工作项和依赖

```powershell
$design = python db/phalanx_db.py task-add `
  --run $run.id `
  --coordinator hermes-main `
  --spec "确定登录接口的修改范围" `
  --role Developer | ConvertFrom-Json

$tests = python db/phalanx_db.py task-add `
  --run $run.id `
  --coordinator hermes-main `
  --spec "为登录修改增加回归测试" `
  --deps $design.id `
  --role QA | ConvertFrom-Json

python db/phalanx_db.py task-ready --run $run.id
```

### 4. 启动并使用受管执行智能体

先在隔离 Pane 中启动一个真实 Agent。所有 ID 必须来自 Herdr 的 JSON 输出。对等待任务的受管执行智能体使用 `agent prompt`。

```powershell
herdr agent start developer-1 --kind omp --pane <pane-id> -- --auto-approve
herdr agent wait developer-1 --until idle --timeout 300000
```

然后让任务协调器领取工作项。只有当 capability 是 `verified`，且其 `roles` 包含工作项要求的职责时，领取才会成功。

```powershell
$claim = python db/phalanx_db.py task-claim `
  --task $design.id `
  --coordinator hermes-main `
  --kind omp `
  --profile local `
  --agent-name developer-1 `
  --pane <pane-id> `
  --tab <tab-id> | ConvertFrom-Json
```

发送工作时，必须在提示中包含 [`templates/worker_done_preamble.md`](templates/worker_done_preamble.md) 的内容。

```powershell
herdr agent prompt developer-1 "<完成协议> <工作内容>"
herdr agent wait developer-1 --until idle,done,blocked,unknown --timeout 900000
```

### 5. 读取输出并完成或阻塞执行记录

```powershell
$output = herdr agent read developer-1 | Out-String

python db/phalanx_db.py dispatch-complete-from-output `
  --dispatch $claim.dispatch.id `
  --coordinator hermes-main `
  --text $output
```

如果没有有效 `TASK_COMPLETE`，或 Herdr 状态为 `blocked`、`unknown`、timeout，不要填写成功或失败。阻塞执行记录并保存证据：

```powershell
python db/phalanx_db.py dispatch-block `
  --dispatch $claim.dispatch.id `
  --coordinator hermes-main `
  --state settled `
  --reason "缺少有效完成报告" `
  --evidence '{"agent":"developer-1","output":"..."}'
```

任务协调器之后可以明确决定重试或确认失败：

```powershell
python db/phalanx_db.py dispatch-resolve-block `
  --dispatch $claim.dispatch.id `
  --coordinator hermes-main `
  --decision retry `
  --evidence '{"reason":"已检查输出，允许重试"}'
```

## Worker 完成协议

执行智能体完成工作后，必须在输出的最后给出：

```text
## TASK_COMPLETE
dispatch_id: <执行记录 ID>
outcome: succeeded
files_modified: ["src/login.py", "tests/test_login.py"]
summary: 完成了登录修复。发现了旧会话过期逻辑。没有剩余工作。
```

`outcome` 只能是 `succeeded` 或 `failed`。

`dispatch_id` 必须原样回显任务协调器消息中的执行记录 ID。这样同一个 Worker 的旧终端报告不能完成新的执行记录。

OMP 可能省略 `##`，或者输出不带引号的文件列表。Phalanx 解析器支持这些格式。

## 能力状态

| 状态 | 含义 | 可以领取真实工作吗 |
|---|---|---|
| `declared` | Herdr 声明支持该 Agent 类型。 | 不可以。 |
| `discovered` | 本机发现命令、配置档或集成。 | 不可以。 |
| `ready` | Herdr 已启动 Agent，且 Agent 可以交互。 | 不可以。 |
| `verified` | Worker 完成完整冒烟执行并给出有效完成报告。 | 可以。 |
| `degraded` | 最近的冒烟验证失败。 | 不可以。 |
| `unknown` | 没有足够证据。 | 不可以。 |

能力证据是当前机器的事实。`SKILL.md` 中的 Agent 和职责映射只是候选策略。`wiki/` 中的记录是历史证据，不能替代当前验证。

## Herdr 控制面规则

| 场景 | 使用方式 |
|---|---|
| 已启动、正在等待任务的受管执行智能体 | `herdr agent prompt` |
| 等待受管执行智能体状态变化 | `herdr agent wait --until ... --timeout ...` |
| 读取受管执行智能体输出 | `herdr agent read` |
| 显式 Pane 中的普通命令、测试或无状态任务 | `herdr pane run` |
| Worker 显示 `blocked` | 先读取输出，再决定。不要自动发送确认键。 |

执行智能体之间不直接写对方状态，也不直接写 SQLite。需要协作时，执行智能体向任务协调器报告；任务协调器再创建、更新或分配工作项。

## 2x2 Worker 网格

- 一个 Worker Tab 最多四个执行智能体。
- 所有 Pane 切分使用 `--ratio 0.5 --no-focus`。
- 第五个执行智能体创建新的 Worker Tab。
- 任务协调器保留在单独的指挥 Tab，不占 Worker 网格。
- 不主动关闭用户没有要求关闭的资源。

详细拓扑见 [`SKILL.md`](SKILL.md)。

## 验证

运行完整单元测试：

```powershell
python -m unittest db.tests.test_phalanx_db -v
```

当前自动化测试覆盖：

- 任务运行 owner 和重启恢复。
- 能力历史和当前能力查询。
- 受管 Worker 冒烟结果。
- 按职责的原子工作项领取。
- 标准和 OMP 格式的完成报告。
- 成功、失败重试和重复完成拒绝。
- 缺少报告、blocked、unknown、timeout 的证据化阻塞。
- 阻塞后的重试或确认失败。

真实 Herdr 冒烟验证必须在隔离 Workspace 中执行。检查项见 [`references/coordinator-smoke-matrix.md`](references/coordinator-smoke-matrix.md)。

## 项目文件

```text
herdr-phalanx/
├── README.md
├── SKILL.md
├── AGENTS.md
├── db/
│   ├── schema.sql
│   ├── phalanx_db.py
│   └── tests/test_phalanx_db.py
├── templates/
│   ├── coordinator_loop.ps1
│   └── worker_done_preamble.md
├── references/
│   ├── agent-capability-discovery.md
│   ├── coordinator-smoke-matrix.md
│   └── herdr-cli-quickref.md
└── wiki/                         # 追加式历史知识
```

## 版本和许可

当前版本：`0.7.0`。

[MIT License](./LICENSE) © 2026 OahMoza
