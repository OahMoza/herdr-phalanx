# grid-2x2-tab-cap-v1

**title**: 一个分身 tab 最多 4 个 agent、2×2 田字布局、超 4 开新 tab（切分算法已实测）
**created**: 2026-09-05
**derived_from**:
- 用户需求（2026-09-05）：一个 workspace 下，一 tab 最多 4 个分身，超出开新 tab，4 个呈田字型
- 本会话临时 tab w1:t6 实测切分 + `pane layout` 几何坐标验证（测完已 tab close）
**tags**: herdr, topology, pane, tab, grid, 2x2, layout, verified, hard-rule
**status**: verified
**summary**: 在同一 workspace 内组织多个分身时，物理布局固定为：dispatcher 独占指挥 tab；每个分身 tab 最多 4 个 agent pane，排成 2×2 田字；第 5 个分身 `tab create` 开新分身 tab。田字通过 3 刀增量二叉切分得到（不是一次指定网格）。

**evidence（herdr v0.8.2，临时 tab root=w1:pE）**:
- 第1刀 `pane split w1:pE --direction down --ratio 0.5` → 上半 pE + 下半 pF
- 第2刀 `pane split w1:pE --direction right --ratio 0.5` → 左上 pE + 右上 pG
- 第3刀 `pane split w1:pF --direction right --ratio 0.5` → 左下 pF + 右下 pH
- `pane layout --pane w1:pE` 几何坐标（tab 区域 228×57）：
  - pE 左上 rect{x:26,y:1,w:114,h:29}
  - pG 右上 rect{x:140,y:1,w:114,h:29}
  - pF 左下 rect{x:26,y:30,w:114,h:28}
  - pH 右下 rect{x:140,y:30,w:114,h:28}
  - splits 树：split_0_root(down,0.5) → split_1_0(right,0.5) + split_2_1(right,0.5)
  - 左右各宽 114（均分），上下 29/28（奇数行取整差 1，正常）→ 标准等宽等高田字
- `tab create --workspace w1 --cwd --no-focus` 返回 `.result.root_pane.pane_id` + `.result.tab.tab_id`；`tab close <id>` 会把其内 pane 一并回收（已验证 t6 关闭后 tab list 只剩 t3）
- `tab focus <id>` **不接受** --no-focus（传了报 usage 错误）

**切分算法（可直接复制）**:
```bash
R=<tab create 返回的 root_pane>
B=$(herdr pane split $R --direction down  --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
TR=$(herdr pane split $R --direction right --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
BR=$(herdr pane split $B --direction right --ratio 0.5 --cwd "$PWD" --no-focus | jq -r .result.pane.pane_id)
# slot1 左上=$R  slot2 右上=$TR  slot3 左下=$B  slot4 右下=$BR
```

**lesson**:
- herdr split 是二叉增量切分，源 pane 保留并收缩、新 pane 从 JSON 拿 ID；要得到规整 2×2 必须"先横后竖"：先 down 切两行，再分别对两行 right 切。先竖后横会得到"左一整列 + 右上下两格"的非田字形态。
- 增量扩容（1→2→3→4）按田字构建顺序切才不返工：1 全屏；2 用 down（上下）；3 对上半 right（上二下一）；4 对下半 right（田字）。若第 2 个用 right 左右排，第 3、4 个无法只靠增量补成田字，需要重排。
- 分身序号 n（从 0 起）：分身 tab = floor(n/4)，槽位 = n mod 4；每满 4 个必须开新 tab，禁止一 tab 第 5 格。
- 校验布局用 `pane layout --pane <该 tab 任一 pane>`（默认只显示当前 focused tab 的布局，跨 tab 必须带 --pane）。
**related**:
- `agent-bypass-permission-flags-v1`（每个槽位 agent start 时仍要带对应 bypass 参数）
- `claude-windows-start-193-fix-v1`（槽位里起 npm 类 agent 的 Windows 193 坑）
