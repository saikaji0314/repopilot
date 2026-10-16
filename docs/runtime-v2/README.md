# 通用仓库运行时：需求与增量验收

## 目标与范围

RepoPilot 向由工单驱动的通用 coding agent 演进：读取需求、准备独立工作区，实现、测试、审核并有限返工，交付可供人审的 PR。核心不绑定 Python、AST 或 pytest；仓库自行声明环境及验证命令。合并与发布由人和既有 CI/CD 控制。

首版只在本机处理预先接入的可信仓库。路径检查、命令注册和独立工作目录用于限制误操作，不是操作系统沙箱；仓库代码仍以当前用户权限执行。

Flaskr 是首个受控场景，Flask 用于较大仓库扩展。真实 GitHub 工单选择依赖较少、可离线验证的仓库。受控缺陷注入只属于评测准备层，不是产品处理工单时的一步。

## 增量 1：调查入口与真实模型接线

输入是本地 YAML 工单，包含需求、仓库路径和命名验证命令。使用 LangChain `create_agent`（基于 LangGraph），接入 DeepSeek，允许模型列文件、搜索、读取与运行已登记的检查，输出带文件证据的结构化调查和后续计划。

本增量不提供编辑工具，不创建 GitHub Issue／PR，不实现审核返工、worktree 生命周期或断点恢复。这些属于后续增量。执行检查可能生成测试缓存等产物，因此“无编辑工具”不等于操作系统只读环境。

验收：

| 编号 | 行为 | 验证证据 |
| --- | --- | --- |
| R1 | 加载 DS 配置，缺少密钥时明确失败，不把密钥放入日志或报告 | 配置及脱敏测试 |
| R2 | 文件工具读取普通 UTF-8 源码，不依赖语言扩展名白名单 | Python 与 JavaScript 样例测试 |
| R3 | 阻止路径逃逸、链接越界和敏感目录读取 | 路径边界测试 |
| R4 | 模型只能选择已登记检查，不能添加 shell 或命令参数；返回真实退出码与截断信息 | 命令执行与超时测试 |
| R5 | 限制模型轮次、工具次数、输出和费用；失败调用保留费用不确定性 | 预算及运行失败测试 |
| R6 | 完成至少一条真实 DeepSeek 工具调用链，输出合法报告 | 本地运行摘要；不以 Fake 替代 |
| R7 | 保留旧诊断入口和题集版本 | 旧测试回归 |

本增量已完成上述入口与真实接线验证，结果及未覆盖项见[增量 1 验证记录](VALIDATION-01.md)。结构合法、检查通过和报告语义正确是三项不同结果。

## 增量 2：独立工作区与有限编辑

目标是在只读调查之上，让 agent 在原仓库之外改动文件、运行目标检查，并交出可核对的差异与证据。范围限单仓库、单工作区、有限次数的文本编辑；不含合并、PR、审核返工与断点恢复。

任务是版本 2.0 的本地 YAML，字段与增量 1 相同：需求文本、仓库路径与命名检查。区别是仓库必须是 Git 工作树，`Workspace.prepare()` 在准备阶段要求原仓库含 `.git`，否则以 `workspace_error` 停止，不再退回直接改原仓库。每次运行前执行 `git worktree add --detach <dir> <base_commit>`，基线提交取原仓库当时 HEAD，工作区建在原仓库之外。读取与写入工具共用同一套路径判断：拒绝绝对路径、`..`、盘符与敏感目录，写入还必须落在工作区内。

验收：

| 编号 | 行为 | 验证证据 |
| --- | --- | --- |
| I1 | 仓库必须是 Git 工作树；工作区建在原仓库之外，原仓库内容保持不变 | 真实运行摘要中 `repository_unchanged: true`；非 Git 仓库以 `workspace_error` 停止 |
| I2 | 编辑工具受路径、单文件大小与次数限制；无工作区时不提供编辑工具 | 路径边界与编辑预算测试；`--no-workspace` 只暴露读取与检查工具 |
| I3 | 变更以相对基线的差异和状态呈现，截断显式标注 | `changes.json` 与运行摘要 `changes` 字段 |
| I4 | 报告的证据区分文件证据（相对路径 + 起始行号）与检查证据（检查名 + 真实退出码） | 结构契约校验测试 |
| I5 | 无文件变更不得自称 `implemented`；有变更即为 `implemented`；`implemented` 至少含一条检查证据 | 状态校正与契约校验测试 |
| I6 | 无工作区的只读入口保留原行为，不因本增量回归 | `--no-workspace` 路径测试；旧只读样例配该开关运行 |
| I7 | 真实模型能完成一次最小实现并跑通目标检查 | 本地运行摘要（Python 与 JavaScript 各一条）；不以 Fake 替代 |

