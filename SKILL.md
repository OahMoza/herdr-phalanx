---
name: herdr-phalanx
version: 0.6.1
description: "Use when orchestrating multiple coding agents inside a Herdr TUI workspace. Triggers: Herdr pane/agent management, multi-agent team setup, parallel coding work, dispatcher role, ontology-based team design, evolving role/agent registry, open-world role pool, grid topology / 2x2 phalanx pane layout, per-tab agent cap. Built on open ontology: agent count and role set are NOT fixed — both grow at runtime via assign(). Hermes is the dispatcher, NEVER a pane-internal agent. NOT for single-agent tasks, casual shell use, or anything outside Herdr (HERDR_ENV must be 1)."
platforms: [windows]
---

# Herdr Orchestrator

编排型 skill：在 Herdr 多路复用器内把多个 coding agent 组织成"项目团队"，由 Hermes 担任 dispatch / 协调者。

## 本体论（Ontology）

本 skill 把世界拆成 5 类实体 + 4 类关系 + 2 类约束。所有运行时决策都必须挂回到这棵本体树上。

**开放性约束（开放本体论，先于一切实体声明）：**

- **OO-1**：Agent 数量不固定。1 个也行，N 个也行，由运行时任务和可用 pane 决定。
- **OO-2**：Role 集合不固定。新角色可以临时出现，也可以用完即弃；不必预注册。
- **OO-3**：任何"当前注册 / 当前名单"段落都是**快照**，不是定义；真正的定义在 `## Upgrade Hooks` 的动态段。

### 实体（Entities）

1. **Role** — 角色层，描述"在团队中做什么"。
   - **不预注册**。Role 在 `assign(role, agent)` 调用时按需声明；同一个 Role 可以反复 instantiate。
   - 常见快照（仅供参考）：ProjectManager / SystemArchitect / Developer / QA / SRE / TechWriter / SecurityReviewer / ...
   - 角色 ≠ 人。一个 Role 可由多个 Agent 同时承担；一个 Agent 也可在不同上下文切换 Role。

2. **Agent** — 执行层，物理上是"Herdr pane 里跑着的 coding agent 进程"。
   - **数量按运行时需要**。员工表是快照，不是上限。
   - 当前快照：`pi`, `omp`, `claudecode`, `opencode`, 以及任意 hermes-profile 下属员工。
   - Agent 有 kind（pi / omp / claude / opencode / hermes-profile / 未来 kind）和唯一 live name。
   - **重要**：Hermes 本体（dispatcher / 编排者）**不是** Agent，也不占用任何 pane。任何把 Hermes 写进员工表的映射都是错的。

3. **Workspace** — 项目层，物理上是 `herdr workspace`，ID `wN`。
   - 承载一个项目或一组相关任务。

4. **Task** — 工作单元，单次 `agent prompt` 或 `pane run`。
   - 有 owner（Agent）、reviewer（Role，可选）、state（pending / running / blocked / done / failed）。

5. **WikiArticle** — 知识单元，WikiSkill 三层模型中的 Wiki Layer 一条条目。
   - 来源：Google Research arXiv 2608.27454（WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill Evolution, 2026-08-27）。
   - 内容：结构化条目，主题粒度（如"在 Herdr 里启动 hermes profile 的坑"），持续累积、按主题去重合并。
   - 状态：append-only（不删除，只新增版本）。这是 WikiSkill 的核心约束——失败经验也留底。
   - 与 Skill 的区别：WikiArticle 是"为什么这么做"和"踩过哪些坑"的事实库；Skill 是"现在该怎么做"的程序。Wiki 永远存在，Skill 可回滚。

### 关系（Relations）

- `Role ─assigned_to─→ Agent`：角色当前由哪个员工承担（运行时绑定，可换人）。
- `Agent ─occupies─→ Pane`：员工当前坐在哪个 pane。
- `Task ─owned_by─→ Agent`、`Task ─reviewed_by─→ Role`：任务的主责和验收角色。
- `Workspace ─contains─→ Tab ─contains─→ Pane`：物理拓扑，遵循 Herdr 原生结构。
- `Skill ─derived_from─→ WikiArticle`：当前 Skill Layer 的每个程序片段，挂回到至少一条 WikiArticle 作为事实支撑。这条关系让 skill 更新可追溯、可回滚时可定位原始证据。

### 约束（Constraints，硬规则）

- **C1**：只在 `HERDR_ENV=1` 下激活此 skill；否则拒绝执行控制命令。
- **C2**：所有 pane 拓扑变更默认 `--no-focus`，不抢用户焦点。
- **C3**：所有 ID 来自 JSON 响应，不靠记忆或示例。
- **C4**：不主动关闭未创建的 workspace/tab/pane/session。
- **C5**：不主动 `herdr server stop`，不杀 Herdr 主进程。
- **C6**：跨 pane/agent 通信只能通过 prompt 或 shared file，不直接共享内存。

### 升级接口（Versioning）

本 skill 设计为可演进。升级点全部集中在 `## Upgrade Hooks` 段落，运行时新增角色 / 员工 / 拓扑模式都从这里插入，不动本体论主干。版本号见 frontmatter 下方。

