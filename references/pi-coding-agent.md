# Pi Coding Agent — Core Facts

> 最后核实：2026-09-04。事实来源：pi.dev 官网、npm、GitHub earendil-works/pi。

## 基本信息

- **名称**：Pi Coding Agent（简称 Pi）
- **官网**：https://pi.dev
- **npm 包**：`@earendil-works/pi-coding-agent`
- **GitHub**：earendil-works/pi（monorepo，旧名 badlogic/pi-mono）
- **License**：MIT
- **作者**：Mario Zechner（@badlogic），组织 earendil-works
- **定位**：终端优先的 AI 编程 Agent，不依赖 IDE，全屏 TUI 运行

## 安装

```bash
# npm 全局安装（推荐，--ignore-scripts 跳过依赖生命周期脚本）
npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# 独立安装脚本
curl -fsSL https://pi.dev/install.sh | sh
```

## Monorepo 包结构

| 包名 | 用途 |
|---|---|
| `@earendil-works/pi-coding-agent` | 交互式 coding agent CLI 和 SDK（用户安装这个） |
| `@earendil-works/pi-agent-core` | Agent runtime，tool calling，state management |
| `@earendil-works/pi-ai` | 统一多 provider LLM API（OpenAI / Anthropic / Google / ...） |
| `@earendil-works/pi-tui` | 终端 UI 库，differential rendering |
| `@earendil-works/pi-natives` | N-API 原生绑定（grep / shell / image / syntax highlighting） |

## 运行模式

```bash
pi                  # 交互式 TUI 模式（全屏终端 UI）
pi -p "..."         # print 模式：处理单次 prompt 后退出（stateless）
pi --continue "..." # 继续上一次会话
pi --resume <id>    # 恢复指定会话
pi --model <name>   # 指定模型（fuzzy match："opus" / "gpt-5.2" / "openai/gpt-5.2"）
pi --profile <name> # 使用隔离 profile
```

## 核心特性

- **Hash-anchored edits**：用 2 字符 xxHash32 锚点识别行，防止并发 agent run 互相覆盖
- **LSP 集成**：symbol-aware 编辑，不是盲文本替换
- **DAP debugger**：直接在终端控制调试器
- **Subagent 编排**：fan out 到隔离 worker，typed results 回传父 agent
- **Browser automation**：可控浏览器表面，用于实时文档和 web 测试
- **多 provider 支持**：40+ model providers，支持 mid-session 模型切换
- **持久记忆**：跨会话记忆系统

## 在 Herdr 里怎么用（本机实测结论）

### ✅ 可用：pane run pi -p（stateless 任务）

```bash
# 先 split 新 pane，再 pane run
NEW_PANE=$(herdr pane split --current --direction right --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
herdr pane run "$NEW_PANE" 'pi -p "echo hello from pi"'
```

- 实测通过：w1:p6 跑 `pi -p "echo hello from pi"`，6.3s 返回
- dispatcher pane 不受影响

### ❌ 不可靠：herdr agent start --kind pi（持续对话）

- 实测：60s 超时，herdr 收不到 Pi TUI 的 lifecycle signal，名字被回收
- **推测根因**：未安装 `herdr integration install pi` 时，herdr 用 screen manifest 检测 idle，Pi 全屏 TUI 提示行格式和 herdr 期望的 idle marker 不匹配
- **待验证**：安装 `herdr integration install pi` 后是否能走通 agent start 路线

### 任务分配规则

- **stateless / 一次性任务**（文件扫描、代码 grep、批量分析、git log 解析）→ 给 Pi，走 `pane run pi -p`
- **持续对话 / 多轮 prompt**（开发任务、需要 lifecycle / idle 检测）→ 给 omp，不走 Pi

## 与 OMP（Oh My Pi）的关系

- OMP 是 Pi 的 **coding-first fork**（作者 Can Bölük），不是同一个工具
- OMP 用 Rust 原生引擎重写了核心，保留了 Pi 的 hash-anchored edits、LSP、subagents 等设计
- 两者在 herdr 里是独立的 kind：`--kind pi` 和 `--kind omp`
- 本机：pi 未在 PATH 中（npm 全局安装可能未完成），omp 已安装（v18.0.4，`~/.bun/bin/omp.exe`）