离线回归为 257 passed、5 skipped：3 个跳过是符号链接用例，2 个是缺独立环境或系统条件的旧用例。真实 DeepSeek（`deepseek-flash`，关闭思考模式）本轮共 5 次运行：Python／Flaskr 教程仓库上“补一条首页匿名可浏览的测试”完成 1 次编辑、检查 13 passed、报告 `implemented`；JavaScript／Node 样例上“把内部空白折叠为单个空格并补断言”完成 2 次编辑、`node --test` 通过、报告 `implemented`；Flaskr 只读调查 2 次为 0 次编辑、报告 `planned`；另有 Node 任务复核 1 次。每次运行都记录 `repository_unchanged: true`。

本增量未验证：合并与 PR、审核返工、断点恢复、同一仓库并发运行、非 Git 仓库（明确不支持 worktree），以及真实业务仓库上的效果。上面是受控样例上的结果，不等于真实仓库的修复率。逐条运行记录、费用与限制见[增量 2 验证记录](VALIDATION-02.md)。

实现类样例指向本机克隆的 Git 仓库，不随本项目分发：`examples/runtime/flaskr-implement.yaml` 需要 `.local/target-repos/flaskr-tutorial-git`（Pallets Flask 教程，见该目录下的 README），`examples/runtime/javascript-implement.yaml` 使用 `examples/runtime/fixtures/javascript`。克隆与初始化命令见 `.local/target-repos/README.md`。

## 增量 3：审核与有限返工

目标是在增量 2 的独立工作区之上加入第二个角色：实现完成后，由只读审核 Agent 针对固定提交核对改动，需要改动时交回实现方返工，返工次数有限。范围仍是单仓库、单工作区；不含合并与 PR、断点恢复、人工暂停与并发运行。

一次运行现在是两阶段循环：**实现阶段**（可读写工作区）→ 把工作区提交为固定版本 → **审核阶段**（只读）→ 审核要求改动则回到实现阶段返工。返回实现的次数由 `max_rework` 限制，默认 3，取 0 表示不返工。审核方拿到被审提交号、基线提交、变更文件、独立复核结果与差异；返工轮里实现方额外拿到上一轮的 findings 与 `reviewed_commit`。

审核锚点是 git 提交，而不是工作区的当前状态：每次进入审核前由编排层把工作区提交为固定版本（提交身份 `RepoPilot <repopilot@localhost>`，写在工作区自身的 git 配置里，不修改用户全局配置），审核 payload 带该提交号与基线提交，`review-history.json` 逐轮记录锚点提交与发现。审核 Agent 只读：装配的工具集里没有 `write_file` / `replace_in_file`，这条有测试断言，不是只写在提示词里。执行检查可能生成测试缓存等产物，因此“无编辑工具”不等于操作系统只读环境。

进入审核前由**编排层**（不是模型）重跑全部登记检查，把真实退出码与输出尾部写入 `verification.json` 交给审核方；实现方自称的检查结果与复核冲突时以复核为准。审核提示词另有一条硬规则：复核结果中任何一项不是退出码 0，就不允许 `approved`，必须给 `changes_requested` 或 `unable_to_review`。这条规则是真实运行发现问题后补上的（见[增量 3 验证记录](VALIDATION-03.md)），不是预先设计。

验收：

| 编号 | 行为 | 验证证据 |
| --- | --- | --- |
| R1 | 审核绑定具体提交：审核前提交工作区，payload 与历史都带被审提交与基线提交 | 审核锚点提交测试；`review-history.json` 逐轮记录 `commit` 与 verdict |
| R2 | 审核 Agent 只读：工具集不含 `write_file` / `replace_in_file` | 审核工具集测试断言；审核阶段使用只读工具集 |
| R3 | 进入审核前由编排层独立重跑全部登记检查，真实退出码与输出尾部落盘并交给审核方 | 独立复核测试；`verification.json` |
| R4 | 复核结果中任一检查不是退出码 0 时不允许 `approved`，只能 `changes_requested` 或 `unable_to_review` | 审核提示词硬规则；真实运行中同一场景由 `approved` 改为 `unable_to_review` |
| R5 | 返工受 `max_rework` 限制，超过上限停止并交人工 | 返工达上限后停止的确定性用例；`rework_limit_reached`（退出码 1） |
| R6 | 审核无法判断时记为 `review_inconclusive` 并交人工，不当作通过 | `unable_to_review` 用例；真实运行中已到达 |

终止与退出码：`approved` → `completed`（退出码 0）；返工超过 `max_rework` → `rework_limit_reached`（退出码 1）；审核无法判断 → `review_inconclusive`（退出码 1）。实现方声明 `blocked` / `needs_clarification`，或没有任何文件变更时跳过审核，不产生审核结论。