```
version: 0.6.1
schema: herdr-phalanx.onto.v1
changelog:
  - 0.6.1: 修复端到端测试发现的两个解析问题 + 新增 worker_done 解析 CLI + 30 个单元测试。①修复 parse_list_arg：omp 渲染输出 [a.py, b.py]（方括号无引号）之前被错误解析为 ['[a.py', 'b.py]']，现在正确解析为 ['a.py', 'b.py']；单元素 [string_utils.py] 之前变成 ['[string_utils.py]']，现在正确。②新增 parse_worker_done(text) 函数：兼容标准格式（## TASK_COMPLETE）、omp 渲染格式（去掉 ##、TASK_COMPLETE 与字段间有空行）、缺失字段降级、无标记时 parsed=false；端到端测试中 omp 输出的 TASK_COMPLETE 标记现在可正确解析。③新增 CLI 子命令 parse-worker-done（--text/--file，纯解析输出 JSON）和 dispatch-complete-from-output（--dispatch + --text/--file，解析后直接写库完成 dispatch），dispatcher 不再需要自己写正则。④新增 db/tests/test_phalanx_db.py：30 个 unittest 测试，覆盖 parse_list_arg（10 例）、parse_worker_done（11 例）、数据库操作（9 例，含 DAG 依赖自动计算/dispatch 重试/run 汇总统计/事件 append-only/解析+写库集成），全部通过（Ran 30 tests, OK）。
  - 0.6.0: 编排架构重大升级：外挂 sqlite 编排状态数据库（Phalanx DB），实现 Run/Task/Dispatch 三层模型 + 标准化任务清单 + DAG 依赖自动计算 + worker_done 外挂协议 + 事件驱动协调器循环。①新增 db/schema.sql（6 表 2 view：runs/tasks/dispatches/events/gates + ready_tasks/run_summary）和 db/phalanx_db.py CLI（18 个子命令，仅用 Python 标准库 sqlite3）；②Run/Task/Dispatch 三层模型借鉴 Orca orchestration，但完全基于 herdr 原生命令实现，herdr 只负责物理执行，Phalanx DB 负责编排状态；③DAG 依赖用 tasks.deps 字段（JSON 数组），ready_tasks view 自动计算依赖已满足的 task，dispatcher 不再手动推理依赖；④worker_done 外挂协议：解释 herdr 为何不能原生实现（被动 screen scraping、agent 不知 herdr 存在、无 dispatch --inject），通过 dispatcher 在 agent prompt 注入 preamble 要求输出 ## TASK_COMPLETE 标记 + pane wait-output --match 捕获来模拟，模板 templates/worker_done_preamble.md；⑤事件驱动协调器循环替代旧版 sleep 5s 轮询：用 herdr agent wait --until idle,done,blocked 阻塞等待，多 agent 并行用 PowerShell Start-Job + Wait-Job -Any，零消耗实时捕获，参考脚本 templates/coordinator_loop.ps1；⑥旧版协调器循环段落标记为已废弃但保留概念；⑦新增编排状态数据库段落（三层模型表/schema 概述/CLI 命令清单/DAG 自动工作原理/worker_done 协议）和事件驱动协调器段落（7 步循环/vs 旧版轮询对比表/PowerShell 并行等待模板/硬规则）。
  - 0.5.0: 项目重命名 herdr-orchestrator → herdr-phalanx（Phalanx=重步兵方阵，呼应 2×2 田字格编队）。①skill 内部 ID（frontmatter name + schema）全量改名，wiki/getting-started.md 同步；②迁移到 GitHub 项目 E:\WorkSpace\github\herdr-phalanx 作为唯一真实源，四处 agent skills 目录（.agents/.pi/opencode/hermes）改为指向该项目的符号链接；③description 补充 grid topology / 2x2 phalanx / per-tab cap 触发关键词；④历史 changelog 中旧路径名保留原样（作为当时事实记录）。
  - 0.4.2: 新增"分屏拓扑规则（Grid Topology：田字格 + 每 Tab 上限 4）"（用户 2026-09-05 定版）。①硬规则 T-Grid-1~5：同一 workspace 每个分身 tab 最多 4 个 agent、固定 2×2 田字，第 5 个开新分身 tab，dispatcher 独占指挥 tab，所有 split 用 --ratio 0.5 --no-focus；②田字切分算法实测验证（herdr v0.8.2，临时 tab 切 3 刀后用 pane layout 几何坐标确认等宽等高 2x2，测完关闭）：root down 分上下两行 → 上行 right → 下行 right；③槽位编号 slot1左上/slot2右上/slot3左下/slot4右下，分身序号→tab=floor(n/4)、slot=n mod 4 +1；④不足 4 个的增量渐进布局表（1全屏/2上下/3上二下一/4田字/5开新tab），保证扩容不重排已有 pane；⑤扩容操作流程（数 pane → 补 slot 或 tab create → agent start → pane layout 核对）；⑥最小工作流步骤1改为强制引用 Grid Topology，DAG 并行硬规则补"并行度超 4 开新 tab"，Upgrade Hooks topologies 段登记。
  - 0.4.1: 修复并 verified claudecode（Claude Code v2.1.260 / Opus 4.8 1M）。两层根因：①native binary 缺失——bin/claude.exe 是 500 bytes 占位脚本，用 `npm i -g @anthropic-ai/claude-code --include=optional` 补装 208MB 真二进制；②npm shim 干扰——herdr 的 `Start-Process -FilePath claude` 在同目录 claude.cmd 与 claude.exe 并存时误选 .cmd 文本当 PE 加载报 Win32 193，把无扩展名 claude/claude.cmd/claude.ps1 改名 .bak、PATH 只留 claude.exe 后修复。冒烟全链路通过（start interactive_ready → prompt done 3s → read 见 CLAUDE_SMOKE_OK + bypass permissions）。新增 P9（Windows npm 全局 agent 的 193 启动坑 + 通用规律 + 验证标准 + 无害 hook 告警说明）；员工表/P5/任务分配硬规则/Upgrade Hooks/P8 共 6 处把 claudecode 从"未安装/待冒烟"升为第 4 个 verified 持续对话 agent。
  - 0.4.0: 借鉴 Orca orchestration 编排能力，新增"任务编排模式"段落。①任务状态机：形式化 pending/running/blocked/done/failed 五种状态的转换规则和硬规则（禁止跳变、done 必须有证据、blocked 禁止盲发 Enter）；②任务 DAG：三种编排模式（并行独立/依赖链/扇出-扇入）+ DAG 硬规则（依赖任务禁止提前启动、失败暂停 DAG）；③决策门：5 个标准决策门（设计评审/实现验收/QA验证/集成测试/冒烟验证）+ 通过/回退/升级三分支；④协调器循环：8 步扫描-派活循环（agent list 扫描→pending 可启动检查→超时检查→blocked 响应→done 决策门→failed 回退→派活→退出判断）；⑤升级机制：Level 0-4 五级升级路径（正常执行→自动回退→换高级 agent→拆分任务→问用户）+ 触发条件和硬规则；⑥强化准入检查：补充 herdr server 状态确认、agent list 首次扫描、运行前必读 references/herdr-cli-quickref.md；⑦Upgrade Hooks protocols 段登记任务编排协议。
  - 0.3.4: 移除 codex + 完善免审批方案。①因 codex 在 herdr 中 prompt 时显示 "Conversation interrupted" 后退出（不稳定），从员工表、实体段、P5、P8、Upgrade Hooks、决策树、跨 Agent 兼容性说明中移除 codex；herdr 原生 kind 列表保留 codex（事实），但标注"员工表已移除，如需使用需先解决 prompt 中断问题"。②P8 完善各 agent 免审批参数汇总：omp(--auto-approve/--approval-mode yolo)、claude(--permission-mode bypassPermissions)、opencode(--auto)、hermes profile(无需 bypass，无审批弹窗，已验证)、pi(无 auto-approve 参数，走 pane run pi -p print 模式)。③P8 新增重要架构约束：herdr 多 pane 隔离架构下 dispatcher 不能替其他 agent 点审批，开团队必须每个 agent 启动时自带免审批参数。④P8 正确用法代码块补充 hermes profile 启动示例，移除 codex 示例。⑤员工表从 11 个独立 agent 减为 10 个（移除 codex）。
  - 0.3.3: 全面冒烟测试结果落盘。2026-09-04 对 4 个 agent 执行 `pane split → agent start → agent prompt --wait → agent read` 完整冒烟：①hermes-coding（LongCat-2.0）完全 verified，24s 返回正确回复；②hermes-testing（MiniMax-M2.7）完全 verified，18s 返回正确回复；③opencode 启动+prompt 链路通过（状态 done），但 agent read 未抓到文本（在 alternate screen），待复验；④codex 启动成功（idle/interactive_ready），但 prompt 时显示 "Conversation interrupted" 后退出，待调试。员工表同步更新 4 行状态，P5 整段重写（从"只有 omp verified"改为"3 个 verified + 8 个各异状态"），补充 hermes profile 不需要 bypass 参数的说明。
  - 0.3.2: 纠正 claudecode 的 herdr kind：从 `claude-code` 统一改为 `claude`（herdr `agent start --help` 的 possible values 里是 `claude`，不是 `claude-code`）。涉及 6 处：实体 Agent 段 kind 示例、员工表 claudecode 行 kind 列、员工表 wikiskill-proposer 行 kind 列、P1 kind 列表、Upgrade Hooks agents 段 claudecode 条目、跨 Agent 兼容性说明。备注里的 npm 包路径 `@anthropic-ai/claude-code/install.cjs` 保留不变（这是包名，不是 herdr kind）。P8 正确用法代码块里的 `--kind claude` 原本就是对的。
  - 0.3.1: 完整健康体检修复。①纠正 codex 安装状态：从"本机未在 PATH 中"改为"已安装（C:\Users\OahMoa\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe），未经冒烟测试"；②纠正 opencode 安装状态：从"本机未安装"改为"已安装（C:\Users\OahMoa\scoop\shims\opencode.exe），未经冒烟测试"，并补充 bypass 参数 --auto（已用 opencode --help 确认）；③P5 标题和正文补充 codex（之前遗漏）；④实体 Agent 段当前快照补充 codex；⑤P8 修复 opencode 重复/矛盾表述（一行说已确认、一行说未验证），合并为单一条目；P8 正确用法代码块补充 opencode 启动示例；⑥Upgrade Hooks agents 段补充 codex 条目（之前遗漏）；⑦安装方式从四处复制改为通用目录+三处符号链接（pi/opencode/hermes 均指向 .agents/skills/herdr-orchestrator），hermes 已验证 os.walk(followlinks=True) 可正常加载。
  - 0.3.0: 跨 agent 通用化发布。①新增"跨 Agent 兼容性说明"段落：明确通用内容（本体论框架、任务分配决策树、角色表、Pitfalls 方法论、故障兜底模式、WikiSkill 三层模型）与 Herdr 特定内容（pane/agent 命令、HERDR_ENV、workspace 拓扑）的边界，提供 Herdr→非 Herdr 等价替换表，以及非 Herdr 环境下的推荐用法；②安装到通用 agent skill 目录（C:\Users\OahMoa\.agents\skills\herdr-orchestrator\）、pi 专用目录（C:\Users\OahMoa\.pi\agent\skills\herdr-orchestrator\）、opencode 专用目录（C:\Users\OahMoa\.config\opencode\skills\herdr-orchestrator\），三处均含完整 SKILL.md + 3 份 references + wiki + runs；③frontmatter version 字段同步更新。
  - 0.2.9: 结构与实用性优化。①frontmatter 增加标准字段 version 和 platforms:[windows]；②员工表补全 codex 独立行（标注本机未在 PATH 中，待确认）；③新增"任务分配决策树"段落：按任务类型（单条命令/持续对话低中高复杂度/调研/部署/测试）快速定位首选 agent，附硬规则；④最小可用工作流步骤5 补全收尾动作（汇总产出+验证证据）；⑤新增"完整端到端示例"：原型 A omp 单人开发全流程命令模板+预期输出检查点；⑥故障与兜底表从 5 行扩充到 10 行，新增 agent start 超时、pane split 返回空、pane run 注入 caller、omp 弹权限框、pi 超时、workspace 报错等场景；⑦Upgrade Hooks artifacts 段从"当前未定"改为建议格式（runs/<task-id>/task.md + raw.jsonl）。
  - 0.2.8: 全面事实核查修正。①本体论 Agent kind 示例补充 pi/omp；②P1 kind 列表补充 omp；③P7 Pi agent start 超时根因从确定结论改为"推测根因"，补充待安装 herdr integration install pi 后复验；④opencode 员工表补充"本机当前未安装"；⑤pi 员工表措辞从"可直接 agent start"改为"herdr 支持但本机实测超时，生产首选 pane run pi -p"，消除与 P7 的矛盾；⑥补建缺失的 references/herdr-cli-quickref.md（herdr v0.8.2 命令速查、kinds 列表、state authority 表）和 references/pi-coding-agent.md（Pi 核心事实、安装、运行模式、与 OMP 的区别、Herdr 集成结论）。核实通过项：hermes profile 列表及模型标注（default=MiniMax-M3 / coding+design+devops+research=LongCat-2.0 / testing=MiniMax-M2.7）、WikiSkill 论文 arXiv 2608.27454、Pi npm 包名 @earendil-works/pi-coding-agent 及官网 pi.dev、herdr agent kinds 列表与 --help 完全一致。
  - 0.2.7: 纠正认知偏差：omp 是 Oh My Pi（独立终端 AI 编程 Agent，Rust 原生引擎，LSP/DAP/subagents/多模型），不是 OpenAI Codex CLI。员工表 omp 行 kind 从 codex 改为 omp，描述重写；最小可用工作流示例 kind 从 codex 改为 omp；P5 标题和正文 kind 纠正；P8 整段重写，区分各 agent bypass 参数（omp: --auto-approve；codex: --dangerously-bypass-hook-trust；claude: --permission-mode bypassPermissions），omp-dev 启动命令从 --kind codex 改为 --kind omp；Upgrade Hooks agents 段 omp 条目重写；冒烟 trace 文件名 codex-smoke-test 标注为旧命名沿用。
  - 0.2.6: 用户口述两条 bypass 参数（codex: --dangerously-bypass-hook-trust；claude: --permission-mode bypassPermissions）。新增 raw trace `runs/20260904_bypass-flags-collect/raw.jsonl` + WikiArticle `wiki/agent-bypass-flags.md`（含 3 条 verified 条目）。新增 Pitfalls P8（持续对话型 agent 必须带 bypass）。员工表 claudecode 备注：native binary 当前未安装，需先补 install.cjs。omp 备注加 bypass 参数。
  - 0.2.5: 实测 `herdr agent start --kind codex` 在本机完全可用（整链路：split → start idle → prompt done → read 真输出）。新增 raw trace `runs/20260904_codex-smoke-test/raw.jsonl` + WikiArticle `wiki/codex-smoke-test.md`（2 条 verified）。员工表 `omp` promote 为 verified。P5 标记"omp/codex 部分 verified，hermes/claudecode/opencode 部分仍 seed"。P7 任务分配规则更新：omp 已可用作持续对话 agent。
  - 0.2.4: 实测 `herdr pane run <独立 pane> 'pi -p "..."'` 走通（w1:p6 真独立 pane，6.3s 返回 hello from pi，P4 修复路线完整验证）。新增 raw trace `runs/20260904_pane-run-pi-test/raw.jsonl` + WikiArticle `wiki/pi-pane-run-works.md`（含 2 条 verified 条目）。新增 Pitfalls P7（Pi 走 pane run 路线，不走 agent start；stateless 任务给 Pi，持续对话用 omp/claudecode）。
  - 0.2.3: 实测 `herdr agent start --kind pi --timeout 60000` 在本机超时（即使 Pi TUI 已启动并显示提示行，herdr 仍收不到 lifecycle signal）。新增 raw trace `runs/20260904_pi-startup-test/raw.jsonl` + WikiArticle `wiki/pi-startup-test.md`（id: pi-agent-start-timeout-v1, status: verified）。Fallback 路线待用户决策。
  - 0.2.2: 新增 WikiSkill Wiki Layer 首份种子 `wiki/getting-started.md`：包含 WikiArticle 标准 schema（11 字段）+ 状态枚举（seed/verified/contested/deprecated）+ 5 条种子条目。SKILL.md 补充 wiki 目录指针。
  - 0.2.1: 启用 WikiSkill 循环（evolution: 段 + P6 约束）。新增 references/wikiskill-paper.md 浓缩参考卡（论文事实 + 三层模型 + 四组件 + 数据 + 启用条件）
  - 0.2.0: 融合 WikiSkill（Google Research arXiv 2608.27454）三层模型（Raw / Wiki / Skill）+ 4 组件循环（Inference / Maintainer / Proposer / Gating）。本体论新增 WikiArticle 实体 + Skill─derived_from→WikiArticle 关系；角色表新增 WikiMaintainer / SkillProposer / GatingReviewer；员工表新增 3 个 wikiskill-* 工种；升级接口新增 evolution: 段；Pitfalls 加 P6（wiki ≠ skill）
  - 0.1.3: 加 Pitfalls P4（`herdr pane run --pane <非 caller>` 在本机不可靠：实际把命令注入到 caller pane；要求用 `pane split + agent start` 路线）；停掉 hermes profile 启动冒烟测试，避免再出类似事故
  - 0.1.2: 员工表纳入 hermes profile 系列员工（hermes-coding/hermes-design/hermes-devops/hermes-research/hermes-testing/default）；Upgrade Hooks 同步登记；员工表 kind 清单补充 `--profile <name>` 用法
  - 0.1.1: 加 Pitfalls 段（P1 Hermes≠agent / P2 先 web_search / P3 快照≠定义）；升级 description 触发词；员工表 pi 转快照
  - 0.1.0: 初始骨架（4 实体 / 3 关系 / 6 约束 + 4 角色 + 4 员工 + 3 拓扑原型）
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
| WikiMaintainer   | 把 Raw Layer 的执行 trace 提炼为 WikiArticle（WikiSkill 三层中的 Wiki Layer） | `hermes-research`, `omp`              |
| SkillProposer    | 读 Wiki + trace，提议一次 skill 更新       | `claudecode`, `pi`, `hermes-coding`   |
| GatingReviewer   | 在独立验证集上评估提议；不通过就回滚        | `hermes-testing`, `claudecode`        |

运行时通过 `assign(role, agent_name)` 建立绑定；解绑用 `release(role)`。

**PM 行的特殊性**：ProjectManager 默认由 Hermes 本体承担，**不是** pane 里的 agent。它不占 pane、不被 `herdr agent prompt` 调用，也不计入 Agent 名册。任何把 Hermes 写进 `## 员工` 表格的映射都是错的——参见 `## Pitfalls`。

