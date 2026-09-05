# WikiSkill 论文浓缩参考卡

## 引用

- **标题**：WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill Evolution
- **作者**：Liyan Tang, Cyrus Rashtchian, Chun-Sung Ferng, Andrew Tomkins, Da-Cheng Juan, Tu Vu
- **机构**：Google Research + Virginia Tech
- **发布**：arXiv 2608.27454，2026-08-27
- **一手链接**：https://arxiv.org/abs/2608.27454
- **社区解读**：https://explainx.ai/blog/wikiskill-persistent-wiki-agent-skill-evolution-august-2026
- **The Decoder 报道**：https://the-decoder.com/google-gives-ai-agents-their-own-wiki-so-they-can-learn-from-mistakes-and-successes/

## 核心问题

大多数 auto-skill-discovery 流水线把"为什么这一步要加"散落在 optimization history 里。下一轮 skill 更新要么重新推导这些洞见，要么干脆忽略——skill 累积变慢。

## 三层模型（核心架构）

```
┌─────────────────────────────────────────────────────┐
│ Skill Layer   可回滚的当前活动程序（agent 真正执行） │
├─────────────────────────────────────────────────────┤
│ Wiki Layer    结构化知识库（失败模式 + 成功策略）     │
│               append-only，按主题去重合并             │
├─────────────────────────────────────────────────────┤
│ Raw Layer     不可变的执行轨迹（每次 run 一份）       │
└─────────────────────────────────────────────────────┘
```

- **Raw Layer** — immutable execution traces。完整保留所有 tool call 和 result。
- **Wiki Layer** — 把 Raw 蒸馏成结构化笔记：失败模式 / 成功策略 / 反复出现的反模式。
- **Skill Layer** — 当前版本的程序。改了不行就回滚。

## 四组件循环

| 组件              | 职责                                                                 |
|-------------------|----------------------------------------------------------------------|
| Inference Agent   | 用当前 skill 跑 task，产生 Raw trace                                 |
| Wiki Maintainer   | 从 trace 抽模式 → 写 wiki（失败也写，append-only）                   |
| Skill Proposer    | 读 wiki + trace → 提议一次 skill 更新（一次一小改，不批量）          |
| Gating + Rollback | 在独立验证集评估。分提高 → 接受；分持平或下降 → 回滚 skill，wiki 不动 |

关键约束：

1. **四步顺序固定，不允许跳过**
2. **Gating 必须用独立验证集**——不能拿提议时用的 trace 当验证集（数据泄漏）
3. **失败的提议也要记到 wiki**，下次 Proposer 不会重蹈覆辙

## 论文报告的实验数据（自评，未独立验证）

| 模型              | Baseline | WikiSkill | Δ        |
|-------------------|----------|-----------|----------|
| Qwen 4B           | 25%      | 37%       | +12.3    |
| Qwen 9B           | 30%      | 47.5%     | +17.5    |
| Qwen 27B          | 39.4%    | 63.3%     | +23.9    |
| Gemma-4-31B       | -        | -         | -        |
| Gemini-3.5-Flash  | 49.5%    | 68.1%     | +18.6    |

5 个 benchmark（LiveMath / web search / SpreadSheet / 长文档 QA / 交互式 embodied 任务）。**9B + WikiSkill 跑赢 27B 裸模型**——累积知识换模型规模。

## 与本 skill 的对接点

- **本体论**：新增 WikiArticle 实体（第 5 类）；新增 Skill─derived_from─→WikiArticle 关系（第 4 类）
- **角色表**：WikiMaintainer / SkillProposer / GatingReviewer 三个角色
- **员工表**：`wikiskill-maintainer` / `wikiskill-proposer` / `wikiskill-gating`（不是新 binary，是现有员工的别名）
- **循环协议**：在 skill 正文 `## Upgrade Hooks → evolution:` 段定义
- **关键约束**：在 skill 正文 `## Pitfalls → P6` 段定义（wiki ≠ skill；append-only；derived_from 强制）

## 已知局限（论文自己也提了）

- 不是一个"模型本身在学"——只是 skill 在变好；模型权重不动
- 自我写入的指令可能带坏习惯并被忠实执行——所以 Gating 重要
- 论文数据来自作者自评，独立复现未见

## 何时启用 WikiSkill 循环

- 至少有 1 个 prototype（A/B/C）实战跑通过
- 至少有 1 个 Inference Agent 真正产出 trace
- wiki/<slug>.md 落盘目录已建好
- 满足以上 3 条 → 把 skill 正文里 `evolution:` 段的 `> 未启用` 标注去掉即可激活
