# Wiki 知识库（初始种子，v0.2.1）

> 这份 wiki 是 herdr-phalanx skill 的 WikiSkill Wiki Layer。
> 约束（来自 P6）：append-only，失败经验保留。每次蒸馏只新增 / 新增版本，不删不改历史。
> 维护规则见 `../SKILL.md` 的 `evolution:` 段。

## 字段约定（WikiArticle 标准 schema）

每条 WikiArticle 必须包含以下字段，缺一项视为不完整，Gate reject。

| 字段             | 必填 | 类型     | 说明                                                                |
|------------------|------|----------|---------------------------------------------------------------------|
| `id`             | ✓    | string   | `<topic-slug>-v<n>`，topic-slug 用 kebab-case                       |
| `title`          | ✓    | string   | 主题名（一句话）                                                    |
| `created`        | ✓    | ISO date | 创建日期（YYYY-MM-DD）                                              |
| `derived_from`   | ✓    | list     | 至少 1 条 Raw trace 引用（`runs/<task-id>/raw.jsonl#<line-range>`） |
| `tags`           | ✓    | list     | 主题标签（kebab-case），至少 1 个                                    |
| `status`         | ✓    | enum     | `seed` / `verified` / `contested` / `deprecated`                     |
| `summary`        | ✓    | string   | 一段话总结这件事                                                    |
| `evidence`       | ✓    | list     | 支持证据（命令输出 / 文件路径 / 错误消息）                          |
| `lesson`         | ✓    | string   | 一句话可执行的教训                                                  |
| `related`        | ×    | list     | 相关 WikiArticle id 列表                                            |
| `supersedes`     | ×    | string   | 如果是某旧条目的新版本，填旧 id                                     |

`status` 语义：

- `seed` — 由人工/PM 写入，未经过 WikiMaintainer 蒸馏验证
- `verified` — 经过至少一次 Inference → Propose → Gate 流程，验证为有效
- `contested` — 与另一条 WikiArticle 矛盾或被新证据推翻，需要人工仲裁
- `deprecated` — 不再适用，但**不删除**，标记以保留历史

---

## 条目索引

| id                            | title                                | status     | created       |
|-------------------------------|--------------------------------------|------------|---------------|
| `getting-started-v1`          | 怎么用 herdr-phalanx skill       | seed       | 2026-09-04    |
| `wikiskill-loop-v1`           | WikiSkill 4 步循环的要点              | seed       | 2026-09-04    |
| `herdr-pane-run-trap-v1`      | `pane run --pane <非 caller>` 陷阱    | verified   | 2026-09-04    |
| `hermes-profile-untested-v1`  | hermes + profile agent 启动未经冒烟    | seed       | 2026-09-04    |
| `pi-coding-agent-basics-v1`   | Pi Coding Agent 在 Herdr 里的基本用法 | seed       | 2026-09-04    |

---

## getting-started-v1

**title**: 怎么用 herdr-phalanx skill
**created**: 2026-09-04
**derived_from**: 人工 seed（v0.2.1 启用时由 PM 写入）
**tags**: meta, getting-started
**status**: seed
**summary**: 5 实体 / 4 关系 / 6 约束 / 6 角色快照 / 14 员工快照 + WikiSkill 三层循环。要开始：先准入检查，再 split pane，再 agent start，最后派活。
**evidence**:
- `../SKILL.md` 全文
- `references/wikiskill-paper.md` 论文浓缩
- `references/herdr-cli-quickref.md`（待补）
- `references/pi-coding-agent.md`（待补）
**lesson**: 进入 skill 前先做 `test "${HERDR_ENV:-}" = 1` 准入检查，不在 Herdr 里就拒绝执行控制命令。
**related**:
- `wikiskill-loop-v1`

---

## wikiskill-loop-v1

**title**: WikiSkill 4 步循环的要点
**created**: 2026-09-04
**derived_from**: 人工 seed
**tags**: wikiskill, evolution, protocol
**status**: seed
**summary**: 四步顺序固定（Inference → Maintain → Propose → Gate），不允许跳过。Gating 必须用独立验证集，不能拿提议时用的 trace 当验证集。
**evidence**:
- `references/wikiskill-paper.md` 第 4 段"四组件循环" + "关键约束"
- `../SKILL.md` 的 `## Upgrade Hooks → evolution:` 段
**lesson**: 提议更新时必须引用至少一条 WikiArticle（`derived_from` 字段非空），否则 Gate reject。
**related**:
- `getting-started-v1`