---

## 员工（Agents）

每个员工 = 一个 Herdr pane + 一个已启动的 coding agent。

| Name        | Kind 默认       | 备注                                          |
|-------------|------------------|-----------------------------------------------|
| `pi`        | `pi`             | Pi Coding Agent（pi.dev / `@earendil-works/pi-coding-agent`），Herdr 原生 kind；herdr 支持 `agent start --kind pi`，但**本机实测 agent start 超时**，生产首选 `pane run pi -p`（见 P7） |
| `omp`       | omp              | Oh My Pi（omp v18.0.4，**verified 持续对话**，本会话实测整链路通过）。Rust 原生引擎，LSP/DAP/subagents/多模型支持，本机安装于 `~/.bun/bin/omp.exe`。生产用法：必须带 `--auto-approve`（P8） |
| `claudecode`| claude           | Anthropic Claude Code CLI v2.1.260（模型 Opus 4.8 1M，**2026-09-04 冒烟 verified 持续对话**：start idle → prompt done → read 真输出，3s 返回，bypass permissions 生效）。native binary 在 `C:\Program Files\nodejs\node_modules\@anthropic-ai\claude-code\bin\claude.exe`（208MB）。生产用法：必须带 `--permission-mode bypassPermissions`（P8）。**Windows 启动坑见 P9**（npm shim 会让 herdr 的 Start-Process 报 193，必须只留 claude.exe） |
| `opencode`  | opencode         | OpenCode CLI（**已安装**：`C:\Users\OahMoa\scoop\shims\opencode.exe`；2026-09-04 冒烟：启动成功+prompt 完成（状态 done），**链路通过**，但 agent read 未抓到文本（在 alternate screen），待复验）。生产用法：必须带 `--auto`（P8） |
| `hermes-coding`   | `hermes --profile coding`   | Hermes 子 profile（LongCat-2.0，**2026-09-04 冒烟 verified**，24s 返回正确回复）        |
| `hermes-design`   | `hermes --profile design`   | Hermes 子 profile（LongCat-2.0）        |
| `hermes-devops`   | `hermes --profile devops`   | Hermes 子 profile（LongCat-2.0）        |
| `hermes-research` | `hermes --profile research` | Hermes 子 profile（LongCat-2.0）        |
| `hermes-testing`  | `hermes --profile testing`  | Hermes 子 profile（MiniMax-M2.7，**2026-09-04 冒烟 verified**，18s 返回正确回复）        |
| `hermes-default`  | `hermes --profile default`  | Hermes 默认 profile（MiniMax-M3）        |
| `wikiskill-maintainer` | `hermes --profile research` | WikiSkill 三层循环的 Wiki Maintainer；把 Raw trace 蒸馏成 WikiArticle |
| `wikiskill-proposer`   | `claude`              | WikiSkill 的 Skill Proposer；读 Wiki + trace 提议一次 skill 更新 |
| `wikiskill-gating`     | `hermes --profile testing`  | WikiSkill 的 Gating Reviewer；在独立验证集上评估更新；不通过就回滚 |

补充说明：

- 员工表里的 Hermes 不应出现。Hermes 是 dispatcher，**不**作为 pane 里的 agent 被 `herdr agent prompt` 调用。
- Herdr 原生支持的 agent kind（来自 `herdr agent --help`）：`pi | claude | codex | gemini | cursor | devin | agy | cline | omp | mastracode | opencode | copilot | kimi | kiro | droid | amp | grok | hermes | kilo | qodercli | qwen | maki`。**员工表当前使用其中的 pi / omp / claude / opencode / hermes**（codex 已于 v0.3.4 因不稳定移除，如需使用需先解决 prompt 中断问题）。Hermes 系列 kind 在 herdr 里就是 `hermes`，profile 通过 `hermes agent start --profile <name>` 或 herdr 的 `-- <agent-args...>` 透传；不在清单内的 kind 走 `pane run` 路线。
- Hermes profile 列表是**本机快照**（`hermes profile list` 出来什么就用什么）。本机当前：default / coding / design / devops / research / testing。新增 / 删除 profile 后要同步刷新员工表，并在 `## Upgrade Hooks → agents:` 段追加或删除。
- 员工名变更 / 新增员工时，在 `## Upgrade Hooks` 的 `agents:` 段追加，不改其它段。

---

## 团队形态（Team Topology，可与用户共同敲定）

> 这一段故意留白，等首次实战时跟用户定。当前预留 3 种原型。

- **原型 A：单一全栈** — 1 Developer + 1 QA，ProjectManager 即 Hermes。适合小修小补。
- **原型 B：架构-实现分离** — 1 Architect 出方案，2 Developer 并行实现，1 QA 验证。适合新模块开发。
- **原型 C：红蓝对抗** — 1 Developer vs 1 QA（QA 同时写破坏性测试），PM 仲裁。适合重构 / 安全加固。

切换原型 = 在运行时重新分配 `Role → Agent` 映射，不创建/销毁 agent。

---

## 分屏拓扑规则（Grid Topology：田字格 + 每 Tab 上限 4）

> 2026-09-05 用户定版并实测验证（herdr v0.8.2，`pane layout` 几何坐标确认 2x2 等宽等高）。**这是开分身时的强制布局规则，优先级高于最小工作流步骤 1 的通用 split 写法。**

### 硬规则（T-Grid）

- **T-Grid-1**：同一个 workspace 内，**每个"分身 tab"最多放 4 个 agent 分身，布局固定为 2×2 田字格**。
- **T-Grid-2**：需要第 5 个分身时，**新开一个分身 tab**，从田字 slot 1 重新铺；以此类推，每满 4 个翻一个 tab。禁止在一个 tab 里切出第 5 个 pane。
- **T-Grid-3**：dispatcher（Hermes）**独占一个"指挥 tab"**，不混进分身田字 tab——保证每个分身 tab 都是整齐的 4 格田字，且分身计数 = 纯 agent 数，不被 dispatcher 占槽。
- **T-Grid-4**：所有 split 一律 `--ratio 0.5`（均分）+ `--no-focus`（不抢焦点，承接 C2）。
- **T-Grid-5**：一个分身 = 一个 pane（承接 DAG 硬规则"并行任务禁止共享 pane"）；槽位一旦切好不重排，扩容只做增量 split。

### workspace 内的 tab 角色

```
Workspace wN（一个项目）
├─ Tab「指挥」      : 1 个 pane，跑 Hermes dispatcher（发号施令，本身不是分身）
├─ Tab「团队-1」    : 田字 4 分身（slot 1-4，第 1-4 个分身）
├─ Tab「团队-2」    : 田字 4 分身（slot 1-4，第 5-8 个分身）
└─ Tab「团队-k」    : 第 (4k+1) 个分身起，每 tab 4 个
```

分身序号（从 0 起）到物理位置的映射：
- 分身 tab 序号 = `floor(分身序号 / 4)`（0 → 团队-1，1 → 团队-2 …）
- tab 内槽位 slot = `(分身序号 mod 4) + 1`（1=左上，2=右上，3=左下，4=右下）

### 田字格切分算法（grid-2x2，3 刀，已实测）

新建分身 tab 后拿到 root pane `R`（`herdr tab create --workspace wN --cwd <项目路径> --no-focus` 返回的 `root_pane.pane_id`），按下面顺序切 3 刀：

```bash
# 第 1 刀：root 沿 down 横切 → R=上行，B=下行
B=$(herdr pane split $R --direction down  --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
# 第 2 刀：上行 R 沿 right 竖切 → R=左上(TL/slot1)，TR=右上(slot2)
TR=$(herdr pane split $R --direction right --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
# 第 3 刀：下行 B 沿 right 竖切 → B=左下(BL/slot3)，BR=右下(slot4)
BR=$(herdr pane split $B --direction right --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
```

切完槽位固定：**slot1=左上=`$R`，slot2=右上=`$TR`，slot3=左下=`$B`，slot4=右下=`$BR`**，按 slot1→4 的阅读顺序依次 `agent start`。

实测几何（tab 区域 228×57）：TL/TR 在上半高 29，BL/BR 在下半高 28（奇数行取整差 1，正常），左右各宽 114——标准等宽等高田字。

### 不足 4 个时的渐进布局（增量切分，不返工）

为保证从少到多扩容时**不重排已有 pane**，按田字二叉树的构建顺序增量切：

