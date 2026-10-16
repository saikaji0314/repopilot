# 增量 2：独立工作区与有限编辑验证

日期：2026-10-16。环境：Windows、CPython 3.12.4、Node.js v22.14.0（本轮为本机安装的独立副本）。模型：DeepSeek 官方接口 `deepseek-flash`，非思考模式。框架版本沿用 `constraints.txt` 固定值。

## 离线验证

`python -m pytest -q -rs`：**257 passed、5 skipped**。相对增量 1 的 230 passed、5 skipped，新增 27 个用例，覆盖工作区准备与隔离、编辑路径与内容边界、精确替换语义、编辑次数上限、差异采集与截断、证据契约与状态一致性。真实运行期间测得的是 255 passed、5 skipped，之后为状态一致性补了 2 个用例，因此最终数为 257。

跳过项 5 个：三个符号链接用例因当前 Windows 权限无法创建链接；两个 Flask 源码用例因未准备独立源码测试环境。与增量 1 相同，不能把跳过视为通过。

## 真实模型运行

七个运行全部使用真实 DeepSeek 接口，工作区为真实 git worktree，检查在各自语言的原生工具链下执行。

| 运行 | 工单 | 实际检查 | 编辑 | 变更 | 报告 | 原仓库 | 估算费用 |
| --- | --- | --- | ---: | --- | --- | --- | ---: |
| `flaskr-implement-97cc9019ebdc` | Flaskr 补首页匿名可浏览的测试 | pytest 13 passed | 1 | 1 文件 +8/−0 | `implemented` | 未变 | ¥0.092184 |
| `javascript-implement-f72369fa22c8` | Node 样例折叠内部空白 | `node --test` 通过 | 2 | 2 文件 +3/−1 | `implemented` | 未变 | ¥0.051394 |
| `flask-inspect-67a6536962d3` | Flaskr 只读调查 | pytest 13 passed | 0 | 无 | `implemented`（错误） | 未变 | ¥0.057714 |
| `flask-inspect-ff67aafcb83d` | Flaskr 只读调查（状态修正后） | pytest 13 passed | 0 | 无 | `planned` | 未变 | ¥0.053859 |
| `javascript-implement-505efae967f8` | Node 样例复核（状态修正后） | `node --test` 通过 | 2 | 2 文件 +3/−1 | `implemented` | 未变 | ¥0.036696 |
| `flaskr-check-only-4a45fbef39ce` | 只跑检查的对照任务（原仓库摘要改为 git 状态快照后） | pytest 13 passed | 0 | 无 | `planned` | 未变 | ¥0.011550 |
| `javascript-implement-dfe2df6c227b` | Node 实现复核（快照改动后） | `node --test` 通过 | 2 | 2 文件 +3/−1 | `implemented` | 未变 | ¥0.040176 |

七个运行合计 33 次模型调用，本轮账面约 ¥0.343573。窗口累计 ¥0.548869（含增量 1 的 ¥0.205296），窗口上限 ¥200；这些是本地保守估算，不是供应商实付账单。

工作区路径、基线提交与差异文本保存在各运行的 `changes.json`；`summary.json` 记录 `edits`、`changes` 与 `repository_unchanged`。原始材料位于被忽略的 `.local/runtime/`，不随 Git 分发。

### 运行中发现并修复的三个问题

1. **只读运行自称已实现**：`flask-inspect-67a6536962d3` 没有任何文件变更，报告却给出 `implemented`。原因是状态完全由模型措辞决定。修复方式为由本地事实校正：有文件变更才允许 `implemented`，没有变更一律改写为 `planned`（无证据时改写为 `blocked`）。修复后 `flask-inspect-ff67aafcb83d` 正确给出 `planned`，同时 `javascript-implement-505efae967f8` 仍正确给出 `implemented`。
2. **检查名与文件路径混用**：增量 1 的缺陷是在同一个证据结构里同时表达文件位置与检查结果。本轮把证据拆成 `kind: "file"` 与 `kind: "check"`，并要求 `implemented` 至少含一条检查证据；报告均按新契约给出两类证据。
3. **原仓库摘要的代价过高**：最初对仓库可见文件逐个哈希，实测在较大仓库上单次约 3 秒，而每次运行要算两次，会挤占 120 秒运行期限。改为记录 git 自身状态（HEAD 与工作区条目）的快照，实测约 0.05 秒，同时仍能反映工作区改动与未跟踪文件；覆盖范围由“全部可见文件”缩小为“未被忽略的 git 可见变化”，这一点在限制中写明。改动后重跑两个任务确认无回归（`flaskr-check-only-4a45fbef39ce`、`javascript-implement-dfe2df6c227b`）。

### 编辑质量抽查

- Python：新增的是未登录请求首页的用例，断言 200 与标题、正文，没有重复 `test_index` 中登录后才做的断言；检查由 12 passed 变为 13 passed。
- Node：`normalizeTag` 由 `trim().toLowerCase()` 改为 `trim().replace(/\s+/g, ' ').toLowerCase()`，抑制了制表符与换行导致同一标签得到不同结果的问题，并补了两条断言；`node --test` 通过。
- 两次实现运行都只改动了工单允许的文件，`repository_unchanged` 均为 true，说明编辑确实只发生在工作区。

## 结论与限制

已验证：独立 git worktree 工作区（建在原仓库之外、原仓库摘要不变）、受限编辑工具（路径、内容与次数边界）、差异与变更证据、文件证据与检查证据的分离、以及 Python／Node 两条真实链路。

未验证或明确不支持：

- 非 Git 仓库不提供 worktree 工作区（会以 `workspace_error` 停止），不再退回直接修改原仓库。
- 合并、PR 创建、审核返工、断点恢复、人工暂停均未实现。
- 未验证同一仓库的并发运行；工作区在运行后保留，尚无清理策略。
- 原仓库未变的判定基于 git 状态快照，覆盖未忽略的工作区与未跟踪文件变化；被忽略文件（例如缓存目录）的改动不在该快照内。
- 工作区隔离是文件层面的编辑隔离，不是操作系统沙箱；被执行的仓库代码仍以当前用户权限运行。
- 报告结构合法与状态一致都不等于改动正确；语义正确性仍需人工审核，本轮样本量为 3 条可改动任务，不构成成功率结论。
- 本机 Node.js 为验证用临时安装，未纳入项目依赖；未安装时相关用例跳过。