---

## herdr-pane-run-trap-v1

**title**: `pane run --pane <非 caller>` 陷阱
**created**: 2026-09-04
**derived_from**: `runs/20260904_herdr-pane-run-test/raw.jsonl`（待补，本次实测 trace）
**tags**: herdr, pane, trap, pitfall
**status**: verified
**summary**: 在 Windows + herdr 当前版本下，`herdr pane run <非 caller pane id> '<cmd>'` 不会把命令发到目标 pane，而是注入到当前 caller pane，导致 dispatcher pane 被 hijack；本会话 terminal 工具被阻塞，必须用户在 Herdr UI 手动按 Ctrl+C 恢复。
**evidence**:
- 本会话实测（2026-09-04）：`herdr pane run w1:p4 'pi -p "echo READY"'` 把 `pi -p` 注入了 w1:p3（dispatcher pane），pane read 抓到的命令行回显就在 caller pane 的视口里，底部状态条显示 dispatcher 的 model 标识
- 后续两次 `terminal` 调用都被 `[Command interrupted]` / exit 130 打断
- `../SKILL.md` 的 `## Pitfalls → P4`
**lesson**: 让命令跑在**新 pane** → 必须先 `pane split --current --direction <dir> --cwd "$PWD" --no-focus` 拿新 pane ID，**再**用 `pane run`。不要跳过 split。
**related**:

---

## hermes-profile-untested-v1

**title**: hermes + profile agent 启动未经冒烟
**created**: 2026-09-04
**derived_from**: 人工 seed
**tags**: hermes, agent-start, untested, smoke-pending
**status**: seed
**summary**: skill v0.1.2 列出了 `hermes-coding / hermes-design / hermes-devops / hermes-research / hermes-testing / hermes-default` 共 6 个员工，假设 `herdr agent start --kind hermes -- --profile coding` 能工作，但未实测。Pi 的 `agent start` 已经踩了超时坑（30s 超时 + 名字被回收），所以 hermes + profile 组合保持为"理论可行，未实测"。
**evidence**:
- `herdr agent --help` 输出的 kinds 列表里有 `hermes` 一个槽位
- 本会话实测 `herdr agent start pi-test --kind pi --pane w1:p4 --timeout 30000` 返回 `{"error":{"code":"timeout"}}` + 名字立刻被回收
- `../SKILL.md` 的 `## Pitfalls → P5`
**lesson**: skill 里 `--kind hermes -- --profile <name>` 是**未验证**的写法，不要直接拿来跑生产；先单独冒烟（用 60s timeout）验证 detect 成功，再解锁。
**related**:
- `pi-coding-agent-basics-v1`

---

## pi-coding-agent-basics-v1

**title**: Pi Coding Agent 在 Herdr 里的基本用法
**created**: 2026-09-04
**derived_from**: 人工 seed
**tags**: pi, coding-agent, kind
**status**: seed
**summary**: Pi 是 Herdr 原生 kind（`herdr agent --help` 的 kinds 列表里有 `pi`）。包名 `@earendil-works/pi-coding-agent`，MIT，已装 0.84.4。4 种使用模式：Interactive TUI / Print / RPC / SDK。YOLO mode by default（用户命令直接执行）。
**evidence**:
- 本机 `which pi` → `/c/Program Files/nodejs/pi`；`pi --version` → `0.84.4`
- `herdr agent --help` 输出 `kinds: pi|claude|codex|...|hermes|...`
- `references/pi-coding-agent.md`（待补）
**lesson**: 启动 Pi 用 `herdr agent start <name> --kind pi --pane <id>`；保守做法用 `pi -p "..."` 走 print 模式（但要走 split 后的 pane，不要直接 `pane run --pane <非 caller>`，见 `herdr-pane-run-trap-v1`）。
**related**:
- `herdr-pane-run-trap-v1`
- `hermes-profile-untested-v1`