审核契约：`ReviewReport.verdict` 为 `approved` / `changes_requested` / `unable_to_review`；每条 finding 必须给出工作区相对路径、起始行号、严重程度（`blocker` / `major` / `minor`）、观察与建议；`changes_requested` 至少一条 finding，`approved` 不允许带 `blocker`。实现方的报告类型相应改为 `ImplementationReport`（`implemented` / `needs_clarification` / `blocked`）。

新增产物：`review.json`（最近一次审核）、`review-history.json`（逐轮锚点与发现）、`verification.json`（独立复核结果）；`summary.json` 增加 `review` 块，`review_rounds` 是审核轮数、`rework_attempts` 是实际返工次数（一次返工对应两次审核），另有 `reviewed_commit`、`verdict`、`findings`、`limit`、`final_commit`。

已验证：离线回归 `python -m pytest -q -rs` 为 269 passed、5 skipped，跳过项与上一轮相同。真实 DeepSeek（`deepseek-flash`）本轮 10 次启用审核的运行全部记录 `repository_unchanged: true`，其中 8 次 `approved`、1 次 `rework_limit_reached`、1 次 `review_inconclusive`；抽查的审核结论都引用了具体文件与行号并自行核对差异，还出现过一次指出实现方声称的检查结果与复核结果冲突。

未验证：**真实模型下从未触发过实际返工**——实现 Agent 每次第一次就把工单做对，审核方没有理由要求返工；刻意构造的三个“未完成实现”基线也没能让审核方给出 `changes_requested`。返工的编排逻辑由确定性假模型用例覆盖（一次返工后通过、返工无新提交时锚点不变、返工达上限停止），但返工的真实效果尚未验证。另一条限制是审核结论在同一输入上不稳定（同一场景三次运行分别得到 `approved` / `changes_requested` / `unable_to_review`），因此两种终止路径必须交人工，不能当作自动通过。仍未验证：合并与 PR、断点恢复、人工暂停、同一仓库并发运行、非 Git 仓库。逐条运行记录与限制见[增量 3 验证记录](VALIDATION-03.md)。

## 调用限制与费用口径

默认每次运行最多 20 次模型请求、40 次普通工具调用，单次输出 2048 tokens；请求上下文以序列化字节数限制在 60,000 bytes。单次检查最多返回 8,000 bytes，文件读取最多 200 行／8,000 字符，单文件上限 256 KB。检索最多扫描 2,000 个文件并返回 20 处匹配，截断和跳过项显式记录。

编辑口径独立于工具次数：每次运行默认最多 12 次编辑、上限 50，`write_file` 单文件上限 100,000 字符，`replace_in_file` 的新文本同样受该上限约束。变更差异按 4,000 字符截断并标注 `truncated`；新增文件的差异正文只内联前 400 行。这些与工具次数、模型轮次一样属于本地硬限制，触及即停并保留已有结果。`--no-workspace` 表示不建工作区、读取类工具直接指向原仓库、不提供编辑工具，适用于只想只读检查的场景；该模式下不采集变更，摘要中 `changes` 与 `repository_unchanged` 为空。`--no-review` 表示只做实现不做审核，摘要 `review.enabled` 为 false；`--max-rework N` 覆盖本次运行的返工上限（默认 3，0 表示不返工）。审核阶段会在实现之外额外重跑全部登记检查，并额外消耗模型请求与工具调用；每轮返工同样各跑一次实现与审核。

默认运行期限 300 秒，HTTP 请求超时不超过 30 秒；行动前和模型响应后检查期限，检查执行超时随剩余期限收紧。同步 HTTP 调用和进程清理不能保证毫秒级硬截止。SDK 自动重试与结构化输出自动修复关闭；触及限制直接保留结果并停止。

每次请求前在 SQLite 账本中原子预留费用，避免两个运行同时消费同一剩余额度。默认单次运行估算上限 2 元，累计上限由 `REPOPILOT_BUDGET_CNY` 配置，默认 200 元。按输入 3 元／百万 tokens、输出 12 元／百万 tokens 保守估算，不折扣缓存命中；这些是本地估算参数，不是供应商实付单价。输入预留按字节量加协议余量估算，响应后按返回用量结算；用量缺失、请求失败或崩溃时保留预留，不记为免费。

同一账本不自动按日期清零，也不能通过再次启动并传入更高额度扩大原窗口。新预算窗口需显式配置独立输出目录，并由操作者管理累计支出；本地估算不是账户级供应商扣费硬限制。账本不能覆盖旧运行器或其他应用的模型调用。实际扣费以供应商账单为准。