| 分身数 | 动作（在当前分身 tab） | 形态 |
|---|---|---|
| 1 | 直接用 root pane，不切 | 单格全屏（=slot1 左上） |
| 2 | 对 root `split --direction down --ratio 0.5` | 上下两行（slot1 上 / slot3 下） |
| 3 | 对上半 `split --direction right --ratio 0.5` | 上二下一（slot1,2 / slot3） |
| 4 | 对下半 `split --direction right --ratio 0.5` | **完整田字（slot1-4）** |
| 5 | **新开 tab**，回到"1 个"的流程 | 新 tab slot1 |

> 说明：2 个分身时是上下排列（而非左右），这是为了让第 3、4 个分身只需各补一刀即得到田字，中途不移动任何已启动的 agent。若明确只用 2 个且不会扩容，可改用 `--direction right` 左右排列。

### 扩容操作流程（第 N 个分身加入时）

1. `herdr pane list`（或 `tab list`）数当前 workspace 已有几个**分身 pane**（排除指挥 tab 的 dispatcher pane）。
2. 当前最新分身 tab 未满 4 → 在该 tab 按上表补切下一个 slot；已满 4 → `herdr tab create --workspace <wN> --cwd <项目路径> --no-focus` 开新分身 tab 并走 grid-2x2。
3. 拿到目标 slot 的 pane_id 后 `herdr agent start <name> --kind <k> --pane <slot_pane> -- <bypass 参数>`（bypass 见 P8，Windows npm 类 agent 启动报 193 见 P9）。
4. 用 `herdr pane layout --pane <该 tab 任一 pane>` 核对几何，确认没有第 5 格、比例为 0.5。

---

## 任务编排模式（Task Orchestration Patterns）

> 借鉴 Orca orchestration 的结构化多 agent 协调理念，在 Herdr 多路复用器内实现任务 DAG、决策门、协调器循环和升级机制。所有模式都基于 Herdr 原生命令（pane/agent/workspace），不引入外部依赖。

### 任务状态机（Task State Machine）

每个 Task 有明确的状态和转换规则，dispatcher 必须按状态机操作，禁止跳变：

```
                    ┌──────────┐
                    │ pending  │ 初始状态，已创建未派活
                    └────┬─────┘
                         │ assign(role, agent) + agent prompt
                         ▼
                    ┌──────────┐
              ┌────▶│ running  │ agent 正在执行
              │     └────┬─────┘
              │          │
              │     ┌────┴────┐
              │     ▼         ▼
              │ ┌───────┐ ┌─────────┐
              │ │ done  │ │ blocked │ agent 等待输入/审批
              │ └───────┘ └────┬────┘
              │                  │ agent get + agent read 后决定输入
              │                  ▼
              │              ┌──────────┐
              │              │  running │（恢复执行）
              │              └────┬─────┘
              │                   │
              │              ┌────┴────┐
              │              ▼         ▼
              │         ┌───────┐ ┌────────┐
              │         │ done  │ │ failed │ 连续3次失败/超时
              │         └───────┘ └───┬────┘
              │                          │
              │                          ▼
              │                     ┌─────────┐
              │                     │ pending │（回退，换 agent 重派）
              │                     └─────────┘
              │
              └─── blocked 超时 → escalate（见升级机制）
```

**状态转换硬规则：**
- `pending → running`：必须通过 `agent prompt` 派活，禁止直接标记
- `running → done`：必须有验证证据（文件路径/命令输出/测试结果），禁止口头确认
- `running → blocked`：agent 主动等待输入时，dispatcher 必须 `agent get` + `agent read` 看 UI，**禁止盲发 Enter/y**
- `blocked → running`：dispatcher 提供输入后恢复
- `running → failed`：连续 3 次失败或超时，必须记录失败原因
- `failed → pending`：回退时必须换 agent 或换方案，禁止同一 agent 同一方案重试超过 3 次

### 任务 DAG 编排（Task DAG）

当任务间有依赖关系时，用有向无环图（DAG）组织，而不是线性顺序执行。支持三种模式：

**模式 A：并行独立任务** — 无依赖，同时派给多个 agent

```
Task A (omp) ──┐
Task B (hermes-coding) ──┼──→ 全部 done → 汇总验收
Task C (pi -p) ──┘
```

实现：分别 `pane split` 3 个 pane，`agent start` 3 个 agent，同时 `agent prompt`，用 `agent wait --until idle` 分别等待。

**模式 B：依赖链** — 后续任务依赖前置任务的输出

```
Task A (架构设计) → done → Task B (实现) → done → Task C (测试)
```

实现：Task A done 后，把产出（文件路径/设计文档）作为 prompt 上下文传给 Task B。禁止 Task B 在 Task A done 之前启动。

**模式 C：扇出-扇入** — 一个前置任务产出后，并行派多个子任务，最后汇总

```
Task A (需求拆解) → done → ┌─ Task B1 (前端) ─┐
                             ├─ Task B2 (后端) ──┼→ 全部 done → Task C (集成测试)
                             └─ Task B3 (API) ───┘
```

**DAG 硬规则：**
- 有依赖的任务必须等前置任务 `done` 后才能启动（状态机约束）
- 并行任务必须在独立 pane 里跑，禁止共享 pane；pane 布局遵循 `## 分屏拓扑规则（Grid Topology）`——每分身 tab 田字格最多 4 个并行 pane，并行度超过 4 就开新分身 tab 承载
- DAG 中任何一个任务 `failed`，整个 DAG 暂停，dispatcher 决定回退或升级
- DAG 完成后必须有汇总验收步骤，不能各自 done 就结束

### 决策门（Decision Gates）

在关键节点设置决策门，根据前一步结果决定下一步走向，而不是无脑继续。

**标准决策门结构：**

```
前一步完成 → 收集证据 → 评估标准 → 决策
                                  ├─ 通过 → 继续下一步
                                  ├─ 不通过 → 回退到上一步（换 agent/换方案）
                                  └─ 无法判断 → 升级（问用户/换更高级 agent）
```

**常见决策门位置：**

| 决策门 | 评估标准 | 通过条件 | 不通过动作 |
|---|---|---|---|
| **设计评审门** | 架构设计文档 | 覆盖所有需求点+无明显技术风险 | 回退给 SystemArchitect 重设计 |
| **实现验收门** | 代码变更+测试结果 | 所有测试通过+无编译错误 | 回退给 Developer 修 bug |
| **QA 验证门** | QA 测试报告 | 覆盖率达标+无 P0/P1 bug | 回退给 Developer 修复 |
| **集成测试门** | 端到端测试结果 | 核心流程全通 | 暂停 DAG，定位失败模块 |
| **冒烟验证门** | agent 启动+最小 prompt | <60s 返回 done | 标记 depromised，换 agent |

**决策门硬规则：**
- 每个决策门必须有**可量化的通过条件**，禁止"感觉可以"
- 决策结果必须记录（通过/不通过/升级），附证据
- 不通过时必须回退，禁止"先继续后面再说"
- 连续 2 次不通过同一决策门 → 升级（见升级机制）

### 协调器循环（Coordinator Loop）

> ⚠️ **v0.6.0 起，本段落的 sleep 轮询实现已废弃**。改用事件驱动协调器循环，见 `## 事件驱动协调器循环` 段落。这里保留核心概念（8 步扫描-派活循环），但实现方式从 sleep 轮询改为 `agent wait --until` 阻塞等待。

dispatcher 不是一次性派活就结束，而是进入持续监控循环，动态调整任务分配。

**循环概念（8 步，实现见事件驱动段落）：**

```
1. 扫描所有 agent 状态（agent list / run-status）
2. 检查 DAG 中 pending 任务是否可启动（task-ready 自动算依赖）
3. 检查 running 任务是否超时（超过预期时间？）
4. 检查 blocked 任务是否需要 dispatcher 输入
5. 检查 done 任务是否通过决策门
6. 检查 failed 任务是否需要回退/升级
7. 有可启动的 pending 任务 → 派活（agent prompt + dispatch-start）
8. 所有任务 done 或 DAG 暂停 → 退出循环
```

**协调器循环硬规则：**
- 每次循环必须先查 `run-status` + `task-ready`，禁止凭记忆判断
- 超时阈值按任务类型设定：简单任务 60s，中等任务 300s，复杂任务 900s
- blocked 任务必须在 30s 内响应（agent get + agent read + 提供输入）
- **禁止用 sleep 轮询**——必须用 `agent wait --until` 或 `pane wait-output --match` 阻塞等待（见事件驱动段落）
- 循环退出条件：所有任务 completed **且** 通过最终决策门，或用户中断，或 DAG 暂停等待用户决策

### 升级机制（Escalation）

当任务无法通过常规回退解决时，按升级路径逐级上报，禁止死磕。

**升级路径：**

```
Level 0: 正常执行
  │ 失败/超时
  ▼
Level 1: 自动回退（换 agent/换方案，同一任务最多 3 次）
  │ 仍失败
  ▼
Level 2: 换更高级 agent（如从 hermes-coding 换到 omp，或加 SystemArchitect 评审）
  │ 仍失败
  ▼
Level 3: 拆分任务（把复杂任务拆成更小的子任务，降低单次失败概率）
  │ 仍失败 / 无法拆分
  ▼
Level 4: 问用户（明确说明失败原因、已尝试的方案、需要用户决策的点）
```

**升级触发条件：**
- 同一任务连续 3 次 failed
- 同一决策门连续 2 次不通过
- blocked 任务超过 5 分钟无响应
- agent 进程崩溃（agent get 返回 error）
- DAG 中关键路径任务失败，影响整体交付

**升级硬规则：**
- 每次升级必须记录：失败原因、已尝试的方案、升级原因
- Level 4 问用户时必须给出**具体选项**，不能只说"失败了怎么办"
- 禁止跳过升级路径直接问用户（Level 1-3 必须先尝试）
- 升级后原任务标记为 `blocked`（等待用户/更高级 agent），不占用 worker

---

## 编排状态数据库（Phalanx DB：Run / Task / Dispatch 三层模型）

> v0.6.0 新增。herdr 原生只有物理层（Session/Workspace/Tab/Pane）和 agent 状态检测（idle/working/blocked/done/unknown），**没有**任务编排层（Run/Task/Dispatch、DAG 依赖、worker_done 协议）。我们用 sqlite 在 skill 层外挂一个编排状态数据库，herdr 只负责物理执行，Phalanx DB 负责编排状态。这是借鉴 Orca orchestration 的 Run/Task/Dispatch 三层模型，但完全基于 herdr 原生命令实现，不引入外部依赖。

### 为什么 herdr 不能原生实现 worker_done（关键认知）

herdr 的 agent 检测是**被动 screen scraping**：它读 pane 终端输出，用特征匹配判断 agent 处于什么状态（看到权限弹窗→blocked，看到提示符→idle/done），但**不解析输出内容的语义**。omp/claudecode 这些 agent 启动时**不知道自己跑在 herdr 里**，不会主动发"我完成了"消息。

Orca 能做 worker_done，是因为它用 `dispatch --inject` 把生命周期 preamble 注入到 agent prompt，agent 被要求完成后调用 `orca orchestration send --type worker_done`——这是 Orca 特有的客户端协议。herdr 没有这个注入机制。

