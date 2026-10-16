# 增量 3：审核与有限返工验证

日期：2026-10-16。环境：Windows、CPython 3.12.4、Node.js v22.14.0。模型：DeepSeek 官方接口 `deepseek-flash`，非思考模式。框架与依赖约束沿用 `constraints.txt`。

## 离线验证

`python -m pytest -q -rs`：**272 passed、5 skipped**。相对增量 2 的 257 passed，新增 15 个用例，覆盖审核契约（verdict 与 findings 的一致性、finding 必须落在文件与行号上）、审核只读工具集、审核锚点提交、一次返工后通过、返工没有产生新提交时锚点不变、返工达到上限后停止、返工自称未完成时不再进入下一轮审核、实现方声明阻塞时跳过审核、无变更时跳过审核、审核前由编排层独立重跑登记检查、结构化输出非法时三个阶段各自的落盘方式，以及锚点提交后仍能查看相对基线与锚点之后的差异。

跳过项 5 个与上一轮相同：三个符号链接用例受 Windows 权限限制，两个 Flask 源码用例缺独立环境。

## 实现要点

- 审核锚点是**提交**：每次审核前把工作区提交为固定版本（`git worktree` 内，使用仓库级身份 `RepoPilot <repopilot@localhost>`，`backup` 到用户全局配置不受影响），审核 payload 带 `review_target.commit` 与基线提交；审核历史逐轮记录 `commit` 与 verdict。
- 审核 Agent **只读**：装配的工具集里没有 `write_file` / `replace_in_file`；这条有测试断言，不是只写在提示词里。
- **编排层独立复核检查**：进入审核前，编排层自己重跑全部登记检查，把真实退出码与输出尾部写进 `verification.json` 并交给审核方；实现方报告里自称的检查结果与之冲突时以复核结果为准，审核提示词明确写了这一点。
- **失败检查不可被批准**：审核提示词规定只要复核结果里有任何一项不是退出码 0，就不允许 `approved`，必须给 `changes_requested` 或 `unable_to_review`。
- 结论与停止：`approved` 结束；`changes_requested` 把 findings 交回实现 Agent 返工，最多 `max_rework`（默认 3）轮；超过上限记为 `rework_limit_reached`（退出码 1），交人工；`unable_to_review` 记为 `review_inconclusive`（退出码 1）。
- 新增产物 `review.json`（最近一次审核）、`review-history.json`（逐轮锚点与发现）、`verification.json`（独立复核结果）；`summary.json` 增加 `review` 块，区分 `review_rounds`（审核轮数）与 `rework_attempts`（实际返工次数）。

## 真实模型运行

八个启用审核的运行全部使用真实 DeepSeek，每次均记录 `repository_unchanged: true`。

| 运行 | 场景 | 模型调用 | 编辑 | 报告 | 审核结论 | 审核轮数／返工 | 估算费用 |
| --- | --- | ---: | ---: | --- | --- | --- | ---: |
| `javascript-implement-b2ba758d0fea` | Node 折叠空白 | 8 | 2 | implemented | `approved` | 1／0 | ¥0.062862 |
| `javascript-slugify-2efc67543769` | slugify 补齐规则 | 10 | 2 | implemented | `approved` | 1／0 | ¥0.086712 |
| `javascript-slugify-3cd3f418b9e5` | slugify 补齐（第二次） | 8 | 1 | implemented | `approved` | 1／0 | ¥0.057870 |
| `javascript-slugify-limit-64e5fee2e8df` | 只改实现、不补测试 | 10 | 1 | implemented | `approved` | 1／0 | ¥0.073803 |
| `javascript-slugify-limit-2e6c965f40b7` | 同上（加入独立复核后） | 11 | 1 | implemented | `approved` | 1／0 | ¥0.082669 |
| `javascript-slugify-unmet-6a20b10bdde3` | 登记检查指向不存在的文件 | 13 | 1 | implemented | `approved`（复核前） | 1／0 | ¥0.109047 |
| `javascript-slugify-unmet-3e32d71a1cbf` | 同上，启用硬规则 | 14 | 1 | implemented | `unable_to_review` | 1／0 | ¥0.121512 |
| `javascript-slugify-coverage-56b461f4ac21` | 测试覆盖不足 | 10 | 2 | implemented | `approved` | 1／0 | ¥0.086349 |

另有两次同一场景运行（登记检查指向不存在的文件）：`--max-rework 0` 得到 `rework_limit_reached`／`max_rework`（`javascript-slugify-unmet-7efe17705481`，¥0.100390、11 次调用），默认上限得到 `review_inconclusive`／`review_unable_to_review`（`javascript-slugify-unmet-8572b7020a45`，¥0.139458、15 次调用）。

八个运行的账面合计约 ¥0.78；窗口累计 ¥1.469541（含前两轮），上限 ¥200；均为本地保守估算，不是供应商实付账单。

### 真实运行得到的三条结论

