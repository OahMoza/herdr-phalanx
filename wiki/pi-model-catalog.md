---
id: pi-model-catalog
title: Pi model catalog and worker configuration
status: verified
platform: windows
date: 2026-09-11
pi_version: via `pi --list-models`
herdr_version: 0.9.0
---

# Pi 模型目录与 Worker 配置

## 可用模型清单

运行 `pi --list-models` 获取本机已配置的所有模型：

| Provider | Model | Context | Max Out | Thinking | Images |
|---|---|---|---|---|---|
| `company` | deepseek-v4-flash | 1M | 12.8K | yes | no |
| `longcat` | LongCat-2.0 | 1M | 128K | yes | no |
| `minimax` | MiniMax-M2.7 | 204.8K | 131.1K | yes | no |
| `minimax` | MiniMax-M2.7-highspeed | 128K | 16.4K | no | no |
| `minimax` | MiniMax-M3 | 500K | 131.1K | yes | no |
| `opencodexx` | gpt-5.6-luna | 1.1M | 128K | yes | yes |
| `opencodexx` | gpt-5.6-sol | 1.1M | 128K | yes | yes |
| `opencodexx` | gpt-5.6-terra | 1.1M | 128K | yes | yes |
| `opencodexx` | gpt-6-astra | 1.1M | 128K | yes | yes |
| `pi-claude-cli` | claude-fable-5 | 1M | 128K | yes | yes |
| `pi-claude-cli` | claude-fable-5-1 | 1M | 128K | yes | yes |
| `pi-claude-cli` | claude-haiku-4-5 | 200K | 64K | yes | yes |
| `pi-claude-cli` | claude-haiku-4-5-20251001 | 200K | 64K | yes | yes |
| `pi-claude-cli` | claude-opus-4-5 | 200K | 64K | yes | yes |
| `pi-claude-cli` | claude-opus-4-5-20251101 | 200K | 64K | yes | yes |
| `pi-claude-cli` | claude-opus-4-6 | 1M | 128K | yes | yes |
| `pi-claude-cli` | claude-opus-4-7 | 1M | 128K | yes | yes |
| `pi-claude-cli` | claude-opus-4-8 | 1M | 128K | yes | yes |
| `pi-claude-cli` | claude-opus-5 | 1M | 128K | yes | yes |
| `pi-claude-cli` | claude-sonnet-4-5 | 1M | 64K | yes | yes |
| `pi-claude-cli` | claude-sonnet-4-5-20250929 | 1M | 64K | yes | yes |
| `pi-claude-cli` | claude-sonnet-4-6 | 1M | 128K | yes | yes |
| `pi-claude-cli` | claude-sonnet-5 | 1M | 128K | yes | yes |

## 按强度分级

### Light（轻量快速）

适用于：简单 ack、状态检查、文件读取、单步命令。

| Model | 特点 |
|---|---|
| MiniMax-M2.7-highspeed | 无 thinking，128K context，最快 |
| deepseek-v4-flash | 1M context，有 thinking，小输出 |
| MiniMax-M2.7 | 204.8K context，均衡 |
| claude-haiku-4-5 | 200K context，64K 输出，Claude 最快 |

### Medium（均衡）

适用于：中等复杂度实现、多文件修改、调试。

| Model | 特点 |
|---|---|
| LongCat-2.0 | 1M context，128K 输出，当前 Pi 默认 |
| MiniMax-M3 | 500K context，有 thinking |
| claude-sonnet-4-5 | 1M context，64K 输出 |
| claude-sonnet-4-6 | 1M context，128K 输出 |

### Heavy（重度思考）

适用于：复杂架构设计、深度调试、长链路推理、跨系统变更。

| Model | 特点 |
|---|---|
| claude-opus-5 | 1M context，最强 Claude |
| claude-opus-4-8 | 1M context，上一代旗舰 |
| claude-opus-4-7 / 4-6 | 1M context，深度推理 |
| gpt-6-astra | 1.1M context，128K 输出，多模态 |
| gpt-5.6-luna / sol / terra | 1.1M context，OpenCodeXX 系列 |

## 启动不同强度的 Pi Worker

### 1. 启动 Agent（Herdr 层）

```powershell
# Light — 快速响应
herdr agent start pi-light --kind pi --pane <pane1> -- --model minimax-m2.7-highspeed

# Medium — 均衡（默认 LongCat-2.0）
herdr agent start pi-medium --kind pi --pane <pane2>

# Heavy — 深度推理
herdr agent start pi-heavy --kind pi --pane <pane3> -- --model claude-opus-5
```

### 2. 注册到 Bus（声明 model + intensity）

```powershell
python db/agent_bus.py worker-register `
  --worker-id w-light --agent-kind pi --profile light `
  --agent-name pi-light `
  --model minimax-m2.7-highspeed --intensity light

python db/agent_bus.py worker-register `
  --worker-id w-medium --agent-kind pi --profile medium `
  --agent-name pi-medium `
  --model longcat-2.0 --intensity medium

python db/agent_bus.py worker-register `
  --worker-id w-heavy --agent-kind pi --profile heavy `
  --agent-name pi-heavy `
  --model claude-opus-5 --intensity heavy
```

### 3. 按强度路由（Producer 层）

```powershell
# 简单任务 → Light
python db/agent_bus.py enqueue --caller smoke --agent-kind pi --profile light `
  --payload '{"instruction":"Check git status","workspace":"E:\\WorkSpace\\github\\myproject"}'

# 中等任务 → Medium
python db/agent_bus.py enqueue --caller smoke --agent-kind pi --profile medium `
  --payload '{"instruction":"Refactor the auth module","workspace":"E:\\WorkSpace\\github\\myproject"}'

# 复杂任务 → Heavy
python db/agent_bus.py enqueue --caller smoke --agent-kind pi --profile heavy `
  --payload '{"instruction":"Design and implement the new payment system","workspace":"E:\\WorkSpace\\github\\myproject"}'
```

### 4. 监控 Worker 状态

```powershell
python db/agent_bus.py worker-list
# → 显示每个 worker 的 model / intensity / messages_completed / status
```

## 注意事项

- **Herdr 不暴露 model**：`herdr agent get` 不返回 model 字段，Bus 的 model 字段靠注册时手动声明。
- **Model 切换**：已启动的 Pi agent 不能热切换 model，需要 restart。
- **API Key**：不同 provider 需要对应的 API key（`pi auth` 管理）。
- **Profile 命名**：建议用 `light` / `medium` / `heavy` 作为 profile，与 intensity 对齐。