**但我们可以模拟**：dispatcher 在 `agent prompt` 时注入 preamble，要求 agent 完成后输出固定标记 `## TASK_COMPLETE`，然后用 `pane wait-output --match "## TASK_COMPLETE"` 或 `agent wait --until idle,done` 捕获，解析后写回 sqlite。这是**外挂式 worker_done 协议**。

### 三层模型

```
Run（一次编排会话）
  └── Task（工作项定义，可重试，有 DAG 依赖）
        └── Dispatch（Task 的一次具体执行尝试，分配给某个 agent/pane）
```

| 层 | 对应 Orca | 职责 | 状态 |
|---|---|---|---|
| **Run** | Run | 一次编排会话的命名空间，所有 task/dispatch/events 的归属 | active / completed / failed / aborted |
| **Task** | Task | 工作项定义，可重试，有 DAG 依赖（deps 字段） | pending / ready / dispatched / completed / failed / blocked / skipped |
| **Dispatch** | Dispatch | Task 的一次具体执行尝试，每次重试产生新 Dispatch | running / completed / failed / blocked / abandoned |

**关键区别**：Task 是"要做什么"（可重试），Dispatch 是"谁在做、做的结果"（一次尝试）。同一个 Task 失败重试会产生新的 Dispatch，重试历史完整保留。

### sqlite 数据库

- **路径**：默认 `~/.herdr-phalanx/phalanx.db`，环境变量 `PHALANX_DB` 可覆盖
- **schema**：`db/schema.sql`（6 张表 + 2 个 view）
  - `runs` / `tasks` / `dispatches` / `events`（append-only 事件日志）/ `gates`（决策门）
  - `ready_tasks` view：自动计算依赖已全部完成的 task（协调器循环用这个派活）
  - `run_summary` view：Run 的汇总统计（total/completed/failed/running/pending）
- **管理 CLI**：`db/phalanx_db.py`（Python 3.10+，仅用标准库 sqlite3）

### phalanx_db.py 常用命令

```bash
# 初始化数据库
python db/phalanx_db.py init-db

# Run 管理
python db/phalanx_db.py run-create --objective "实现登录功能" --workspace w1
python db/phalanx_db.py run-status --run <run_id>
python db/phalanx_db.py run-list

# Task 管理（--deps 支持 JSON 数组或逗号分隔）
python db/phalanx_db.py task-add --run <run_id> --spec "设计API" --role Architect --agent claudecode
python db/phalanx_db.py task-add --run <run_id> --spec "实现登录" --deps <task1_id> --role Developer --agent omp
python db/phalanx_db.py task-ready --run <run_id>    # 查可派活的 task（依赖已满足）
python db/phalanx_db.py task-list --run <run_id> --status pending

# Dispatch 管理
python db/phalanx_db.py dispatch-start --task <task_id> --agent-name dev1 --agent-kind omp --pane w1:p3 --tab w1:t3
python db/phalanx_db.py dispatch-complete --dispatch <disp_id> --outcome succeeded --files "src/a.py,src/b.py" --summary "做了什么。发现了什么。还剩什么。"
python db/phalanx_db.py dispatch-fail --dispatch <disp_id> --reason "编译错误"

# worker_done 解析（v0.6.1 新增）
python db/phalanx_db.py parse-worker-done --text "$(herdr agent read <name> --lines 100)"   # 纯解析，输出 JSON
python db/phalanx_db.py parse-worker-done --file /tmp/agent-out.md                              # 从文件读取解析
python db/phalanx_db.py dispatch-complete-from-output --dispatch <disp_id> --text "$(herdr agent read <name> --lines 100)"  # 解析+写库一步完成

# 决策门 + 事件日志
python db/phalanx_db.py gate-create --run <run_id> --type qa_verify --question "测试是否通过"
python db/phalanx_db.py gate-resolve --gate <gate_id> --resolution pass
python db/phalanx_db.py event-log --run <run_id> --limit 20
```

### DAG 依赖如何自动工作

1. 创建 Task 时用 `--deps` 声明依赖（JSON 数组或逗号分隔）
2. `ready_tasks` view 自动过滤：status=pending **且**所有 deps 的 task 都已 completed
3. 协调器循环每轮查 `task-ready`，得到可派活的 task 列表，自动派活
4. 前置 task 完成后，后置 task 自动出现在 ready 队列——**不需要 dispatcher 手动推理依赖关系**

### worker_done 外挂协议

**Preamble 模板**：`templates/worker_done_preamble.md`，注入到 agent prompt 开头，要求 agent 完成后输出：

```
## TASK_COMPLETE
outcome: succeeded|failed
files_modified: ["path/a", "path/b"]
summary: 做了什么。发现了什么。还剩什么。
```

需要提问时输出：
```
## TASK_ASK
question: 你的问题
options: ["选项A", "选项B"]
```

**dispatcher 捕获方式**（优先级从高到低）：
1. `herdr pane wait-output <pane_id> --match "## TASK_COMPLETE" --timeout <MS>` —— 等结构化标记
2. `herdr agent wait <name> --until idle,done,blocked --timeout <MS>` —— 等 agent 状态变化（herdr 原生阻塞等待）
3. 降级：`herdr agent read` 读输出，正则解析 `## TASK_COMPLETE` 块

解析后调用 `dispatch-complete` 写回 sqlite，outcome/files/summary 结构化存储。

**推荐用法（v0.6.1）**：不要自己写正则，直接用 CLI 一步完成：
```bash
# 纯解析（调试用）
python db/phalanx_db.py parse-worker-done --text "$(herdr agent read <name> --lines 100)"
# 解析 + 写库（生产用）
python db/phalanx_db.py dispatch-complete-from-output --dispatch <disp_id> --text "$(herdr agent read <name> --lines 100)"
```

**omp 渲染兼容性（v0.6.1 修复）**：omp 会把 `## TASK_COMPLETE` 渲染成标题（去掉 `##`），且 `TASK_COMPLETE` 与 `outcome:` 之间可能有空行，`files_modified` 的值可能不带引号（如 `[a.py, b.py]`）。`parse_worker_done` 已兼容以上所有格式，dispatcher 无需特殊处理。

---

## 事件驱动协调器循环（v0.6.0 替代旧版 sleep 轮询）

> 旧版协调器循环用 `sleep 5s + agent list` 轮询，效率低、延迟高、可能错过瞬态变化。v0.6.0 起改用**事件驱动**：用 herdr 原生的 `agent wait --until` 阻塞等待，多 agent 并行用 PowerShell 后台 job，哪个先完成先处理哪个。

### 核心循环（7 步）

```
┌──────────────────────────────────────────────────────────┐
│ 1. run-status 查 Run 状态 → 全部 completed 则退出循环     │
│ 2. task-ready 查可派活的 task（依赖已满足的 pending）      │
│ 3. 对每个 ready task：                                     │
│    a. 按 Grid Topology 分配 pane（田字格/超4开新tab）      │
│    b. agent start（带 bypass 参数，见 P8）                 │
│    c. dispatch-start（写 sqlite）                          │
│    d. agent prompt（注入 worker_done preamble + task spec）│
│       用 --wait 阻塞等完成，或 agent wait --until idle,done│
│ 4. 收集当前 running 的 dispatch                             │
│ 5. 事件驱动等待：并行 agent wait，等第一个完成的 agent      │
│    （PowerShell: Start-Job + Wait-Job -Any）              │
│ 6. 读输出 → 解析 ## TASK_COMPLETE → dispatch-complete      │
│ 7. 回到第 1 步（新的 ready task 会自动出现）                │
└──────────────────────────────────────────────────────────┘
```

### 事件驱动 vs 旧版轮询

| 维度 | 旧版（sleep 轮询） | 新版（事件驱动） |
|---|---|---|
| 等待方式 | `sleep 5s` 后 `agent list` | `agent wait --until idle,done,blocked` 阻塞 |
| 延迟 | 最高 5s | 实时（状态变化立即返回） |
| 资源 | 每 5s 一次全量扫描 | 阻塞等待，零消耗 |
| 多 agent | 逐个检查 | 并行 wait，Wait-Job -Any 先到先处理 |
| 瞬态变化 | 可能错过（5s 内完成又开始新任务） | 不会错过（阻塞等待精确捕获） |

### PowerShell 并行等待模板

```powershell
# 并行等待多个 agent，返回第一个完成的
function Wait-AnyAgent($agentNames, $timeoutMs = 900000) {
    $jobs = foreach ($name in $agentNames) {
        Start-Job -ScriptBlock {
            param($n, $t)
            herdr agent wait $n --until idle,done,blocked --timeout $t 2>&1
        } -ArgumentList $name, $timeoutMs
    }
    $done = $jobs | Wait-Job -Any
    $result = $done | Receive-Job
    $jobs | Stop-Job -PassThru | Remove-Job -Force
    return $result
}
```

参考脚本：`templates/coordinator_loop.ps1`（完整的事件驱动协调器循环模板）。

### 事件驱动的硬规则

- **禁止**在协调器循环里用 `sleep` 轮询 agent 状态——必须用 `agent wait --until` 或 `pane wait-output --match` 阻塞等待
- 多 agent 并行时必须用后台 job + `Wait-Job -Any`，不能逐个串行 wait（会阻塞其他 agent 的完成检测）
- `agent wait` 的 `--timeout` 必须设置（建议 900000ms = 15 分钟），超时视为 checkpoint 不是失败（长任务可能跑 15-60 分钟）
- 超时后检查 `agent get` 确认 agent 是否还在 working，在则继续 wait，不在则标记 failed
- heartbeat（agent 仍在 working 但长时间无输出）不视为完成，继续 wait

---

## 任务分配决策树（快速选 agent）

拿到任务后，按以下顺序判断，直接定位到首选 agent：

```
任务来了
  │
  ├─ 是单条 shell 命令 / 文件扫描 / grep / git log？
  │   └─ 是 → pi（pane run pi -p，stateless，6s 级返回）
  │
  ├─ 是持续对话型开发任务（需要多轮修改 / 读文件 / 跑测试）？
  │   ├─ 任务复杂度低（< 3 个文件，< 100 行改动）
  │   │   └─ omp（已 verified，--auto-approve，首选）
  │   ├─ 任务复杂度中（3-10 个文件，需要架构决策）
  │   │   └─ omp + hermes-research（omp 写代码，research 做调研兜底）
  │   └─ 任务复杂度高（> 10 个文件，跨模块重构）
  │       └─ 原型 B：claudecode（架构）+ omp（实现）+ hermes-testing（QA）
  │
  ├─ 是调研 / 资料整理 / 写文档？
  │   └─ hermes-research 或 omp（omp 也能做，但 research profile 更专注）
  │
  ├─ 是部署 / CI / 运维相关？
  │   └─ hermes-devops
  │
  └─ 是测试用例编写 / 验证报告？
      └─ hermes-testing
```

