# 分屏拓扑规则（Grid Topology）

> 2026-09-05 用户定版并实测验证（herdr v0.8.2，`pane layout` 几何坐标确认 2x2 等宽等高）。**这是开分身时的强制布局规则，优先级高于最小工作流步骤 1 的通用 split 写法。**

## 硬规则（T-Grid）

- **T-Grid-1**：同一个 workspace 内，**每个"分身 tab"最多放 4 个 agent 分身，布局固定为 2×2 田字格**。
- **T-Grid-2**：需要第 5 个分身时，**新开一个分身 tab**，从田字 slot 1 重新铺；以此类推，每满 4 个翻一个 tab。禁止在一个 tab 里切出第 5 个 pane。
- **T-Grid-3**：dispatcher（Hermes）**独占一个"指挥 tab"**，不混进分身田字 tab——保证每个分身 tab 都是整齐的 4 格田字，且分身计数 = 纯 agent 数，不被 dispatcher 占槽。
- **T-Grid-4**：所有 split 一律 `--ratio 0.5`（均分）+ `--no-focus`（不抢焦点，承接 C2）。
- **T-Grid-5**：一个分身 = 一个 pane（承接 DAG 硬规则"并行任务禁止共享 pane"）；槽位一旦切好不重排，扩容只做增量 split。

## workspace 内的 tab 角色

```
Workspace wN（一个项目）
├─ Tab「指挥」      : 1 个 pane，跑 Hermes dispatcher（发号施令，本身不是分身）
├─ Tab「团队-1」    : 田字 4 分身（slot 1-4，第 1-4 个分身）
├─ Tab「团队-2」    : 田字 4 分身（slot 1-4，第 5-8 个分身）
└─ Tab「团队-k」    : 第 (4k+1) 个分身起，每 tab 4 个
```

分身序号（从 0 起）到物理位置的映射：
- 分身 tab 序号 = `floor(分身序号 / 4)`（0 → 团队-1，1 → 团队-2 …）
- tab 内槽位 slot = `(分身序号 mod 4) + 1`（1=左上，2=右上，3=左下，4=右下）

## 田字格切分算法（grid-2x2，3 刀，已实测）

新建分身 tab 后拿到 root pane `R`（`herdr tab create --workspace wN --cwd <项目路径> --no-focus` 返回的 `root_pane.pane_id`），按下面顺序切 3 刀：

```bash
# 第 1 刀：root 沿 down 横切 → R=上行，B=下行
B=$(herdr pane split $R --direction down  --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
# 第 2 刀：上行 R 沿 right 竖切 → R=左上(TL/slot1)，TR=右上(slot2)
TR=$(herdr pane split $R --direction right --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
# 第 3 刀：下行 B 沿 right 竖切 → B=左下(BL/slot3)，BR=右下(slot4)
BR=$(herdr pane split $B --direction right --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
```

切完槽位固定：**slot1=左上=`$R`，slot2=右上=`$TR`，slot3=左下=`$B`，slot4=右下=`$BR`**，按 slot1→4 的阅读顺序依次 `agent start`。

实测几何（tab 区域 228×57）：TL/TR 在上半高 29，BL/BR 在下半高 28（奇数行取整差 1，正常），左右各宽 114——标准等宽等高田字。

## 不足 4 个时的渐进布局（增量切分，不返工）

为保证从少到多扩容时**不重排已有 pane**，按田字二叉树的构建顺序增量切：

| 分身数 | 动作（在当前分身 tab） | 形态 |
|---|---|---|
| 1 | 直接用 root pane，不切 | 单格全屏（=slot1 左上） |
| 2 | 对 root `split --direction down --ratio 0.5` | 上下两行（slot1 上 / slot3 下） |
| 3 | 对上半 `split --direction right --ratio 0.5` | 上二下一（slot1,2 / slot3） |
| 4 | 对下半 `split --direction right --ratio 0.5` | **完整田字（slot1-4）** |
| 5 | **新开 tab**，回到"1 个"的流程 | 新 tab slot1 |

> 说明：2 个分身时是上下排列（而非左右），这是为了让第 3、4 个分身只需各补一刀即得到田字，中途不移动任何已启动的 agent。若明确只用 2 个且不会扩容，可改用 `--direction right` 左右排列。

## 扩容操作流程（第 N 个分身加入时）

1. `herdr pane list`（或 `tab list`）数当前 workspace 已有几个**分身 pane**（排除指挥 tab 的 dispatcher pane）。
2. 当前最新分身 tab 未满 4 → 在该 tab 按上表补切下一个 slot；已满 4 → `herdr tab create --workspace <wN> --cwd <项目路径> --no-focus` 开新分身 tab 并走 grid-2x2。
3. 拿到目标 slot 的 pane_id 后 `herdr agent start <name> --kind <k> --pane <slot_pane> -- <bypass 参数>`（bypass 见 P8，Windows npm 类 agent 启动报 193 见 P9）。
4. 用 `herdr pane layout --pane <该 tab 任一 pane>` 核对几何，确认没有第 5 格、比例为 0.5。

---

# 团队形态（Team Topology，可与用户共同敲定）

> 这一段故意留白，等首次实战时跟用户定。当前预留 3 种原型。

- **原型 A：单一全栈** — 1 Developer + 1 QA，ProjectManager 即 Hermes。适合小修小补。
- **原型 B：架构-实现分离** — 1 Architect 出方案，2 Developer 并行实现，1 QA 验证。适合新模块开发。
- **原型 C：红蓝对抗** — 1 Developer vs 1 QA（QA 同时写破坏性测试），PM 仲裁。适合重构 / 安全加固。

切换原型 = 在运行时重新分配 `Role → Agent` 映射，不创建/销毁 agent。
