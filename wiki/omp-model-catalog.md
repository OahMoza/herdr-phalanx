---
id: omp-model-catalog
title: OMP model catalog and worker configuration
status: verified
platform: windows
date: 2026-09-11
omp_version: v18.0.4
herdr_version: 0.9.0
---

# OMP 模型目录与 Worker 配置

## 可用模型清单

来源：`~/.omp/agent/models.yml`（本机已配置的 provider + model）。

| Provider | Model | Context | Max Out | Reasoning | Images | Thinking Effort |
|---|---|---|---|---|---|---|
| `longcat` | LongCat-2.0 | 1M | 16K | yes | no | — |
| `company` | deepseek-v4-flash | 1M | 16K | yes | yes | — |
| `minimax` | MiniMax-M3 | 1M | 128K | yes | yes | — |
| `minimax` | MiniMax-M2.7 | 204.8K | 128K | yes | no | — |
| `minimax` | MiniMax-M2.7-highspeed | — | — | no | no | — |
| `opencodexx` | gpt-6-astra | 1.05M | 128K | yes | yes | low/medium/high/xhigh/max |
| `opencodexx` | gpt-5.6-sol | 1.05M | 128K | yes | yes | low/medium/high/xhigh/max |
| `opencodexx` | gpt-5.6-terra | 1.05M | 128K | yes | yes | low/medium/high/xhigh/max |
| `opencodexx` | gpt-5.6-luna | 1.05M | 128K | yes | yes | low/medium/high/xhigh/max |

## OMP 特有角色模型

OMP 支持三种"角色模型"，可以在一次会话中切换：

| Flag | 用途 | 推荐模型 |
|---|---|---|
| `--smol` | 轻量快速任务（简单编辑、文件读取） | MiniMax-M2.7-highspeed, deepseek-v4-flash |
| `--slow` | 深度推理（复杂调试、架构设计） | gpt-6-astra, claude-opus-5 |
| `--plan` | 规划模式（只读分析、制定计划） | LongCat-2.0, MiniMax-M3 |

## 按强度分级

### Light（轻量快速）

| Model | 特点 |
|---|---|
| MiniMax-M2.7-highspeed | 无 reasoning，最快 |
| deepseek-v4-flash | 1M context，16K 输出，有 reasoning |

### Medium（均衡）

| Model | 特点 |
|---|---|
| **LongCat-2.0** | 1M context，当前 OMP 默认 |
| MiniMax-M2.7 | 204.8K context，128K 输出 |
| MiniMax-M3 | 1M context，128K 输出，多模态 |

### Heavy（重度推理）

| Model | 特点 |
|---|---|
| gpt-6-astra | 1.05M context，128K 输出，5 级 thinking effort |
| gpt-5.6-sol / terra / luna | 1.05M context，5 级 thinking effort |

## 启动不同强度的 OMP Worker

### 1. 启动 Agent（Herdr 层）

```powershell
# Light
herdr agent start omp-light --kind omp --pane <pane1> -- --model minimax-m2.7-highspeed --auto-approve

# Medium（默认 LongCat-2.0）
herdr agent start omp-medium --kind omp --pane <pane2> -- --auto-approve

# Heavy
herdr agent start omp-heavy --kind omp --pane <pane3> -- --model gpt-6-astra --auto-approve
```

### 2. 注册到 Bus

```powershell
python db/agent_bus.py worker-register `
  --worker-id w-omp-light --agent-kind omp --profile light `
  --agent-name omp-light `
  --model minimax-m2.7-highspeed --intensity light `
  --launch-args "--auto-approve"

python db/agent_bus.py worker-register `
  --worker-id w-omp-medium --agent-kind omp --profile medium `
  --agent-name omp-medium `
  --model longcat-2.0 --intensity medium `
  --launch-args "--auto-approve"

python db/agent_bus.py worker-register `
  --worker-id w-omp-heavy --agent-kind omp --profile heavy `
  --agent-name omp-heavy `
  --model gpt-6-astra --intensity heavy `
  --launch-args "--auto-approve"
```

### 3. 按强度路由

```powershell
python db/agent_bus.py enqueue --caller smoke --agent-kind omp --profile light --payload '{"instruction":"..."}'
python db/agent_bus.py enqueue --caller smoke --agent-kind omp --profile medium --payload '{"instruction":"..."}'
python db/agent_bus.py enqueue --caller smoke --agent-kind omp --profile heavy --payload '{"instruction":"..."}'
```

## 注意事项

- **--auto-approve 必须带**：OMP 会弹权限框，无人值守时必须传此参数（或 `--approval-mode yolo`）。
- **Herdr 不暴露 model**：同 Pi，Bus 的 model 字段靠注册时手动声明。
- **Model 切换**：已启动的 OMP agent 不能热切换 model，需要 restart。
- **API Key**：各 provider 的 key 存在 `~/.omp/agent/models.yml` 中。
- **Profile 命名**：建议用 `light` / `medium` / `heavy` 作为 profile，与 intensity 对齐。
- **与 Pi 的区别**：OMP 用 `--model <id>` 直接指定，不需要 `provider/id` 格式（provider 由 models.yml 自动匹配）。

## Windows Shell 配置

**状态**：已修复。

**问题**：OMP 默认 `shellPath` 指向 Git Bash（`E:\Programs\Git\bin\bash.exe`），但用户日常使用 PowerShell。OMP 在 Git Bash 上生成 CMD 语法导致变量展开失败、路径转义错误。

**修复**：将 `~/.omp/agent/settings.json` 的 `shellPath` 改为 `scripts/detect-shell.ps1` 检测到的路径：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/detect-shell.ps1
# 输出当前平台应使用的 shell 路径
```

然后将输出写入 `settings.json`：
```json
{
  "shellPath": "<detect-shell.ps1 的输出>"
}
```

**注意**：修改后需要 **restart OMP agent** 才能生效（已启动的 agent 仍使用旧 shell）。