**硬规则**：
- 持续对话型任务，**首选已 verified 的 4 个**：omp / claudecode / hermes-coding / hermes-testing
- opencode / 其余 hermes profile（design/devops/research/default）用之前**必须先单独冒烟**（见 P5）
- pi **只走 pane run**，不走 agent start（见 P7）
- 冒烟前**必须带 bypass 参数**（见 P8）；Windows 上 npm 全局装的 agent 若启动报 193，见 P9

---

## 最小可用工作流（v0.1）

### 0. 准入检查

```bash
# 0.1 环境确认
test "${HERDR_ENV:-}" = 1 || { echo "NOT in Herdr"; exit 1; }

# 0.2 版本确认（命令可能随版本变化，先确认版本）
herdr --version

# 0.3 Server 状态确认（确保 herdr server 在运行）
herdr workspace list

# 0.4 当前 agent 状态扫描（协调器循环的第一次扫描）
herdr agent list
```

**运行前必读**：执行任何 herdr 命令前，先读 `references/herdr-cli-quickref.md` 确认命令语法和 ID 形态——herdr 版本升级时命令可能变化，quickref 是与本机实测版本匹配的速查表。禁止凭记忆或旧版本缓存猜命令。

任何一步失败 → 停下来，把错误贴给用户，**不要**继续 split/start。

### 1. 准备 pane（按原型选数量）

**布局强制走田字格规则**：分身 pane 的切分/翻 tab 一律按 `## 分屏拓扑规则（Grid Topology）`——每分身 tab 最多 4 个、2×2 田字、超 4 开新 tab、dispatcher 独占指挥 tab，不要在本步骤自由选择 split 方向。下面只列最小原子动作：

```bash
# 田字格的一刀：对指定源 pane 均分切分，保留 cwd，不抢焦点
NEW_PANE=$(herdr pane split <源PANE_ID> --direction right|down --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
echo "$NEW_PANE"
```

需要几个分身就按 Grid Topology 的增量表切几刀；切满 4 个再需要分身时 `tab create` 开新分身 tab，不要在同一 tab 切第 5 刀。

**先 split，再 run——不要直接 `pane run --pane <非 caller>`**（见 P4）。

**禁止**：把 `pane run` 直接对着非 caller pane ID 调用——这会把命令注入到当前 caller pane，hijack dispatcher（2026-09-04 实测）。

### 2. 启动 agent

```bash
herdr agent start developer1 --kind omp --pane "$NEW_PANE"
```

启动阻塞时返回 `agent_not_ready`，名字仍有效；用 `agent wait <name> --until idle` 等就绪。

### 3. 派活（ProjectManager 视角）

```bash
herdr agent prompt developer1 "Implement X. Return only file paths + diff summary." --wait --timeout 180000
```

`--wait` 等到 idle/done/blocked；不要冗余加 `--until idle`。

### 4. 验收（QA 视角）

QA 跑验证命令或读源文件，结论必须带证据（路径 / 命令输出）。证据不足视为 failed，回退到 Developer 修。

### 5. 收尾

- 不主动 close pane（违反 C4）。
- 把 Task state 写回共享笔记（详见 `## Upgrade Hooks → artifacts`）。
- 汇总产出：列出所有修改的文件路径 + 改动摘要 + 验证证据。

### 完整端到端示例（原型 A：omp 单人开发）

```bash
# 0. 准入检查
test "${HERDR_ENV:-}" = 1 || { echo "NOT in Herdr"; exit 1; }
herdr workspace list

# 1. 准备 pane（右侧分屏，不抢焦点）
NEW_PANE=$(herdr pane split --current --direction right --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
echo "New pane: $NEW_PANE"

# 2. 启动 omp（带 bypass，持续对话首选）
herdr agent start dev1 --kind omp --pane "$NEW_PANE" -- --auto-approve
herdr agent wait dev1 --until idle

# 3. 派活（等结果，超时 3 分钟）
herdr agent prompt dev1 "修复 src/auth.js 里的 token 过期 bug。返回修改的文件路径和改动摘要。" --wait --timeout 180000

# 4. 验收（QA 视角，必须带证据）
herdr agent read dev1
# 手动验证：跑测试 / 读源文件 / 检查 diff
# 证据不足 → 回退给 dev1 修；通过 → 标记 done

# 5. 收尾（不关 pane，写任务账本）
mkdir -p runs/20260904-auth-bug
echo "# Task: fix token expiry bug in src/auth.js" > runs/20260904-auth-bug/task.md
echo "owner: dev1 (omp)" >> runs/20260904-auth-bug/task.md
echo "status: done" >> runs/20260904-auth-bug/task.md
echo "files: src/auth.js" >> runs/20260904-auth-bug/task.md
```

**预期输出检查点**：
- 步骤1：`NEW_PANE` 有值（如 `w1:p4`），不是空
- 步骤2：agent 状态从 `starting` → `idle`，不超时
- 步骤3：agent 状态 `working` → `idle` 或 `done`，返回包含文件路径
- 步骤4：能读到完整输出，不是截断或乱码

---

## 故障与兜底

| 现象                                   | 兜底动作                                                                 |
|----------------------------------------|--------------------------------------------------------------------------|
| `agent prompt` 卡住 5s 无 lifecycle 变化 | 视为 `agent_prompt_stalled`，改用 `pane run` + `pane wait-output`          |
| `agent read` 行数加到很大仍读不全       | agent 在 alternate screen，让它把完整响应写到 `/tmp/agent-out/*.md` 再读  |
| `blocked` 状态出现                     | `agent get` + `agent read` 看 UI，再决定输入；**不要**盲发 Enter / y        |
| 同一员工连续 3 次失败                  | 换 Agent 或回退到原型 A 单人模式，不要死磕                                |
| `agent start` 超时 30s                 | 名字被回收，需重新 `agent start`；检查 bypass 参数是否带了（P8）           |
| `pane split` 返回空 pane_id            | 检查 `--no-focus` 是否加了；重试一次；仍失败则用 `pane list` 手动找空闲 pane |
| `pane run` 命令注入到 caller pane      | 命中 P4：必须先 split 新 pane 或先 focus 目标 pane，不要直接对非 caller 调 pane run |
| omp 启动后弹权限确认框                  | 没带 `--auto-approve`；停掉 agent，重新 `agent start --kind omp -- --auto-approve` |
| pi `pane run` 超时无输出               | pi 可能在等 API key 或模型响应；检查 `pi -p` 是否能在普通 shell 单独跑通   |
| workspace list 报错                    | herdr server 可能没启动；先 `herdr server start`，不要继续 split/start      |

---

## Pitfalls（踩过的坑，写给未来的自己）

### P1：Hermes dispatcher ≠ pane 里的 agent

把"Hermes"写进员工表 / 把它当成 `herdr agent start --kind hermes` 启动目标，是这个 skill 最常犯的错。

- Hermes 是 skill 的执行者（dispatcher），永远不进 Herdr pane。
- 它不能被 `herdr agent prompt` 调用，也不能用 `herdr agent start` 启动。
- 在角色表里，PM 行可以默认由 Hermes 承担，但这一栏**描述 dispatcher 角色**而不是 Agent 员工。
- 员工表的 kind 必须是真实存在的 agent 二进制（pi / omp / claude / opencode / hermes-profile / 未来新增 kind）。Hermes 不是 kind。

修复检查：每当要填 `## 员工` 表格或写 `herdr agent start ... --kind X` 时，问一次"X 是 Herdr pane 里能跑的进程吗？"——是 → 写；不是 → 别写。

### P2：填具体 kind / 加新员工前，先 web_search 确认实体

不要凭印象写"pi 就是 Hermes / Codex 衍生品"之类。Agent 名字第一次出现时，按这个顺序确认：

1. `web_search "<name>" coding agent CLI`（带具体关键词过滤）
2. 拉官网 + GitHub，确认：kind、二进制名、是否需要安装、运行模式（TUI / print / RPC / SDK）
3. 把 condensed 事实沉淀到 `references/<name>.md`，下次直接引用
4. 然后再填员工表 + 在 `## Upgrade Hooks → agents:` 登记

不搜就填 = 猜测 = 用户会立刻打回。

### P3：快照不是定义

`## 角色` 和 `## 员工` 表格是**当前快照**，会过时。新增 / 调整时改两个地方：

1. 表格里直接补一行
2. `## Upgrade Hooks` 的 `roles:` / `agents:` 段追加注册项

漏掉 `## Upgrade Hooks` = 这个快照进不了本体论，下次版本 bump 时会丢。

### P4：`pane run --pane <非 caller>` 在本机不可靠

2026-09-04 实测：`herdr pane run w1:p4 'pi -p "..."'` 没有把命令发到 w1:p4，而是注入了 caller pane（w1:p3 / dispatcher pane）。后果：

- dispatcher pane 被 hijack，本会话 terminal 工具被阻塞（连续 `[Command interrupted]` / exit 130）
- 没法用 `Ctrl+C` 自动恢复，只能用户在 Herdr UI 手动按

修复规则（升级版 `## 最小可用工作流 → 步骤 1`）：

1. 想让命令跑在**新 pane** → 必须先 `herdr pane split --current --direction <dir> --cwd "$PWD" --no-focus` 拿新 pane ID，**再**用 `pane run <新 pane ID>`。不要跳过 split。
2. 想让命令跑在**已存在的非 caller pane** → 必须先 `herdr pane focus <pane_id>` 把它变 caller（或用 `--current` 在 caller 上跑），否则会被 hijack 到当前 caller pane。
3. 想启动 agent → 走 `herdr agent start <name> --kind <k> --pane <id>`，**不要**用 `pane run <id> <agent 命令>` 绕路（agent start 自带 agent 识别和 lifecycle 跟踪）。
4. 启动阻塞 → `agent wait <name> --until idle`；启动 30s 超时 → 名字会被回收（不是 `agent_not_ready` 状态），需要重新 start。

**禁止动作**：在没有先 split/focus 的前提下，对非 caller pane 用 `pane run` 发任何命令。

### P5：agent 启动冒烟测试状态（omp / hermes-coding / hermes-testing 已 verified）

本 skill 列出的 10 个独立 agent 中，**4 个已 verified**（2026-09-04 冒烟：`pane split → agent start idle → prompt done → read 真输出` 整链路通过）：

- `omp` (kind=omp) — 已 verified（持续对话首选）
- `claudecode` (kind=claude, Opus 4.8 1M) — 已 verified（3s 返回正确回复；修复过程见 P9）
- `hermes-coding` (kind=hermes, LongCat-2.0) — 已 verified（24s 返回正确回复）
- `hermes-testing` (kind=hermes, MiniMax-M2.7) — 已 verified（18s 返回正确回复）

其余 6 个状态：

