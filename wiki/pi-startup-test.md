# herdr-pane-run-trap-v1

**title**: `pane run --pane <非 caller>` 陷阱
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_pane-run-test/raw.jsonl`（待补，本次实测 trace）
- 人工 seed
**tags**: herdr, pane, trap, pitfall
**status**: verified
**summary**: 在 Windows + herdr 当前版本下，`herdr pane run <非 caller pane id> '<cmd>'` 不会把命令发到目标 pane，而是注入到当前 caller pane，导致 dispatcher pane 被 hijack；本会话 terminal 工具被阻塞，必须用户在 Herdr UI 手动按 Ctrl+C 恢复。
**evidence**:
- 本会话实测（2026-09-04）：`herdr pane run w1:p4 'pi -p "echo READY"'` 把 `pi -p` 注入了 w1:p3（dispatcher pane），pane read 抓到的命令行回显就在 caller pane 的视口里，底部状态条显示 dispatcher 的 model 标识
- 后续两次 `terminal` 调用都被 `[Command interrupted]` / exit 130 打断
- `../SKILL.md` 的 `## Pitfalls → P4`
**lesson**: 让命令跑在**新 pane** → 必须先 `pane split --current --direction <dir> --cwd "$PWD" --no-focus` 拿新 pane ID，**再**用 `pane run`。不要跳过 split。
**related**:
- `pi-coding-agent-basics-v1`
- `wikiskill-loop-v1`

---

# pi-agent-start-timeout-v1

**title**: Pi agent 启动 60s 超时（herdr detect 失败）
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_pi-startup-test/raw.jsonl`（本会话实测，2026-09-04）
**tags**: herdr, agent-start, pi, timeout, pitfall, smoke-pending
**status**: verified
**summary**: `herdr agent start <name> --kind pi --pane <id> --timeout 60000` 在本机即使 60s 也返回 `timeout`。**但 Pi 实际已经启动成功**（pane read 显示底部状态条 = `minimax-cn | MiniMax-M2.7-highspeed · high` + `0%` 进度条 = Pi 自己的 TUI 提示行）。herdr 等的是 lifecycle signal，Pi 的提示行格式不是 herdr 预期的 detect 标记，所以一直等 idle 等不到。
**evidence**:
- 本会话实测（2026-09-04）：`herdr pane split --current --direction right --cwd "E:/WorkSpace" --no-focus` 成功返回 `w1:p6`
- `herdr agent start pi-developer --kind pi --pane w1:p6 --timeout 60000` 返回 `{"error":{"code":"timeout","message":"timed out waiting for agent startup"}}`，名字被立刻回收
- `herdr pane read w1:p6 --source recent-unwrapped` 抓到 Pi TUI 已运行：底部状态条显示 `0% | minimax-cn | MiniMax-M2.7-highspeed · high`
- `../SKILL.md` 的 `## Pitfalls → P5`（已有但未挂本次实测 trace，本次补上）
**lesson**: 不要假设 `herdr agent start --kind pi` 能 detect 到 Pi 已就绪。Pi 用全屏 TUI，提示行格式和 herdr 期望的 idle marker 不匹配。要么： (a) 增加 timeout 到 120s 重试； (b) 改走 `pane run pi -p "..."` 一次性 print 模式，跳过 lifecycle detect； (c) 放弃 herdr agent start，改用 pane run 直接包 pi 进程。三种 fallback 待用户决策。
**related**:
- `herdr-pane-run-trap-v1`
- `pi-coding-agent-basics-v1`
