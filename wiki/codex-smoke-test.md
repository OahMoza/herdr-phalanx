# codex-agent-start-works-v1

**title**: Codex (kind) 在 herdr 里能完整跑通持续对话
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_codex-smoke-test/raw.jsonl`（本会话实测 trace）
**tags**: herdr, codex, agent-start, verified, working
**status**: verified
**summary**: 整链路跑通：`pane split` → `agent start --kind codex`（返回 idle + interactive_ready）→ `agent prompt`（返回 done + 真输出 `codex-ready`）→ `agent read`（拿到 gpt-5.6-terra medium 模型 + 完整对话历史）。本会话首个 verified 持续对话型 agent。
**evidence**:
- 本会话实测（2026-09-04）：
  - `which codex` → `/c/Users/OahMoa/AppData/Local/Programs/OpenAI/Codex/bin/codex`，版本 `codex-cli 0.149.1`
  - `herdr pane split --current --direction right --cwd "E:/WorkSpace" --no-focus` → 新 pane `w1:p7`
  - `herdr agent start omp-developer --kind codex --pane w1:p7 --timeout 60000` → 返回 `{"agent":{"agent":"codex","agent_status":"idle","interactive_ready":true,"name":"omp-developer",...}}`
  - `herdr agent prompt omp-developer "Reply with exactly one line: 'codex-ready'. Nothing else." --wait --timeout 60000` → 返回 `agent_status: done`
  - `herdr agent read omp-developer --source recent-unwrapped` → 抓到 `• codex-ready` 回复 + `gpt-5.6-terra medium · E:\WorkSpace` 状态行
**lesson**: Codex 在本机 herdr 完全可用作持续对话型 agent。codex TUI 的 prompt 格式（`›` 符号 + 模型行 + `Ask Codex to do anything`）herdr 能正确识别为 idle / done lifecycle。
**related**:
- `pi-agent-start-timeout-v1`（对照：Pi 不可用作持续对话）
- `pi-pane-run-works-v1`（对照：Pi 适合 stateless）
- `wikiskill-loop-v1`

---

# omp-promotion-to-verified-v1

**title**: omp (codex) 在员工表里 promote 为 verified 持续对话 agent
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_codex-smoke-test/raw.jsonl`
**tags**: meta, employee-promotion, codex
**status**: verified
**summary**: 把员工表 `omp` 的 status 从 seed 升为 verified——它的 kind (codex) 在 herdr 里有完整链路实证，可以承担 Developer / Architect 角色的持续对话任务。
**evidence**:
- `codex-agent-start-works-v1` 的完整 trace
- 本会话实测成功（详见 raw.jsonl）
**lesson**: skill 编排里分配持续对话任务给 omp 即可，不用担心 P5 的冒烟风险。
**related**:
- `codex-agent-start-works-v1`
- `pi-coding-agent-basics-v1`（Pi 仍保留 seed 状态）