- `pi` — 不可走 agent start（见 P7，走 pane run）
- `opencode` — 启动成功+prompt 完成（状态 done），**链路通过**，但 agent read 未抓到文本，待复验
- `hermes-design` / `hermes-devops` / `hermes-research` / `hermes-default` — 未测（与 coding/testing 同属 hermes profile，链路应相同，但需逐个验证）

后果：除了已 verified 的 4 个之外，其他"持续对话型"用法（agent start + prompt --wait），**理论上** herdr 支持，**实践上** 状态各异。

修复路线（按优先级）：
1. `omp` / `claudecode` / `hermes-coding` / `hermes-testing` 已 verified → 持续对话任务优先派给这 4 个
2. `pi` 走 pane run 路线（stateless / 一次性任务，详见 P7）
3. `opencode` 链路已通，可谨慎使用，read 问题待复验
4. `hermes-design` / `hermes-devops` / `hermes-research` / `hermes-default` — 每次用之前先单独冒烟（`agent start` + `agent prompt --wait` 各跑一次最小任务，< 60s 内返回 done 算 verified）
5. **冒烟前必带 bypass 参数**（详见 P8，各 agent 参数不同，不可混用）：
   - omp：`herdr agent start <name> --kind omp --pane <id> -- --auto-approve`
   - claudecode：`herdr agent start <name> --kind claude --pane <id> -- --permission-mode bypassPermissions`
   - opencode：`herdr agent start <name> --kind opencode --pane <id> -- --auto`
   - hermes profile：hermes 本身不需要 bypass 参数（无权限弹窗），直接 `--profile <name>` 即可
6. 冒烟通过后把员工表 status 升 verified，失败把 status 降 depromised 并写新 Pitfall

### P6：wiki ≠ skill（WikiSkill 三层模型的关键约束，已启用）

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
- **Proposer 提议更新时** → 必须引用至少一条 WikiArticle，否则不通过 Gate
- **任何时候 wiki 都不能"清空"或"重写历史条目"**；只能新增版本或新增条目

修复合一：每次 skill bump 时，changelog 末尾加一行 `derived_from: wiki/<slug>.md#<section>` 指针，pane 上看到缺失这条就 reject。

### P7：Pi 走 pane run 路线，不走 agent start（实测沉淀）

2026-09-04 两次实测结论：

- **`herdr agent start --kind pi` 不可靠**（P5 已记录）：60s 超时，herdr 收不到 Pi TUI 的 lifecycle signal，名字被回收
- **`herdr pane run <独立 pane> 'pi -p "..."'` 可用**：本会话在 w1:p6 跑通 `pi -p "echo hello from pi"`，6.3s 返回 `hello from pi ✓`，dispatcher pane `w1:p3` 未受影响（P4 修复路线完整验证）

Pi 的调用链是 2 层（外层 Pi TUI agent + 内层 `pi -p` print 子进程），不适合做持续对话。任务分配规则：

- **stateless / 一次性任务**（文件扫描、代码 grep、批量分析、git log 解析）→ 给 Pi，走 `pane run pi -p`
- **持续对话 / 多轮 prompt**（开发任务、需要 lifecycle / idle 检测）→ 给 `omp`，**已 verified**（本会话 2026-09-04 实测通过）；其他持续对话型员工（`claudecode` / `opencode` / 任何 hermes profile）每次用之前先冒烟（见 P5）
- **禁止**用 `herdr agent start --kind pi` 跑生产（本机超时；**推测根因**：未安装 herdr pi integration 时，herdr 用 screen manifest 检测 idle，Pi 全屏 TUI 提示行格式和 herdr 期望的 idle marker 不匹配——待安装 `herdr integration install pi` 后复验）

实测 trace：
- `runs/20260904_pi-startup-test/raw.jsonl`（agent start 失败）
- `runs/20260904_pane-run-pi-test/raw.jsonl`（pane run 成功）

### P8：持续对话型 agent 必须带 bypass 权限参数（用户口述 + 实测验证）

2026-09-04 用户口述 + 工具验证结论（各 agent 参数不同，不可混用）：

- **omp（Oh My Pi）**：`--auto-approve`（已用 `omp --help` 确认，auto-approve all tool calls / skip approval prompts）；也可用 `--approval-mode yolo`
- **claudecode**：`--permission-mode bypassPermissions`（已用 `claude --help` 确认 choices 含 bypassPermissions；2026-09-04 冒烟实测底部显示 "bypass permissions"，参数生效）
- **opencode**：`--auto`（已用 `opencode --help` 确认：auto-approve permissions that are not explicitly denied, dangerous!）
- **hermes profile**：**不需要 bypass 参数**（hermes 无权限弹窗，已验证：coding/testing 冒烟均无审批直接执行）
- **pi**：无专门 auto-approve 参数；`--approve` 仅信任项目本地文件，不跳过工具审批；生产走 `pane run pi -p`（print 模式，一次性任务无持续审批弹窗）

**重要架构约束**：herdr 采用多 pane 隔离架构，dispatcher（Hermes）**不能**替其他 pane 里的 agent 点击审批弹窗——每个 agent 的审批框只出现在它自己的 pane 里。因此开团队时**必须**在启动每个 agent 时就带上对应的免审批参数，不能依赖 dispatcher 代审批。

不带的症状：agent 启动后第一次执行动作时弹出权限确认 / hook trust 对话框，herdr pane 没人按确认，任务 hang 死，`agent_status` 卡在 `blocked` 不进 `working`。

**正确用法**：

```bash
# omp（首选，已 verified）
herdr agent start omp-dev --kind omp --pane w1:pN -- --auto-approve

# claudecode（已 verified，Opus 4.8）
herdr agent start cc-dev --kind claude --pane w1:pN -- --permission-mode bypassPermissions

# opencode（链路已通）
herdr agent start oc-dev --kind opencode --pane w1:pN -- --auto

# hermes profile（无需 bypass，直接 --profile）
herdr agent start hc-dev --kind hermes --pane w1:pN -- --profile coding
```

⚠️ **安全约束**：bypass 参数 = 跳过所有权限确认 = agent 可以任意执行 / 写文件 / 改代码。**只在 herdr pane 隔离环境里用**，不要在用户主 shell 复用。本机本次冒烟（`runs/20260904_codex-smoke-test/raw.jsonl`，文件名沿用旧命名，实际实测对象已纠正为 omp）任务太简单（echo 一行）没触发权限弹窗，所以没带 bypass 也成功了——但**生产任务必须带**。

事实落盘：
- `runs/20260904_bypass-flags-collect/raw.jsonl`
- `wiki/agent-bypass-flags.md`（含 3 条 verified 条目：bypass-flags / P8 / claude native binary missing）

### P9：Windows 上 npm 全局装的 agent，herdr Start-Process 会报 193（claudecode 实测修复）

2026-09-04 修复 claudecode 全过程沉淀。**症状**：`herdr agent start cc --kind claude` 超时 "timed out waiting for agent startup"，`pane read` 看到 PowerShell 报 `Start-Process : This command cannot be run due to the error: %1 is not a valid Win32 application`（Win32 错误码 193 = ERROR_BAD_EXE_FORMAT）。

**两层根因，必须都修**：

1. **native binary 缺失**：`@anthropic-ai/claude-code` 是 wrapper 包，真正的 ~208MB 二进制在 optionalDependency `@anthropic-ai/claude-code-win32-x64` 里，由 postinstall（`install.cjs`）复制到 `node_modules\@anthropic-ai\claude-code\bin\claude.exe`。若 postinstall 没跑（`--ignore-scripts` / optional deps 被 omit / 网络失败），`bin\claude.exe` 只是一个 **500 bytes 的占位 shell 脚本**（内容是 `echo "Error: claude native binary not installed."`）。
   - 修复：`npm install -g @anthropic-ai/claude-code --include=optional`（本机用淘宝镜像 registry.npmmirror.com，32s 装好），装完 `bin\claude.exe` 应为 217,771,680 bytes，`claude --version` 返回 `2.1.260 (Claude Code)`。

