# 员工（Agents）

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
