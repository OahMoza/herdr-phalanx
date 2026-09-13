---
name: herdr-phalanx
version: 0.8.0
description: "Orchestrate named coding agents inside a Herdr TUI workspace with Phalanx Run, Task, Dispatch, Gate, blocking-question, evidence, retry, and 2x2 topology rules. Use for multi-agent task decomposition, dependency-ordered direct dispatch, coordinator supervision, TASK_ASK/TASK_COMPLETE handling, or Herdr pane/team topology. Agent Bus is optional and separate; capability/model inventory belongs to herdr-runtime-init. Requires HERDR_ENV=1 for Herdr control commands."
platforms: [windows]
---

# Herdr Phalanx

兼容入口：在 Herdr 多路复用器内把多个 coding agent 组织成“项目团队”，由 Hermes 担任 dispatch / 协调者。

新安装按意图加载三个入口：

- 根 `SKILL.md`（`herdr-phalanx`）：默认的 Run / Task / Dispatch / Gate 编排与直接 Herdr 派发。
- `skills/herdr-runtime-init/SKILL.md`：发现本地 Agent、Profile、模型、调用强度与能力证据。
- `skills/herdr-agent-bus/SKILL.md`：仅在需要匿名 Worker 池、竞争消费、lease、DLQ 或可靠异步回投时加载。

Agent Bus 是高级可选基础设施，不是普通 Phalanx 工作流的必经阶段。

## 升级接口（Versioning）

```
version: 0.8.0
schema: herdr-phalanx.onto.v3
changelog:
  - 0.8.0: 三 Skill 意图边界 — Phalanx 默认直接编排，Runtime Init 独立，Agent Bus 降为高级可选
  - 0.7.4: 拆分 SKILL.md — 核心行为保留，拓扑规则/员工/Pitfalls 拆到 references/
  - 0.7.3: Agent Bus 分层重构（ADR 0003 + ADR 0004）— Core / Herdr Adapter / CLI adapter 三层
  - 0.7.2: 拆出 reader.ps1，新增 Agent Bus N:N 队列（issue #22）
  - 0.7.1: 明确 managed 与 raw-pane capability 区分，TASK_ASK 解析绑定 dispatch_id
  - 0.7.0: 建立单一 Coordinator 可靠执行闭环（Phalanx DB + 事件驱动循环）
  - 0.6.0: 外挂 sqlite 编排状态数据库（Phalanx DB），Run/Task/Dispatch 三层模型 + DAG
  - 0.5.0: 项目重命名 herdr-orchestrator → herdr-phalanx
  - 0.4.2: 分屏拓扑规则（Grid Topology：田字格 + 每 Tab 上限 4）定版实测
  - 0.4.0: 任务编排模式（状态机 / DAG / 决策门 / 协调器循环 / 升级机制）
  - 0.3.0: 跨 agent 通用化发布
  - 0.2.0: 融合 WikiSkill 三层模型
  - 0.1.0: 初始骨架
```

schema 字段升级时同步 bump `schema` 主版本号；只增实体/关系时 bump `version` 次版本号。

---

## 角色（Roles）

> 角色表是**快照**，不是定义；新角色在 `assign()` 时按需声明。

| Role             | 职责                                       | 默认 Agent 候选                       |
|------------------|--------------------------------------------|---------------------------------------|
| ProjectManager   | 拆任务、派活、收结果、对用户汇报            | **Hermes dispatcher（不进 pane）**     |
| SystemArchitect  | 设计目录结构、接口契约、技术选型            | `claudecode`, `omp`, `hermes-design`  |
| Developer        | 落地实现、写代码、改 bug                    | `claudecode`, `opencode`, `omp`, `pi`, `hermes-coding` |
| QA               | 跑测试、找证据、写验证报告                  | `claudecode`, `opencode`, `hermes-testing` |
| ResearchLead     | 调研外部资料、写发现文档                    | `hermes-research`, `omp`              |
| OpsLead          | 部署 / CI / 监控相关                        | `hermes-devops`, `omp`                |
| WikiMaintainer   | 把 Raw trace 提炼为 WikiArticle             | `hermes-research`, `omp`              |
| SkillProposer    | 读 Wiki + trace，提议一次 skill 更新       | `claudecode`, `pi`, `hermes-coding`   |
| GatingReviewer   | 在独立验证集上评估提议；不通过就回滚        | `hermes-testing`, `claudecode`        |

运行时通过 `assign(role, agent_name)` 建立绑定；解绑用 `release(role)`。

