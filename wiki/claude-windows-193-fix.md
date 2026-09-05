# claude-windows-start-193-fix-v1

**title**: Windows 上 claudecode 在 herdr 启动报 Win32 193 的两层根因与修复（已 verified）
**created**: 2026-09-04
**derived_from**:
- 本会话完整修复 + herdr 冒烟实测（2026-09-04 22:52）
- 接替并修正 `claude-native-binary-missing-v1`（那条只发现第一层 postinstall 根因，漏了第二层 shim 193）
**tags**: herdr, claude, windows, npm-shim, start-process, error-193, verified, fix
**status**: verified
**summary**: `herdr agent start --kind claude` 超时、pane 里 PowerShell 报 `Start-Process: %1 is not a valid Win32 application`（Win32 错误 193 / ERROR_BAD_EXE_FORMAT）。根因有两层，只修第一层不够：

1. **native binary 缺失**：`@anthropic-ai/claude-code` 是 wrapper，真二进制（~208MB）在 optionalDependency `@anthropic-ai/claude-code-win32-x64`，由 postinstall `install.cjs` 复制到 `bin/claude.exe`。postinstall 没跑时 `bin/claude.exe` 只是 500 bytes 的占位 shell 脚本（`echo "Error: claude native binary not installed."`）。
2. **npm shim 干扰 Start-Process（更隐蔽）**：npm 全局装在 `C:\Program Files\nodejs\` 生成无扩展名 `claude`（Unix sh）、`claude.cmd`、`claude.ps1`。herdr 在 pane 执行 `Start-Process -FilePath claude`（不带扩展名）。对照实验证实：同目录 `claude.cmd`（文本）与 `claude.exe`（PE）并存时，Start-Process 误选 .cmd 当二进制直接加载 → 193；它不像交互式 shell 按 PATHEXT(.EXE 优先 .CMD)。

**evidence**:
- 修复前：`bin/claude.exe` = 500 bytes，文件头 ASCII 为 `echo "Error: claude native binary not installed."`
- `npm i -g @anthropic-ai/claude-code --include=optional`（registry.npmmirror.com，32s，added 2 packages）后 `bin/claude.exe` = 217,771,680 bytes，`claude --version` = `2.1.260 (Claude Code)`
- 对照实验（干净临时目录）：`Start-Process realclaude`（claude.exe 副本、无扩展名调用）exit 0 正常 → 证明 Start-Process 无扩展名机制本身没坏，是 PATH 中 shim 污染
- 决定性实验：nodejs 目录只留 claude.exe（.cmd/.ps1/无扩展名 sh 全改名 .bak）后，`Start-Process -FilePath claude --version`（cwd=E:\WorkSpace、系统真实 PATH）exit 0 返回 2.1.260；恢复 .cmd 则立刻复现 193
- herdr 冒烟全链路：`agent start cc-smoke --kind claude -- --permission-mode bypassPermissions` → `interactive_ready:true`、terminal_title `✳ Claude Code`、argv 正确透传；`agent prompt "Reply with exactly: CLAUDE_SMOKE_OK" --wait` → done（3s）；`agent read` 见 `● CLAUDE_SMOKE_OK`、模型 Opus 4.8 (1M)、底部 `⏵⏵ bypass permissions`

**fix-steps（可直接复制，PowerShell 管理员）**:
```powershell
# 第一层：补装 native binary
npm install -g @anthropic-ai/claude-code --include=optional
# 第二层：PATH 目录只留 claude.exe，npm shim 改名备份
$nd = "C:\Program Files\nodejs"
Copy-Item "$nd\node_modules\@anthropic-ai\claude-code\bin\claude.exe" "$nd\claude.exe" -Force
Rename-Item "$nd\claude" "claude.unix-sh.bak" -Force          # 无扩展名 Unix sh
Rename-Item "$nd\claude.cmd" "claude.cmd.bak" -Force
Rename-Item "$nd\claude.ps1" "claude.ps1.bak" -Force
```
**lesson**:
- herdr 启动 agent 走 `Start-Process -FilePath <kind>`，**最稳形态是 PATH 目录里有一个与 kind 同名的原生 `.exe`**。omp（`.bun\bin\omp.exe`）、opencode（`scoop\shims\opencode.exe`）、hermes（`bin\hermes.exe`）都是这种形态所以一次成功；凡是只靠 npm `.cmd/.ps1/无扩展名 shim` 的包都可能踩 193。
- 移除 .cmd/.ps1 shim **不影响**交互式 PowerShell / cmd 使用——两者敲 `claude` 会直接命中 claude.exe（已实测）。.bak 保留可回滚。
- 排查 Win32 193 的方法：错误码 193 = "找到了文件但不是有效 PE"，区别于错误码 2 "找不到文件"；用"干净临时目录放 exe 副本对照" + "逐目录二分 PATH" 可快速定位是哪个 shim 抢先命中。
**known-harmless-warning**:
- claudecode 启动/收尾 pane 里出现 `SessionStart / UserPromptSubmit / Stop hook error: Failed with non-blocking status code: No stderr output`，是 claude hook 在 Windows 的非阻塞告警，不影响思考/回复/done，忽略。
**related**:
- `claude-native-binary-missing-v1`（第一层根因，本条补全第二层并标记其修复方案不完整：只跑 install.cjs 仍会被 shim 193 卡住）
- `agent-bypass-permission-flags-v1`（bypass 参数 `--permission-mode bypassPermissions`，本条验证其在 pane 底部显示 "bypass permissions" 生效）