默认跳过 `.git`、`.env`、`.local`、凭证、依赖目录以及 `data`／`output` 等本地数据目录。链接检查不防止其他进程并发替换文件。命令注册由操作者维护，不能把模型建议的任意命令直接升级为已授权检查。子进程不继承模型密钥等环境变量，但仍能以当前用户权限访问宿主文件和网络，不能据此处理不可信仓库。输出先写临时文件再截断读取，尚未设置磁盘配额。超时尝试终止普通进程树，不保证清理刻意脱离的后台进程。

模型运行的第三方远程 tracing 默认关闭。输入、工具事件和报告仅保存在本机；日志会替换当前模型密钥，不是通用敏感信息识别系统。

## 框架与模块边界

- `repopilot/runtime/`：新的任务配置、仓库访问、检查执行、预算、模型接线与 CLI，以及实现／审核两阶段循环的编排。编排层负责提交审核锚点、独立重跑登记检查、执行返工上限与终止判定，这些判断不交给模型。
- `repopilot/runtime/workspace.py`：工作区生命周期与差异事实。负责在原仓库之外用 `git worktree add --detach` 准备工作区、采集相对基线的变更（路径、状态码、增删行数、是否二进制）、生成按 4,000 字符截断的统一差异、限制编辑次数（`EditBudget`），并用 git 状态快照（`source_snapshot`：HEAD 与工作区条目）证明原仓库未被改动。审核前它把当前改动提交为固定锚点；提交身份 `RepoPilot <repopilot@localhost>` 写在工作区自身的 git 配置里（仓库级，不改用户全局配置）。它不判断改动是否正确。
- 工作区默认位于 `<输出目录>/worktrees/`，本机即 `.local/runtime/worktrees/`，运行结束后保留供人工核对，不做自动删除；运行摘要记录工作区路径与基线提交。实现类产物是 `input.json`、`report.json`、`changes.json`、`events.jsonl` 与 `summary.json`；启用审核后另有 `review.json`（最近一次审核）、`review-history.json`（逐轮锚点提交与发现）、`verification.json`（编排层独立复核的真实退出码与输出尾部），`summary.json` 增加 `review` 块（`review_rounds`、`rework_attempts`、`reviewed_commit`、`verdict`、`findings`、`limit`、`final_commit`）。检查的完整输出只在 `events.jsonl` 的 `tool_finished` 事件里，摘要只保留名称、退出码、超时与截断等判定要素。
- `repopilot/agent/`、旧运行脚本：历史只读诊断入口，暂不迁移其结果协议。
- `examples/runtime/`：不含凭证的任务示例；验证命令以参数数组声明，不使用 shell 字符串。实现类样例 `flaskr-implement.yaml`、`javascript-implement.yaml` 与只读的 `flaskr-inspect-worktree.yaml` 指向 Git 工作树；旧的 `flaskr-inspect.yaml`、`javascript-inspect.yaml` 指向无 `.git` 的快照目录，需配 `--no-workspace` 运行。
- `.local/runtime/`：本机运行证据、工作区目录与累计预算账本，不随 Git 分发。

采用框架提供的工具循环，不另写一套模型消息调度。项目负责工具边界、运行限制、费用记录与完成条件。先选择可控的基础 harness；待真实任务证明需要自动压缩或委派时再评估 Deep Agents。

## 后续验收方向

1. 独立 Git 工作区、有限文件编辑、目标测试及差异报告：已在增量 2 完成并验证（范围限于受控样例），见上文「增量 2：独立工作区与有限编辑」。
2. 实现／审核角色交接、审核绑定具体代码版本、有限返工与失败交接：已在增量 3 实现（审核绑定提交、审核只读、独立复核、失败检查不可批准、返工上限与两种终止路径），但真实模型下从未触发实际返工，返工的真实效果待验证，见上文「增量 3：审核与有限返工」。
3. 关键节点持久化、人工暂停与恢复后的副作用核对。
4. 真实 Issue／PR 接入、小样本评测与跨语言验证。
5. 同一仓库并发运行与工作区清理策略：当前不做自动删除、也不处理两个运行共用同一仓库的情形。

第 3 至 5 项仍是演进方向，不代表已实现。第 2 项的编排逻辑已实现并回归覆盖，真实返工与合并交付仍未验证。旧诊断通过率与新修复率分别记录。复用题用于开发回归，新增留出题用于首次效果验证；参考补丁、隐藏验收和可泄漏答案的 Git 历史不进入解题 Agent 工作区。

## 来源

- [LangChain Agent](https://docs.langchain.com/oss/python/langchain/agents)
- [LangGraph 暂停与副作用](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [DeepSeek 工具调用](https://api-docs.deepseek.com/guides/tool_calls/)