2. **npm shim 干扰 herdr 的 Start-Process（关键、隐蔽）**：npm 全局安装会在 `C:\Program Files\nodejs\` 生成 4 个入口：无扩展名 `claude`（Unix sh 脚本）、`claude.cmd`、`claude.ps1`、以及（修复后手动放的）`claude.exe`。herdr 在 pane 里执行的是 `Start-Process -FilePath claude ...`（不带扩展名）。**对照实验证实**：PowerShell 的 Start-Process 在同目录 `claude.cmd`（文本）与 `claude.exe`（PE）并存时，会误选 `claude.cmd` 直接当二进制加载 → 193；它不像交互式 shell 那样严格按 PATHEXT（.EXE 优先 .CMD）。
   - 修复：把 npm shim 全部改名备份，只留真正的 `claude.exe` 在 PATH 目录：
     - `C:\Program Files\nodejs\claude`（无扩展名）→ `claude.unix-sh.bak`
     - `C:\Program Files\nodejs\claude.cmd` → `claude.cmd.bak`
     - `C:\Program Files\nodejs\claude.ps1` → `claude.ps1.bak`
     - 复制真二进制：`Copy-Item node_modules\@anthropic-ai\claude-code\bin\claude.exe C:\Program Files\nodejs\claude.exe`
   - **安全性**：移除 .cmd/.ps1 shim 不影响交互式使用——PowerShell 和 cmd 里敲 `claude` 会直接命中 `claude.exe`（已实测两者 `claude --version` 均正常）。.bak 文件保留可随时回滚。

**验证标准**（修复后全绿）：
- `claude --version` → 2.1.260
- 模拟 herdr：`Start-Process -FilePath claude -ArgumentList '--version' -Wait -PassThru`（**不带扩展名**）exit 0
- herdr：`pane split → agent start --kind claude -- --permission-mode bypassPermissions` 返回 `interactive_ready:true`、标题 `✳ Claude Code`
- `agent prompt --wait` 返回 done，`agent read` 看到正确回复 + 底部 `bypass permissions`

**通用规律（适用于所有 npm/bun 全局安装的 agent）**：herdr 的 agent 启动走 `Start-Process -FilePath <kind 名>`，**最稳的形态是 PATH 目录里有一个与 kind 同名的原生 `.exe`**（omp 的 `.bun\bin\omp.exe`、opencode 的 `scoop\shims\opencode.exe`、hermes 的 `bin\hermes.exe` 都是这种形态，所以一次成功）。凡是只靠 `.cmd/.ps1/无扩展名 shim` 启动的 npm 包，在 herdr 里都可能踩 193，按上面"只留 .exe"处理。

**已知无害告警**：claudecode 启动/收尾时 pane 里会出现 `SessionStart/UserPromptSubmit/Stop hook error: Failed with non-blocking status code: No stderr output`，是 claude hook 在 Windows 的非阻塞告警，**不影响**思考、回复、done 状态，可忽略。

---

## Upgrade Hooks

> 所有后续扩展在这里追加。结构稳定前不要扩散到其它段。

- `agents:` — 新增员工。每条带 condensed fact 引用：
  - `pi` — 详见 `references/pi-coding-agent.md`。npm: `@earendil-works/pi-coding-agent`，MIT，kind: `pi`。启动：`pi`（TUI）/ `pi -p "..."`（print）。在本 skill 中**首选 `pane run pi -p`** 而非 `herdr agent start --kind pi`，原因见 references 末尾"在 Herdr 里怎么用"。
  - `omp` — kind: `omp`，Oh My Pi（omp）终端 AI 编程 Agent。Rust 原生引擎，LSP/DAP/subagents/多模型支持。本机已安装 v18.0.4（`~/.bun/bin/omp.exe`）。
  - `claudecode` — kind: `claude`，Anthropic Claude Code CLI v2.1.260（Opus 4.8 1M）。本机已修复并 verified（2026-09-04）：native binary 在 `C:\Program Files\nodejs\node_modules\@anthropic-ai\claude-code\bin\claude.exe`（208MB），PATH 目录 `C:\Program Files\nodejs\` 只保留 `claude.exe`，其余 npm shim 改名 .bak（原因与步骤见 P9）。
  - `opencode` — kind: `opencode`，OpenCode CLI。本机已安装（`C:\Users\OahMoa\scoop\shims\opencode.exe`），链路已通（2026-09-04 冒烟），read 待复验。
  - `hermes-coding` / `hermes-design` / `hermes-devops` / `hermes-research` / `hermes-testing` / `hermes-default` — kind: `hermes`，通过 `--profile <name>` 启动指定 profile。本机当前 profile 集合：`hermes profile list`（default / coding / design / devops / research / testing）。profile 列表是本机快照，新增 profile 后必须同步追加员工表行 + 在此处登记。
  - `wikiskill-maintainer` / `wikiskill-proposer` / `wikiskill-gating` — WikiSkill 三层循环的三个工种。**不是新 agent binary**，是给现有员工分配 WikiSkill 角色的别名（详见 `evolution:` 段）。
- `roles:` — 新增角色。例：`SRE` / `TechWriter` / `SecurityReviewer`。v0.2.0 新增 WikiSkill 三角色：`WikiMaintainer` / `SkillProposer` / `GatingReviewer`。
- `topologies:` — 新增团队原型。在 `## 团队形态` 段落登记。v0.4.2 起**物理分屏拓扑强制走 `## 分屏拓扑规则（Grid Topology）`**：dispatcher 独占指挥 tab；分身 tab 固定 2×2 田字、每 tab 上限 4、超 4 开新 tab；田字 3 刀切分算法（root down → 上行 right → 下行 right，均 --ratio 0.5）已用 `pane layout` 几何坐标实测验证；分身序号→tab/slot 映射 = tab=floor(n/4)、slot=n mod 4 +1。
- `artifacts:` — 共享笔记 / 任务账本格式。**建议格式**（v0.2.8 起）：每个任务在 `runs/<task-id>/` 下建 `task.md`，包含：任务描述、owner agent、分配角色、状态（pending/running/blocked/done/failed）、产出文件列表、验证证据。原始执行 trace 存 `runs/<task-id>/raw.jsonl`（append-only）。v0.2.0 起新增 `wiki/<topic-slug>.md`（WikiArticle 的落盘形态）。
- `protocols:` — 跨 agent 通信协议（如 shared file 路径、消息格式）。v0.2.0 起新增 WikiSkill 循环协议（详见 `evolution:` 段）。v0.4.0 起新增**任务编排协议**（详见 `## 任务编排模式` 段）：任务状态机（pending/running/blocked/done/failed 转换规则）、任务 DAG（并行独立/依赖链/扇出-扇入三种模式）、决策门（5 个标准决策门+通过/回退/升级三分支）、协调器循环（8 步扫描-派活循环）、升级机制（Level 0-4 五级升级路径）。v0.6.0 起新增**编排状态数据库协议**（详见 `## 编排状态数据库` 段）：Run/Task/Dispatch 三层模型（sqlite 持久化）、DAG 依赖自动计算（ready_tasks view）、worker_done 外挂协议（## TASK_COMPLETE 标记 + pane wait-output 捕获）、事件驱动协调器循环（agent wait --until 阻塞等待替代 sleep 轮询）。
- `evolution:` — 启用（v0.2.1）。WikiSkill 自演化协议（详见 `references/wikiskill-paper.md`），循环结构：

  ```
  ┌─────────────┐   产生 trace   ┌──────────────┐
  │ Inference   │──────────────→│ Raw Layer    │（append-only，本机落盘 runs/<task-id>/raw.jsonl）
  │ Agent       │                └──────┬───────┘
  └─────────────┘                       │
        ▲                                ▼
        │                       ┌────────────────┐
        │  skill 更新           │ WikiMaintainer │
        │  (通过 gate)           │ → WikiArticle  │（append-only，本机落盘 wiki/<slug>.md）
        │                       └────────┬───────┘
        │                                ▼
  ┌─────────────┐                ┌────────────────┐
  │ Gating      │←─── 评估 ─────│ SkillProposer  │
  │ Reviewer    │   验证集       │ → 提议更新     │
  └──────┬──────┘                └────────────────┘
         │ 不通过则回滚 skill（但 wiki 保留）
         ▼
      当前 Skill Layer
  ```

  四步顺序固定，**不允许跳过任何一步**：
  1. Inference Agent 跑 task，trace 落到 Raw Layer（每次 run 一份，永不删）
  2. WikiMaintainer 蒸馏 trace 为 WikiArticle（失败经验也写，append-only）
  3. SkillProposer 读 Wiki + Raw，提议一次 skill 更新（一条小改动，不批量）
  4. GatingReviewer 在独立验证集（不能是提议时用的 trace）上跑：分提高 → 接受；分持平或下降 → 回滚 skill，wiki 不动

  收益：每跑 N 次 task，skill 持续变好且可回滚；失败经验不会丢，下次 proposer 不会重蹈覆辙。

升级前先 bump frontmatter 的 `version`；改本体论实体/关系 → 同步 bump `schema`。

---

## 参考资料（references/）

- `references/herdr-cli-quickref.md` — herdr CLI 命令、ID 形态、lifecycle 状态、约束速查。本 skill 的所有命令都基于这份 quickref 写出。
- `references/pi-coding-agent.md` — Pi Coding Agent 的核心事实（kind / 安装 / 运行模式 / 与 Herdr 集成建议）。
- `references/wikiskill-paper.md` — WikiSkill 论文浓缩（Google Research arXiv 2608.27454）：三层模型、4 组件循环、实验数据、与本 skill 的对接点、已知局限、启用条件。加载时与 `evolution:` 段 + P6 配合读。

## Wiki（Wiki Layer 落盘目录）

- `wiki/` — WikiArticle 落盘位置。**append-only**（P6）。第一份种子：`wiki/getting-started.md`（含 WikiArticle schema + 5 条种子条目）。新增条目在此目录下建新文件，文件名 = topic-slug；版本演进通过 `id` 字段的 `-v<n>` 后缀表达，不重写历史文件。关键条目：`wiki/grid-2x2-topology.md`（田字格 2×2 + 每 tab 上限 4 的切分算法与几何实测，verified）、`wiki/agent-bypass-flags.md`（各 agent 免审批参数）、`wiki/claude-windows-193-fix.md`（claudecode 在 Windows/herdr 的 193 启动坑两层修复，verified）、`wiki/pi-pane-run-works.md` / `wiki/pi-startup-test.md`（Pi 走 pane run）、`wiki/codex-smoke-test.md`（codex 旧冒烟，现已从员工表移除）。

加载顺序：先看 SKILL.md 全文判断要不要进入编排流；如果要进入，再按需加载这三份 references 和相关 wiki 条目。

---

## 跨 Agent 兼容性说明（非 Herdr 环境下使用）

本 skill 已安装到通用 agent skill 目录（`.agents/skills/`），opencode、pi、claude 等非 Herdr agent 也能加载。但需注意：

### 通用内容（任何 agent 环境下都适用）

- **本体论框架**：5 类实体 + 4 类关系 + 2 类约束的拆分方式，适用于任何多 agent 编排场景
- **任务分配决策树**：按任务类型（单条命令 / 持续对话低中高复杂度 / 调研 / 部署 / 测试）选 agent 的逻辑通用
- **角色表**：ProjectManager / SystemArchitect / Developer / QA / ResearchLead 等角色定义通用
- **Pitfalls 方法论**：P1（dispatcher ≠ worker）、P3（快照≠定义）、P6（wiki≠skill）等认知约束通用
- **故障兜底模式**：agent 卡住换 agent、连续失败回退单人模式、blocked 状态先看 UI 再输入等策略通用
- **WikiSkill 三层模型**：Raw → Wiki → Skill 的自演化循环，适用于任何 agent 的经验沉淀

### Herdr 特定内容（非 Herdr 环境下需替换为等价命令）

以下命令和概念仅在 Herdr TUI 环境下有效，在其他 agent 中需替换为对应工具：

| Herdr 特定 | 非 Herdr 环境下的等价做法 |
|---|---|
| `herdr pane split` / `pane run` | 用终端 multiplexer（tmux / screen / Windows Terminal 分屏）或直接开新 shell |
| `herdr agent start --kind <k>` | 直接在终端启动对应 agent 二进制（omp / claude / opencode / hermes 等） |
| `herdr agent prompt <name> --wait` | 直接向 agent 发送 prompt，等待返回 |
| `HERDR_ENV=1` 准入检查 | 跳过此检查，或替换为当前环境的可用性检查 |
| workspace/tab/pane 拓扑 | 替换为当前环境的会话/窗口/面板概念 |
| `herdr agent list` / `agent get` / `agent read` | 用对应 agent 的会话管理命令 |

### 在非 Herdr 环境下的推荐用法

1. 把本 skill 当作**编排方法论参考**，而非可直接执行的命令脚本
2. 用"任务分配决策树"选 agent，用"角色表"分配职责
3. 用"Pitfalls"避免常见错误（dispatcher 不要混进 worker、快照要同步更新、wiki 和 skill 不要混用）
4. 用"故障兜底表"处理 agent 异常
5. Herdr 特定的命令全部替换为当前环境的等价工具

---

## 何时不用此 skill

- 单 agent 任务 → 直接 `herdr agent prompt` 即可，无需进入编排流。
- 临时跑一条命令 → `pane run`，不要创建 Role/Task。
- `HERDR_ENV` 未设 → 拒绝执行，让用户先在 Herdr 内启动会话。

---

## 下一步（< 2 分钟）

建议先在当前 Herdr 会话里跑一次 `## 最小可用工作流` 的步骤 0，确认 CLI 可用 + 当前 workspace 状态干净，再决定要不要进入原型 A 实战。需要我直接执行吗？
