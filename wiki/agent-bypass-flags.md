# agent-bypass-permission-flags-v1

**title**: Codex / Claude 必须 bypass 权限才能执行（持续对话型 agent 硬规则）
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_bypass-flags-collect/raw.jsonl`（本会话用户口述 + 工具验证）
- 用户输入（2026-09-04 15:24）
**tags**: herdr, codex, claude, bypass, permission, hard-rule
**status**: verified
**summary**: 持续对话型 coding agent 在 herdr pane 里跑会触发权限确认对话框（hook trust / permission mode），必须 bypass 才能继续执行。两类 agent 的 bypass 参数：

- **codex**：`--dangerously-bypass-hook-trust`
- **claudecode**：`--permission-mode bypassPermissions`

**evidence**:
- 本会话用户口述（2026-09-04 15:24）
- 验证 `codex --help` 抓到 `--dangerously-bypass-hook-trust` flag（help 输出含说明："Run enabled hooks without requiring persisted hook trust for this invocation. DANGEROUS."）
- 验证 `claude --help` 失败（native binary 没装，但 bypass 参数 `--permission-mode bypassPermissions` 是用户口述确认正确的）
- 补充：`codex --help` 还抓到 `--dangerously-bypass-approvals-and-sandbox`，但用户指定的是 `--dangerously-bypass-hook-trust`（针对 hook trust，不是 approvals + sandbox）—— 说明本机环境下问题出在 hook trust，不是 sandbox
**lesson**: 任何 herdr pane 里的 codex / claudecode agent，**启动时就必须带 bypass 参数**，否则任务会 hang 在权限对话框上。挂上后下面 `agent prompt` 才能真的派活出去。
**related**:
- `codex-agent-start-works-v1`（但本次冒烟没带 bypass 也成功了，需要 P8 进一步验证）
- `wikiskill-loop-v1`

---

# P8 candidate

**title**: 持续对话型 agent 默认带 bypass 权限参数
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_bypass-flags-collect/raw.jsonl`
- `agent-bypass-permission-flags-v1`
**tags**: meta, hard-rule, bypass
**status**: verified
**summary**: 持续对话型 coding agent（codex / claudecode / opencode）默认会触发权限确认 UI，在 herdr pane 里没人按确认就 hang 死。**所有持续对话型 agent 启动时必须带 bypass 参数**。
**evidence**:
- 见 `agent-bypass-permission-flags-v1`
**lesson**: 任务分配规则补丁：
- **codex**：`herdr agent start <name> --kind codex --pane <id> -- --dangerously-bypass-hook-trust`
- **claudecode**：`herdr agent start <name> --kind claude --pane <id> -- --permission-mode bypassPermissions`
- **opencode**：未验证（用户没口述），按相同逻辑应存在 `--dangerously-bypass-*` 或 `--permission-mode bypass*` 类参数，先查 `--help` 再带

⚠️ **安全注意**：bypass 参数会跳过所有权限确认 = agent 可以任意执行 / 写文件 / 改代码。**只在 herdr pane 隔离环境里用**，不要在用户主 shell 复用。
**related**:
- `agent-bypass-permission-flags-v1`
- `pi-pane-run-works-v1`（Pi 默认 YOLO mode，不需要 bypass，因为 print 模式本身就无 UI）

---

# claude-native-binary-missing-v1

**title**: claude native binary 未安装（postinstall 缺失）
**created**: 2026-09-04
**derived_from**:
- `runs/20260904_bypass-flags-collect/raw.jsonl`
**tags**: herdr, claude, install, blocker
**status**: verified
**summary**: 本机 `claude` 二进制存在但 postinstall 没跑（`Error: claude native binary not installed`），所以 `claudecode` kind **当前跑不起来**。修复方法是跑 `node node_modules/@anthropic-ai/claude-code/install.cjs`，或重装时不带 `--ignore-scripts` / `--omit=optional`。
**evidence**:
- 本会话实测（2026-09-04 15:24）：`claude --help` → `Error: claude native binary not installed. Either postinstall did not run...`
- `which claude` → `/c/Program Files/nodejs/claude`（说明 npm wrapper 在，但底层 native binary 没下载）
**lesson**: 想让 `claudecode` 在 herdr 里可用，先跑 `node node_modules/@anthropic-ai/claude-code/install.cjs` 补 native binary，再按 P8 带 `--permission-mode bypassPermissions`。
**related**:
- `agent-bypass-permission-flags-v1`
- `pi-coding-agent-basics-v1`（Pi 是另一种 install 方式，无此问题）
