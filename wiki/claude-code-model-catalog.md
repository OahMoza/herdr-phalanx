---
id: claude-code-model-catalog
title: Claude Code model catalog and worker configuration
status: verified
platform: windows
date: 2026-09-11
claude_version: v2.1.267
herdr_version: 0.9.0
result: pass
---

# Claude Code 模型目录与 Worker 配置

## 测试状态

**通过**（需正确模型配置）：Claude Code 可以自动处理传入 prompt，并能自纠正路径错误。

## 端到端测试（LongCat-2.0）

| 步骤 | 结果 |
|---|---|
| 接收 envelope | ✅ |
| 创建 artifact 目录 | ✅ |
| 写 raw.txt + result.json | ✅ |
| 调 complete（首次） | ❌ 反斜杠路径被 bash 吃掉 |
| **调 complete（自纠正重试）** | ✅ 自动修正为正斜杠路径 |
| message → succeeded | ✅ |
| messages_completed = 1 | ✅ |

## 模型配置

### 已知可用模型

| Model | 状态 |
|---|---|
| `LongCat-2.0`（用户重配置后默认） | ✅ 全链路通过 |

### 已知不可用模型

| Model | 问题 |
|---|---|
| `gpt-5.6-luna`（旧默认） | 无 credits |
| `longcat/LongCat-2.0`（显式传） | ❌ 不识别（但默认 LongCat-2.0 可以） |
| `minimax/MiniMax-M2.7` | ❌ 不识别 |
| `claude-opus-4-5` | ❌ 不识别 |
| `claude-sonnet-4-5` | ❌ 不识别 |
| `claude-haiku-4-5` | ❌ 不识别 |

### 默认模型问题

Claude Code 默认模型 `gpt-5.6-luna` 不在其目录中。用户已重新配置为 `LongCat-2.0`。

## 启动 Claude Code Worker

```powershell
# 启动（使用 LongCat-2.0 或用户配置的其他可用模型）
herdr agent start claude-test --kind claude --pane <pane> -- --dangerously-skip-permissions

# 注册到 Bus
python db/agent_bus.py worker-register `
  --worker-id claude-w1 --agent-kind claude --profile medium `
  --agent-name claude-test `
  --model longcat-2.0 --intensity medium `
  --launch-args=--dangerously-skip-permissions
```

## 自纠正能力

Claude Code 在首次 complete 失败后，自动将路径从
`E:\Programs\Python\Python313\python.exe`（反斜杠，bash 吃掉）
修正为 `E:/Programs/Python/Python313/python.exe`（正斜杠）并重试成功。

## 注意事项

- `--dangerously-skip-permissions` 必须带：跳过所有权限检查，无人值守必需
- 模型需在 Claude Code 自己的目录中，Pi 的模型名不一定通用
- 路径自纠正依赖 Claude Code 的推理能力，不是所有 agent 都有
