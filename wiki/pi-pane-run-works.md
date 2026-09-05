# pi-pane-run-works-v1

**title**: Pi 在 `herdr pane run` 路线下能干活（嵌套 2 层）
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_pane-run-pi-test/raw.jsonl`（本会话实测 trace）
**tags**: herdr, pane-run, pi, verified, working
**status**: verified
**summary**: 在 pane split 创建的独立 pane（w1:p6）里用 `herdr pane run w1:p6 'pi -p "..."'` 跑通，**没有 hijack dispatcher**（P4 修复路线完全走通）。Pi 实际跑了 6.3s 返回 `hello from pi ✓`。但调用链是 2 层：外层 Pi TUI agent 把"shell 里敲的 pi -p"当成自然语言指令，调用内层 `pi -p` 真正执行 echo。
**evidence**:
- 本会话实测（2026-09-04）：
  - `herdr pane split --current --direction right --cwd "E:/WorkSpace" --no-focus` → 新 pane `w1:p6`
  - `herdr pane run w1:p6 'pi -p "echo hello from pi"'` → 注入 w1:p6（caller `w1:p3` 未受影响）
  - `pane read w1:p6` → 外层 Pi TUI 输出：`$ pi -p "echo hello from pi"` → `The user wants to run a pi command that echoes ... This is a simple shell command that needs to be executed.` → `$ pi -p "echo hello from pi"` → `Elapsed 2.0s` → `hello from pi ✓`（2 次）→ `Took 6.3s`
  - 外层 agent 总结："The command ran successfully. ... pi-claude-cli plugin missing warning, but actual echo worked"
**lesson**: Pi 在 pane run 路线下能做 1-shot / 一次性任务，但**不适合**做"持续接 prompt 的开发 agent"——单次往返要 2 层（agent TUI + 内层 -p），延迟 ~6s 且 prompt history 会被外层 agent 当对话上下文持续累积。skill 编排多 agent 时，Pi 应该只承担：批处理 / 文件扫描 / 一次性分析 这类 stateless 任务。
**related**:
- `herdr-pane-run-trap-v1`
- `pi-agent-start-timeout-v1`
- `pi-coding-agent-basics-v1`
- `wikiskill-loop-v1`

---

# P7 candidate

**title**: Pi 走 pane run 路线（不走 agent start）
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_pane-run-pi-test/raw.jsonl`
- `runs/20260904_pi-startup-test/raw.jsonl`
**tags**: herdr, pi, fallback, pitfall
**status**: verified
**summary**: 综合两次实测（agent start 60s 超时 + pane run 6.3s 成功），得出一条**工作规则**：本机 herdr + Pi 组合下，`herdr agent start --kind pi` 不可靠（herdr 等不到 lifecycle signal，60s 超时），但 `herdr pane run <独立 pane> 'pi -p "..."'` 可用。Pi 的定位应当降级为"普通进程"角色，由 Hermes dispatcher 通过 pane run 直接控制。
**evidence**:
- 本会话实测两次（详见上述 raw traces）
- `../SKILL.md` 的 `## Pitfalls → P5`（已记录 agent start 超时）
- 本条是 P5 的解决方案沉淀
**lesson**: 任务分配规则：
- **stateless / 一次性任务**（文件扫描、代码 grep、批量分析）→ 给 Pi（pane run pi -p）
- **持续对话 / 接收多轮 prompt**（开发任务、需要 lifecycle）→ 用 `omp` (codex) 或 `claudecode`（它们 TUI 提示格式应该更接近 herdr 期望，待冒烟）
- **禁止**用 `herdr agent start --kind pi` 跑生产
**related**:
- `pi-pane-run-works-v1`
- `pi-agent-start-timeout-v1`
