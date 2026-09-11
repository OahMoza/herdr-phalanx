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

## Shell 兼容性

**状态**：OMP ≤ v18.0.4 在 Windows 上有已确认的 bug（[PR #1080](https://github.com/can1357/oh-my-pi/pull/1080) 已修复，需升级）。

### 问题

OMP 使用 `executeBash()` 通过 **brush-core**（嵌入式 Rust bash 解释器）执行所有命令。Brush 对命令字符串应用 POSIX 变量展开，包括双引号内的 `$env`。

当 PowerShell 命令 `Write-Host $env:SystemRoot` 通过 brush 时：
- `$env` 被当作 bash 变量（未定义）→ 展开为空
- 结果变成 `Write-Host :SystemRoot`（空 `$env` + 冒号 + 字面量）

**对比**：Pi 在相同环境下生成正确的 bash 语法（`${VAR}`），工作正常。

### 根因

`executeBash()` 无条件通过 brush 路由所有命令。`getShellConfig()` 返回的 shell 路径仅影响会话键计算和快照检测，不影响实际执行。

### 修复（PR #1080，已合并）

1. **Rust 层修复**：brush 会话创建时定义 `env=$env` 为非导出变量。POSIX 展开 `$env:NAME` 解析为字面量 `$env:NAME`，PowerShell 引用得以保留。
2. **Windows 默认 shell**：分辨率顺序改为 `pwsh.exe` → `powershell.exe` → Git Bash → `bash.exe`。
3. **直接生成路径**：非 bash shell 使用 `executeViaDirectSpawn()` 跳过 brush。

### 解决方案

| 方案 | 操作 | 适用场景 |
|---|---|---|
| **A. 升级 OMP** | `omp update` 到包含 PR #1080 的版本 | **推荐** |
| **B. 用 PowerShell** | `shellPath: "<detect-shell.ps1 输出>"` | 当前版本 workaround |
| **C. 用 WSL bash** | `shellPath: "/bin/bash"`（WSL 内） | WSL 用户 |

**注意**：修改后需要 **restart OMP agent** 才能生效。