**PM 行的特殊性**：ProjectManager 默认由 Hermes 本体承担，**不是** pane 里的 agent。

---

## 约束（Constraints，硬规则）

- **C1**：只在 `HERDR_ENV=1` 下激活此 skill；否则拒绝执行控制命令。
- **C2**：所有 pane 拓扑变更默认 `--no-focus`，不抢用户焦点。
- **C3**：所有 ID 来自 JSON 响应，不靠记忆或示例。
- **C4**：不主动关闭未创建的 workspace/tab/pane/session。
- **C5**：不主动 `herdr server stop`，不杀 Herdr 主进程。
- **C6**：跨 pane/agent 通信只能通过 prompt 或 shared file，不直接共享内存。

### Herdr Safety Rules

- Run Herdr control commands only with `HERDR_ENV=1`. Before changing topology, check `herdr --version`, `herdr workspace list`, and `herdr agent list`; obtain workspace, tab, and pane IDs from command JSON, never examples.
- Preserve the 2x2 worker-grid invariant: at most four agents per worker tab, all splits use `--ratio 0.5 --no-focus`, and a fifth worker starts a new tab. Do not close workspaces, tabs, panes, or stop the Herdr server unless the user explicitly requests it.
- Use `herdr agent prompt` for a waiting managed Worker. Use `herdr pane run` only for an ordinary command in an explicit Pane; never use it against an existing non-caller Pane unless that Pane is focused first.
- Use blocking `herdr agent wait --until ...` or `herdr pane wait-output --match ...` with explicit timeouts, never sleep-polling. For multiple workers, wait in parallel with PowerShell jobs and `Wait-Job -Any`.
- Start managed workers with their required flags: OMP `-- --auto-approve`, Claude `-- --permission-mode bypassPermissions`, OpenCode `-- --auto`; Hermes profiles need no bypass flag. Pi is for stateless `pane run ... 'pi -p ...'` work, not `herdr agent start --kind pi`.

### Shell 环境

Shell 路径通过 `scripts/detect-shell.ps1` 动态检测，不硬编码。Windows 优先 PowerShell 7 (`pwsh`)，降级 PowerShell 5.1。OMP 的 `shellPath` 应设为检测到的路径。详见 `references/shell-conventions.md`。

---

## 工作流程

### 0. 初始化调度能力

需要盘点或刷新本机 Agent、Profile、模型、调用强度与验证证据时，加载 `skills/herdr-runtime-init/SKILL.md`。能力目录不等于正式派活。

### 1. 准备 pane（按 Grid Topology）

详见 `references/topology-rules.md`。核心：每 tab 最多 4 pane 田字格，超 4 开新 tab，dispatcher 独占指挥 tab。

### 2. 启动 agent

```bash
# Pi（stateless）
herdr pane run <pane_id> 'pi -p "..."'

# OMP / Claude / OpenCode（持续对话）
herdr agent start <name> --kind <kind> --pane <pane_id> -- <bypass>

# Hermes profile
herdr agent start <name> --kind hermes --pane <pane_id> -- --profile <name>
```

### 3. 派活

```bash
herdr agent prompt <name> "<preamble> <work>"
herdr agent wait <name> --until idle,done,blocked --timeout <ms>
```

### 4. 验收

```bash
$output = herdr agent read <name> | Out-String
python db/phalanx_db.py dispatch-complete-from-output --dispatch $id --text $output
```

只有解析并持久化的 Worker 报告构成业务证据。普通编排到此不需要 Agent Bus；明确需要竞争消费或异步 Worker 池时再加载 `skills/herdr-agent-bus/SKILL.md`。

---

## 员工 / 拓扑 / Pitfalls（按需加载）

| 内容 | 文件 | 何时加载 |
|---|---|---|
| 员工表 | `references/agent-roster.md` | 选 agent 时 |
| 拓扑规则 | `references/topology-rules.md` | 切 pane 时 |
| Pitfalls | `references/pitfalls.md` | 排错时 |

---

## Upgrade Hooks

> 所有后续扩展在这里追加。

- `agents:` — 新增员工。详见 `references/agent-roster.md`。
- `roles:` — 新增角色。
- `topologies:` — 新增团队原型。v0.4.2 起物理分屏拓扑强制走 `references/topology-rules.md`。
- `artifacts:` — Artifact 权威格式：不可变版本化文件 + Phalanx DB 元数据（identity, version, sha256, producer, status, Gate linkage）。详见 `references/artifact-dag-workflow.md`。
- `protocols:` — 跨 agent 通信协议。
- `evolution:` — WikiSkill 自演化协议。
