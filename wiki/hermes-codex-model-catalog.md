---
id: hermes-codex-model-catalog
title: Hermes and Codex model catalog and worker configuration
status: partial
platform: windows
date: 2026-09-11
hermes_version: v0.21.0
codex_version: v0.149.1
herdr_version: 0.9.0
---

# Hermes & Codex 模型目录与 Worker 配置

## Hermes

### 测试状态：✅ 通过

| 步骤 | 结果 |
|---|---|
| claim + deliver | ✅ |
| 创建 artifact 目录 | ✅ |
| 写 raw.txt + result.json | ✅ |
| 调 complete | ✅ 自动用正斜杠路径 |
| message → succeeded | ✅ |
| TASK_COMPLETE 标记 | ✅ 自动发出 |

### 可用模型（Profile）

| Profile | Model |
|---|---|
| default | MiniMax-M3 |
| coding | LongCat-2.0 |
| design | LongCat-2.0 |
| devops | LongCat-2.0 |
| research | LongCat-2.0 |
| testing | MiniMax-M2.7 |

### 启动

```powershell
herdr agent start hermes-test --kind hermes --pane <pane>
python db/agent_bus.py worker-register --worker-id hermes-w1 --agent-kind hermes --profile coding --agent-name hermes-test --model longcat-2.0 --intensity medium
```

## Codex

### 测试状态：❌ 路径权限错误

| 步骤 | 结果 |
|---|---|
| claim + deliver | ✅ |
| 创建 artifact 目录 | ❌ PermissionError |

**问题**：Codex 有自己的项目本地 artifact 配置（`~/.herdr-phalanx/`），忽略了 lease prompt 里的 `ARTIFACT_DIR`。试图写入 `~/.herdr-phalanx/runs/agent-bus/<msg_id>` 被系统拒绝。

### 可用模型

| Model | 状态 |
|---|---|
| gpt-5.6-terra | 默认 |
| gpt-5.6-luna | 低 credit 替代 |

### 后续

需要配置 Codex 允许写入 Bus 指定的临时目录，或修改 `~/.codex/config.toml` 的路径限制。