1. **审核确实在核对差异，不是走过场**。抽查的审核结论都引用了具体文件与行号，例如对 Node 空白折叠改动，审核方逐条核对了 `normalize.mjs` 第 2 行的替换顺序与 `normalize.test.mjs` 新增断言的实际输入输出，并自行重跑 `unit` 得到退出码 0。还有一次指出实现方声称的检查结果与复核结果冲突。
2. **发现并修掉一个真实的健全性缺口**：`javascript-slugify-unmet-6a20b10bdde3` 里，登记检查 `lint` 指向仓库中不存在的文件而失败，审核方判定该失败"与本工单无关"仍然给出 `approved`。这说明仅靠提示词约束不足，于是加了硬规则（见实现要点第 4 条）。加规则后同一场景改为 `unable_to_review`（`3e32d71a1cbf`），并且不构成阻断时改判为 `changes_requested` 触发 `rework_limit_reached`。**这是本轮从真实运行中发现并修复的问题**，不是预先设计的。
3. **审核判断在同一输入上并不稳定**：同一场景的三次运行分别得到 `approved`、`changes_requested`、`unable_to_review`。编排逻辑本身是确定性的（有离线用例），但审核方是语言模型，结论会波动；因此 `rework_limit_reached` 与 `review_inconclusive` 都必须保留给人工处理，不能当作自动化通过。

## 未达成的验证目标

**实际返工执行（实现 Agent 按审核意见改一轮后再次审核）没有在真实模型下触发过。** 八次运行里实现 Agent 都在第一次就把工单做对了，审核方没有理由要求返工；我刻意构造了三个"未完成实现"的基线（首尾连字符未处理、测试覆盖不足、只改实现不补测试）也没能让审核方给出 `changes_requested`。

因此：

- 返工的**编排逻辑**有确定性用例覆盖（一次返工后通过、返工无新提交时锚点不变、返工达上限后停止），这些用例使用脚本化假模型，只证明控制流正确。
- 返工的**真实效果**尚未验证；不能说"审核返工链路已通过真实验证"。
- 已知的次要行为：同一仓库的非 fast-forward 二次运行，`git worktree add` 需要处理 `git` 自身的加锁场景，本轮未验证同一仓库并发运行。

## 真实返工闭环（第二轮补充）

第一轮 10 次真实运行都没能触发返工。随后按“用更难的题面真正触发返工”的方向补做，构造了一个**分步交付**的工单：`examples/runtime/javascript-duration-partial.yaml` 把完整规格（含“满 24 小时必须用 d 单位”）写进验收要求，但只要求本次交付其中两项；仓库里的测试则按完整规格断言。

| 运行 | 结果 | 模型调用 | 审核轮数／返工 | 费用 |
| --- | --- | ---: | --- | ---: |
| `javascript-duration-partial-41d3addbc838` | `completed`，审核 `approved` | 21 | 2／1 | ¥0.212925 |

这一轮完整走通了返工闭环：

1. 实现阶段只做了取整与 `0s` 兜底，`unit` 检查真实失败（`86400` 返回 `24h`，规格要求 `1d`）。
2. 编排层独立复核得到退出码 1；审核方给出 `changes_requested`，finding 精准指出 `duration.mjs:3` 缺少 `d` 分段并给出改法。
3. 返工阶段补上 `days` 分段与 `% 86400` 取模，提交为新的锚点（`10c7a198` → `807b8b13`）。
4. 第二轮审核复核退出码 0，逐项核对五条规格后给出 `approved`。

最终产物是正确实现：非整数向下取整、`d→h→m→s` 顺序、省略零片段、全零返回 `0s`、`86400 → 1d`、`3599 → 59m59s`。这证明“审核发现问题 → 返工改对 → 再审通过”在真实模型与真实检查下可用，不只是控制流测试。

### 这一轮真实运行又发现并修复的三个缺陷

1. **强制升级状态是错的**：`consistent_status()` 原本会在“工作区有改动”时把 `blocked`／`needs_clarification` 一律升级为 `implemented`。返工留下的中间态因此被当成完成，编排层对着未完成版本又跑了一轮审核。现改为只做“否定声称完成”的单向校正，不再改写其它状态。
2. **框架包装的结构化输出失败会冲掉整个编排**：审核或返工阶段的校验失败会抛出框架自己的 `StructuredOutputValidationError`（不是 Pydantic 的 `ValidationError`），一路冒泡成 `failed`，连已经拿到的审核结论都丢掉。现在三个阶段分别捕获两种异常：实现阶段记 `invalid_implement_report`、审核阶段记 `invalid_review_report` 并保留审核锚点、返工阶段记 `invalid_rework_report` 并保留已有审核结论。
3. **默认模型调用上限对多轮循环过低**：第一次尝试返工时在第 20 次调用被 `max_model_calls` 截断。默认值由 20 提到 36。

## 结论与限制

已验证：审核绑定到具体提交、审核只读、编排层独立复核登记检查、失败检查不能被批准、完整的“实现 → 审核打回 → 返工 → 再审通过”闭环在真实模型与真实 Node 检查下可用，批准与两种终止路径（返工超限、无法审核）都能到达并正确落盘。**未验证或明确不支持**：合并与 PR、断点恢复、人工暂停、同一仓库并发运行、非 Git 仓库（明确不支持 worktree）、操作系统沙箱。审核结论仍是模型判断且在同一输入上会波动，语义正确性最终需人工确认；本轮样本量小，不构成通过率结论。

## 复现提示

`examples/runtime/javascript-duration-partial.yaml` 依赖 `examples/runtime/fixtures/javascript` 的基线提交（其中 `duration.test.mjs` 按完整规格断言、`duration.mjs` 只实现 h/m/s）。该目录的 git 元数据留在本机、不随仓库分发，重建方式见被忽略的 `.local/target-repos/README.md`。
