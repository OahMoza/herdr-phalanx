# Pitfalls（踩过的坑，写给未来的自己）

## P1：Hermes dispatcher ≠ pane 里的 agent

把"Hermes"写进员工表 / 把它当成 `herdr agent start --kind hermes` 启动目标，是这个 skill 最常犯的错。

- Hermes 是 skill 的执行者（dispatcher），永远不进 Herdr pane。
- 它不能被 `herdr agent prompt` 调用，也不能用 `herdr agent start` 启动。
- 在角色表里，PM 行可以默认由 Hermes 承担，但这一栏**描述 dispatcher 角色**而不是 Agent 员工。
- 员工表的 kind 必须是真实存在的 agent 二进制（pi / omp / claude / opencode / hermes-profile / 未来新增 kind）。Hermes 不是 kind。

修复检查：每当要填 `## 员工` 表格或写 `herdr agent start ... --kind X` 时，问一次"X 是 Herdr pane 里能跑的进程吗？"——是 → 写；不是 → 别写。

## P2：填具体 kind / 加新员工前，先 web_search 确认实体

不要凭印象写"pi 就是 Hermes / Codex 衍生品"之类。Agent 名字第一次出现时，按这个顺序确认：

1. `web_search "<name>" coding agent CLI`（带具体关键词过滤）
2. 拉官网 + GitHub，确认：kind、二进制名、是否需要安装、运行模式（TUI / print / RPC / SDK）
3. 把 condensed 事实沉淀到 `references/<name>.md`，下次直接引用
4. 然后再填员工表 + 在 `## Upgrade Hooks → agents:` 登记

不搜就填 = 猜测 = 用户会立刻打回。

## P3：快照不是定义

`## 角色` 和 `## 员工` 表格是**当前快照**，会过时。新增 / 调整时改两个地方：

1. 表格里直接补一行
2. `## Upgrade Hooks` 的 `roles:` / `agents:` 段追加注册项

漏掉 `## Upgrade Hooks` = 这个快照进不了本体论，下次版本 bump 时会丢。

## P4：`pane run --pane <非 caller>` 在本机不可靠

2026-09-04 实测：`herdr pane run w1:p4 'pi -p "..."'` 没有把命令发到 w1:p4，而是注入了 caller pane（w1:p3 / dispatcher pane）。后果：

- dispatcher pane 被 hijack，本会话 terminal 工具被阻塞（连续 `[Command interrupted]` / exit 130）
- 没法用 `Ctrl+C` 自动恢复，只能用户在 Herdr UI 手动按

修复规则（升级版 `## 最小可用工作流 → 步骤 1`）：

1. 想让命令跑在**新 pane** → 必须先 `herdr pane split --current --direction <dir> --cwd "$PWD" --no-focus` 拿新 pane ID，**再**用 `pane run <新 pane ID>`。不要跳过 split。
2. 想让命令跑在**已存在的非 caller pane** → 必须先 `herdr pane focus <pane_id>` 把它变 caller（或用 `--current` 在 caller 上跑），否则会被 hijack 到当前 caller pane。
3. 想启动 agent → 走 `herdr agent start <name> --kind <k> --pane <id>`，**不要**用 `pane run <id> <agent 命令>` 绕路（agent start 自带 agent 识别和 lifecycle 跟踪）。
4. 启动阻塞 → `agent wait <name> --until idle`；启动 30s 超时 → 名字会被回收（不是 `agent_not_ready` 状态），需要重新 start。

**禁止动作**：在没有先 split/focus 的前提下，对非 caller pane 用 `pane run` 发任何命令。

## P5：agent 启动冒烟测试状态

本 skill 列出的 10 个独立 agent 中，**4 个已 verified**（2026-09-04 冒烟：`pane split → agent start idle → prompt done → read 真输出` 整链路通过）：

- `omp` (kind=omp) — 已 verified（持续对话首选）
- `claudecode` (kind=claude, Opus 4.8 1M) — 已 verified（3s 返回正确回复；修复过程见 P9）
- `hermes-coding` (kind=hermes, LongCat-2.0) — 已 verified（24s 返回正确回复）
- `hermes-testing` (kind=hermes, MiniMax-M2.7) — 已 verified（18s 返回正确回复）

其余 6 个状态：

