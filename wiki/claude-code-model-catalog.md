---
id: claude-code-model-catalog
title: Claude Code model catalog and worker configuration
status: partial
platform: windows
date: 2026-09-11
claude_version: v2.1.267
herdr_version: 0.9.0
result: partial
---

# Claude Code 模型目录与 Worker 配置

## 测试状态

**架构不兼容**：Claude Code 是交互式 agent，不支持自动处理传入 prompt。

## 已知问题

### 模型目录隔离

Claude Code 有**独立的模型目录**，与 Pi 的 `pi-claude-cli` provider 不共享。

| 尝试的模型 | 结果 |
|---|---|
| `longcat/LongCat-2.0` | ❌ "may not exist or you may not have access" |
| `minimax/MiniMax-M2.7` | ❌ "may not exist or you may not have access" |
| `claude-opus-4-5` | ❌ "may not exist or you may not have access" |
| `claude-sonnet-4-5` | ❌ "may not exist or you may not have access" |
| `claude-haiku-4-5` | ❌ "may not exist or you may not have access" |
| (default) | ⚠️ 回退到 `gpt-5.6-luna`（无 credits） |

### 默认模型

Claude Code 默认使用 "Haiku with medium effort"，但实际映射到 `gpt-5.6-luna`（可能通过 `behavesAs` 或 `modelOverrides` 配置）。该模型无 credits。

### 架构限制

Claude Code 是**交互式 agent**，设计为人类在环。当 worker-loop 发送 lease prompt 时：

1. ✅ Claude Code 接收并显示 envelope
2. ❌ **不会自动执行命令**——等待用户手动确认
3. ❌ 需要人工干预才能调 `complete`

**对比**：

| Agent | 自动处理传入 prompt | 无需人类确认 |
|---|---|---|
| Pi | ✅ | ✅ |
| OpenCode | ✅ | ✅ |
| OMP | ✅ | ✅ |
| **Claude Code** | ❌ | ❌ |

**结论**：Claude Code **不适合**作为 Bus Worker。它的交互式设计决定了它需要人类在环才能执行命令。Bus Worker 需要全自动处理，Claude Code 无法满足。

如果需要使用 Claude Code，应通过 Phalanx 的 `managed` 模式（Coordinator 直接管理），而非 Bus 的 `worker-loop`。

## 启动 Claude Code Worker

```powershell
# 启动（需要指定有效模型）
herdr agent start claude-test --kind claude --pane <pane> -- --model <valid-model> --dangerously-skip-permissions

# 注册到 Bus
python db/agent_bus.py worker-register `
  --worker-id claude-w1 --agent-kind claude --profile medium `
  --agent-name claude-test `
  --model <model-id> --intensity medium `
  --launch-args="--model <valid-model> --dangerously-skip-permissions"
```

## Bus 集成验证

即使模型有问题，Claude Code 的 Bus 集成能力已验证：
- ✅ 接收 envelope
- ✅ 解析 4 个允许命令 + 5 个禁止命令
- ✅ 理解 lease_id / worker_id 绑定
- ❌ 执行 complete（因模型问题导致 agent 不响应）

## 后续

1. 配置 Claude Code 的模型（通过 `/model` 或配置文件）
2. 确保所选模型有 API credits
3. 重新跑端到端 smoke
