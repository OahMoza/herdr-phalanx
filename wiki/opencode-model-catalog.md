---
id: opencode-model-catalog
title: OpenCode model catalog and worker configuration
status: verified
platform: windows
date: 2026-09-11
opencode_version: v0.8.2
herdr_version: 0.9.0
---

# OpenCode 模型目录与 Worker 配置

## 可用模型清单

运行 `opencode models` 获取本机已配置的所有模型：

| Provider | Model | 备注 |
|---|---|---|
| `opencode` | big-pickle | OpenCode 自有 |
| `opencode` | ling-3.0-flash-fin-free | 免费 |
| `opencode` | mimo-v2.5-free | 免费 |
| `opencode` | muse-spark-1.2-contributor-free | 免费 |
| `opencode` | muse-spark-1.3-contributor-free | 免费 |
| `opencode` | nemotron-3-ultra-free | 免费 |
| `opencode` | nemotron-3.5-lightning-free | 免费 |
| `opencodexx` | gpt-5.6-luna | 1.1M context，多模态 |
| `opencodexx` | gpt-5.6-sol | 1.1M context，多模态 |
| `opencodexx` | gpt-5.6-terra | 1.1M context，多模态 |
| `opencodexx` | gpt-6-astra | 1.1M context，多模态 |
| `company` | deepseek-v4-flash | 1M context |
| `longcat` | LongCat-2.0 | 1M context，当前默认 |
| `minimax` | MiniMax-M2 | |
| `minimax` | MiniMax-M2.1 | |
| `minimax` | MiniMax-M2.5 | |
| `minimax` | MiniMax-M2.5-highspeed | 快速 |
| `minimax` | MiniMax-M2.7 | 204.8K context |
| `minimax` | MiniMax-M2.7-highspeed | 快速 |
| `minimax` | MiniMax-M3 | 1M context，多模态 |
| `stealth` | stealth/ox-alpha | |

## 按强度分级

### Light（轻量快速）

适用于：简单 ack、状态检查、文件读取、单步命令。

| Model | 特点 |
|---|---|
| opencode/ling-3.0-flash-fin-free | 免费，快速 |
| opencode/nemotron-3.5-lightning-free | 免费，快速 |
| minimax/MiniMax-M2.5-highspeed | 免费，快速 |
| minimax/MiniMax-M2.7-highspeed | 快速 |
| company/deepseek-v4-flash | 1M context，小输出 |

### Medium（均衡）

适用于：中等复杂度实现、多文件修改、调试。

| Model | 特点 |
|---|---|
| **longcat/LongCat-2.0** | 1M context，当前默认 |
| minimax/MiniMax-M2.7 | 204.8K context |
| minimax/MiniMax-M3 | 1M context，多模态 |
| opencode/big-pickle | OpenCode 自有 |

### Heavy（重度推理）

适用于：复杂架构设计、深度调试、长链路推理、跨系统变更。

| Model | 特点 |
|---|---|
| opencodexx/gpt-6-astra | 1.1M context，多模态 |
| opencodexx/gpt-5.6-luna | 1.1M context，多模态 |
| opencodexx/gpt-5.6-sol | 1.1M context，多模态 |
| opencodexx/gpt-5.6-terra | 1.1M context，多模态 |

## 启动不同强度的 OpenCode Worker

### 1. 启动 Agent（Herdr 层）

```powershell
# Light
herdr agent start oc-light --kind opencode --pane <pane1> -- --model minimax/MiniMax-M2.7-highspeed --auto

# Medium（默认 LongCat-2.0）
herdr agent start oc-medium --kind opencode --pane <pane2> -- --auto

# Heavy
herdr agent start oc-heavy --kind opencode --pane <pane3> -- --model opencodexx/gpt-6-astra --auto
```

### 2. 注册到 Bus

```powershell
python db/agent_bus.py worker-register `
  --worker-id w-oc-light --agent-kind opencode --profile light `
  --agent-name oc-light `
  --model minimax/MiniMax-M2.7-highspeed --intensity light `
  --launch-args=--auto

python db/agent_bus.py worker-register `
  --worker-id w-oc-medium --agent-kind opencode --profile medium `
  --agent-name oc-medium `
  --model longcat-2.0 --intensity medium `
  --launch-args=--auto

python db/agent_bus.py worker-register `
  --worker-id w-oc-heavy --agent-kind opencode --profile heavy `
  --agent-name oc-heavy `
  --model opencodexx/gpt-6-astra --intensity heavy `
  --launch-args=--auto
```

### 3. 按强度路由

```powershell
python db/agent_bus.py enqueue --caller smoke --agent-kind opencode --profile light --payload '{"instruction":"..."}'
python db/agent_bus.py enqueue --caller smoke --agent-kind opencode --profile medium --payload '{"instruction":"..."}'
python db/agent_bus.py enqueue --caller smoke --agent-kind opencode --profile heavy --payload '{"instruction":"..."}'
```

## Windows PowerShell 兼容性

**状态**：✅ 已验证，完全兼容。

OpenCode 的 shell tool 自动检测 PowerShell 环境并生成正确的语法：
- 使用 `$env:VAR` 而非 `${VAR}`
- 使用 `New-Item`、`Out-File` 等 PowerShell cmdlet
- 使用 `& "..."` 调用可执行文件

**实测结果**：claim → deliver → 写 artifact → complete → succeeded，全链路通过。

## 注意事项

- **--auto 必须带**：OpenCode 会弹权限框，无人值守时必须传此参数。
- **Herdr 不暴露 model**：同 Pi，Bus 的 model 字段靠注册时手动声明。
- **Model 切换**：已启动的 OpenCode agent 不能热切换 model，需要 restart。
- **与 Pi 的区别**：OpenCode 用 `--model provider/id` 格式（如 `longcat/LongCat-2.0`），Pi 用 `--model <pattern>` 模糊匹配。