- `pi` — 不可走 agent start（见 P7，走 pane run）
- `opencode` — 启动成功+prompt 完成（状态 done），**链路通过**，但 agent read 未抓到文本，待复验
- `hermes-design` / `hermes-devops` / `hermes-research` / `hermes-default` — 未测（与 coding/testing 同属 hermes profile，链路应相同，但需逐个验证）

修复路线（按优先级）：
1. `omp` / `claudecode` / `hermes-coding` / `hermes-testing` 已 verified → 持续对话任务优先派给这 4 个
2. `pi` 走 pane run 路线（stateless / 一次性任务，详见 P7）
3. `opencode` 链路已通，可谨慎使用，read 问题待复验
4. `hermes-design` / `hermes-devops` / `hermes-research` / `hermes-default` — 每次用之前先单独冒烟
5. **冒烟前必带 bypass 参数**（详见 P8）

## P6：wiki ≠ skill（WikiSkill 三层模型的关键约束，已启用）

启用 WikiSkill（Google Research arXiv 2608.27454，详见 `references/wikiskill-paper.md`）后，本 skill 同时存在两类内容载体：

- **WikiArticle**（wiki/<slug>.md）— append-only。失败经验也保留。每次蒸馏只新增，不删、不改历史版本。
- **Skill**（skill 内容本身）— 可回滚。分下降就回退到上一版本。

混用会导致：
1. 把失败 trace 当成 skill 改正的依据 → skill 反而学坏
2. 想"清理一下 wiki" → 抹掉失败经验，下次 proposer 重蹈覆辙
3. 想"重写一遍 skill 起点" → 找不到为什么当初这么写

判定规则：
- **问"该不该这么做"** → 查 WikiArticle（事实库）
- **问"现在该怎么做"** → 看 Skill（程序库）
- **Skill 每次更新** → 在 changelog 里写明它 derived_from 哪条 WikiArticle
- **任何时候 wiki 都不能"清空"或"重写历史条目"**；只能新增版本或新增条目

## P7：Pi 走 pane run 路线，不走 agent start（实测沉淀）

- **`herdr agent start --kind pi` 不可靠**：60s 超时，herdr 收不到 Pi TUI 的 lifecycle signal，名字被回收
- **`herdr pane run <独立 pane> 'pi -p "..."'` 可用**：6.3s 返回，dispatcher pane 未受影响

任务分配规则：
- **stateless / 一次性任务** → 给 Pi，走 `pane run pi -p`
- **持续对话 / 多轮 prompt** → 给 `omp`（已 verified）
- **禁止**用 `herdr agent start --kind pi` 跑生产

## P8：持续对话型 agent 必须带 bypass 权限参数

- **omp**：`--auto-approve`（或 `--approval-mode yolo`）
- **claudecode**：`--permission-mode bypassPermissions`
- **opencode**：`--auto`
- **hermes profile**：**不需要 bypass 参数**
- **pi**：无专门 auto-approve 参数；生产走 `pane run pi -p`

**重要架构约束**：herdr 采用多 pane 隔离架构，dispatcher（Hermes）**不能**替其他 pane 里的 agent 点击审批弹窗——每个 agent 的审批框只出现在它自己的 pane 里。因此开团队时**必须**在启动每个 agent 时就带上对应的免审批参数。

## P9：Windows 上 npm 全局装的 agent，herdr Start-Process 会报 193（claudecode 实测修复）

**症状**：`herdr agent start cc --kind claude` 超时，PowerShell 报 Win32 错误码 193。

**两层根因，必须都修**：

1. **native binary 缺失**：真正的 ~208MB 二进制在 optionalDependency 里。修复：`npm install -g @anthropic-ai/claude-code --include=optional`
2. **npm shim 干扰**：npm 全局安装会在 PATH 目录生成 4 个入口（无扩展名 / .cmd / .ps1 / .exe）。herdr 的 `Start-Process -FilePath claude` 在同目录 .cmd 与 .exe 并存时误选 .cmd → 193。修复：只留 `claude.exe`，其余改名 .bak。

**通用规律**：herdr 的 agent 启动走 `Start-Process -FilePath <kind 名>`，**最稳的形态是 PATH 目录里有一个与 kind 同名的原生 `.exe`**。
