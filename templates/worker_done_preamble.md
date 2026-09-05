<!--
  Herdr Phalanx — Worker Done Preamble
  注入到 agent prompt 开头，要求 agent 完成后输出标准化完成报告。
  dispatcher 用 pane wait-output --match "## TASK_COMPLETE" 捕获。
  用法：把本文件内容拼接到 agent prompt 前面。
-->

## 任务完成协议（必须遵守）

你完成本任务后，**必须**在回复的最后输出以下标准化代码块（不要用 ``` 包裹，直接输出纯文本）：

```
## TASK_COMPLETE
outcome: succeeded
files_modified: ["path/to/file1.ext", "path/to/file2.ext"]
summary: 做了什么。发现了什么。还剩什么。
```

**字段说明：**
- `outcome`: `succeeded`（成功完成）或 `failed`（失败，必须在 summary 说明原因）
- `files_modified`: JSON 数组，列出你修改/创建/删除的所有文件路径（相对项目根目录）。没有修改文件则写 `[]`
- `summary`: 三句话，分别说明：①做了什么 ②发现了什么（坑/风险/意外）③还剩什么（未完成/需要后续）

**硬规则：**
1. 必须输出 `## TASK_COMPLETE` 标记，dispatcher 靠这个标记检测你完成了
2. outcome 必须是 succeeded 或 failed，不要写其他值
3. files_modified 必须是合法 JSON 数组，用双引号
4. summary 不超过 3 句话
5. 如果你需要提问而不是完成任务，输出 `## TASK_ASK` 标记：
   ```
   ## TASK_ASK
   question: 你的问题
   options: ["选项A", "选项B"] （可选，没有选项就省略）
   ```
6. 不要在 TASK_COMPLETE 之后再输出其他内容
