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

**部分通过**：Claude Code 可以正确接收和解析 Bus envelope，但模型选择有问题。

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

### 可行的模型

需要通过 `/model` 命令或 Claude Code 的配置界面选择模型。当前未找到可用的模型。

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
