# Herdr Phalanx

> 在 Herdr 终端多路复用器里，把多个 AI coding agent 编成 **2×2 田字方阵**协同作战的编排型 Skill。

**Phalanx（重步兵方阵）**：古希腊士兵把盾拼在一起结阵推进，单个人不强、结阵无解——正如单个 AI agent 能力有限，多个 agent 按田字格编队、分工协同后能覆盖架构/开发/测试/运维全流程。

---

## 核心特性

### 1. 田字格分屏拓扑（Grid Topology）

- 同一 workspace 内，每个「分身 tab」**最多 4 个 agent**，固定 **2×2 田字布局**
- 第 5 个分身自动开新 tab，每满 4 个翻一屏
- dispatcher（Hermes）独占「指挥 tab」，不混进分身田字
- 3 刀增量切分算法（已用 `pane layout` 几何坐标实测验证等宽等高）：
  ```
  root ─down─→ 上下两行 ─right(上)─→ 左上/右上 ─right(下)─→ 左下/右下
  ```

### 2. 开放本体论（Open Ontology）

- Agent 数量和 Role 集合**都不固定**，运行时通过 `assign(role, agent)` 动态绑定
- 5 类实体（Role / Agent / Workspace / Task / WikiArticle）+ 4 类关系 + 硬约束
- 员工表是快照不是定义，真正的扩展点集中在 `Upgrade Hooks`

### 3. 任务编排模式

- **任务状态机**：pending → running → blocked → done / failed，禁止跳变
- **任务 DAG**：并行独立 / 依赖链 / 扇出-扇入三种模式
- **决策门**：设计评审 → 实现验收 → QA 验证 → 集成测试 → 冒烟验证
- **协调器循环**：8 步扫描-派活循环，自动处理超时/blocked/failed
- **升级机制**：Level 0-4 五级（正常执行 → 自动回退 → 换高级 agent → 拆分任务 → 问用户）

### 4. WikiSkill 自演化（三层模型）

基于 Google Research arXiv 2608.27454（WikiSkill, 2026-08-27）：

- **Raw Layer**（`runs/`）：执行 trace，append-only，不进版本控制
- **Wiki Layer**（`wiki/`）：蒸馏后的知识条目，失败经验也保留
- **Skill Layer**（`SKILL.md`）：当前生效的程序，可回滚

四步循环：Inference → WikiMaintainer → SkillProposer → GatingReviewer，每跑 N 次任务 skill 持续变好且可回滚。

### 5. 已验证的 Agent 员工

| Agent | Kind | 状态 | 免审批参数 |
|---|---|---|---|
| omp (Oh My Pi) | `omp` | ✅ verified 持续对话 | `--auto-approve` |
| claudecode | `claude` | ✅ verified 持续对话 | `--permission-mode bypassPermissions` |
| hermes-coding | `hermes --profile coding` | ✅ verified | 无需 bypass |
| hermes-testing | `hermes --profile testing` | ✅ verified | 无需 bypass |
| opencode | `opencode` | ⚠️ 链路通，待复验 | `--auto` |
| pi | `pi` | ⚠️ 走 `pane run pi -p`，不走 agent start | — |

> codex 因不稳定已从员工表移除（prompt 时 "Conversation interrupted" 退出）。

---

## 安装

本项目是单源维护：GitHub 仓库是唯一真实源，各 agent 的 skills 目录通过**符号链接**指向它。

### Windows（PowerShell，需管理员权限创建符号链接）

```powershell
# 1. 克隆仓库
git clone git@github.com:OahMoza/herdr-phalanx.git E:\WorkSpace\github\herdr-phalanx

# 2. 为各 agent 创建符号链接（指向同一仓库）
# 通用 .agents 目录
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.agents\skills\herdr-phalanx" -Target "E:\WorkSpace\github\herdr-phalanx"

# Pi
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.pi\agent\skills\herdr-phalanx" -Target "E:\WorkSpace\github\herdr-phalanx"

# OpenCode
New-Item -ItemType SymbolicLink -Path "$env:APPDATA\opencode\skills\herdr-phalanx" -Target "E:\WorkSpace\github\herdr-phalanx"

# Hermes
New-Item -ItemType SymbolicLink -Path "$env:LOCALAPPDATA\hermes\skills\herdr-phalanx" -Target "E:\WorkSpace\github\herdr-phalanx"
```

### Linux / macOS

```bash
git clone git@github.com:OahMoza/herdr-phalanx.git ~/herdr-phalanx
ln -s ~/herdr-phalanx ~/.agents/skills/herdr-phalanx
# 同理为 pi / opencode / hermes 创建链接
```

---

## 快速开始

在 Herdr TUI 内（`HERDR_ENV=1`），对 Hermes dispatcher 说：

> "用 herdr-phalanx 开一个 4 人田字团队：架构师(claudecode)、开发者(omp)、测试(hermes-testing)、运维(hermes-devops)，任务是给 X 模块加 Y 功能。"

Hermes 会自动：
1. 开指挥 tab + 分身 tab
2. 切田字格 4 个 pane
3. 按 slot 启动对应 agent（带免审批参数）
4. 进入协调器循环：拆任务 → 派活 → 收结果 → 决策门验收 → 升级/回退

---

## 目录结构

```
herdr-phalanx/
├── SKILL.md              # 核心 Skill 文件（本体论 + 编排模式 + 工作流 + Pitfalls）
├── README.md             # 本文件
├── LICENSE               # MIT
├── .gitignore            # 排除 runs/（本地执行 trace）
├── references/           # 浓缩事实卡
│   ├── herdr-cli-quickref.md   # herdr v0.8.2 命令速查（实测）
│   ├── pi-coding-agent.md      # Pi agent 核心事实
│   └── wikiskill-paper.md      # WikiSkill 论文浓缩
├── wiki/                 # Wiki Layer（蒸馏知识，append-only）
│   ├── getting-started.md
│   ├── grid-2x2-topology.md    # 田字格切分算法与实测
│   ├── agent-bypass-flags.md    # 各 agent 免审批参数
│   ├── claude-windows-193-fix.md
│   └── ...
└── runs/                 # Raw Layer（本地执行 trace，不进版本控制）
    └── <task-id>/raw.jsonl
```

---

## 兼容性

| 运行环境 | 状态 | 说明 |
|---|---|---|
| Herdr TUI | ✅ 原生 | 设计目标平台，所有 pane/agent 命令基于 herdr v0.8.2 |
| Hermes | ✅ 已验证 | dispatcher 角色，`os.walk(followlinks=True)` 可加载符号链接 |
| Pi | ✅ 符号链接 | 走 `pane run pi -p` 路线 |
| OpenCode | ✅ 符号链接 | `--auto` 免审批 |
| 非 Herdr 环境 | ⚠️ 部分兼容 | 本体论/任务编排/WikiSkill 通用；pane/agent 命令需替换为等价操作 |

---

## 版本

当前 **v0.5.0**。详见 `SKILL.md` 内的 changelog。

---

## License

[MIT](./LICENSE) © 2026 OahMoza
