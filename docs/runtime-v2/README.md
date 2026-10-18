# 通用仓库运行时：范围与协议

整体能力与原始愿景的对应、概念边界、模块阅读顺序和新旧实现关系见[架构与现状](ARCHITECTURE.md)。

目标：人工给出 Issue 或本地需求，Agent 在独立工作区实现并自检，编排层独立验证，审核 Agent 针对固定提交审核、有限返工，最后暂停等人审批，再发布草稿 PR。自动监听已接入持久化队列与有界进程池，见[监听协议](WATCH.md)；自动合并和软件发布不在首版内。

## 任务与工具

任务契约 `schema_version: '2.0'` 包含 `id`、`request`、`repository`、`checks` 和可选的 `execution`。检查由可信操作者登记为参数数组，模型只能按名称选择；`{python}` 在默认本机后端为当前解释器，在 Docker 后端为镜像的 `python3`。核心工具支持通用 UTF-8 文本，不按语言白名单过滤：`list_files`、`read_file`、`search_text`、`run_registered_check`，实现阶段另有 `write_file`、`replace_in_file`。同一编辑版本内同名检查最多执行一次；发生编辑或新一轮返工后可以再次执行。独立验证额外执行全部登记检查。

编辑只在 Git worktree 开启；精确替换拒绝不存在或多处匹配。单文件写入上限 100,000 字符，读取上限 256 KB，每次最多 200 行／8,000 字符。搜索与文件列表支持 `path / file_glob`，先选目录和文件名再读取；搜索最多读取 2,000 个候选文件、返回 20 处匹配，两种工具的文件名筛选上限为 20,000。覆盖数量、跳过文件与停止原因见[搜索协议](SEARCH.md)。路径拒绝绝对路径、上溯、盘符、敏感目录与链接。检查命令不经 shell。默认本机后端有当前用户权限；可选 [Docker 后端](SANDBOX.md)统一承载文件工具及各阶段检查，仅接入可信仓库。

`ImplementationReport` 的结论是 `implemented / blocked / needs_clarification`；implemented 必须包含检查证据且工作区有变更，无变更只会降为 blocked，不把 blocked 强制升级。文件证据和检查证据分开记录。`ReviewReport` 是 `approved / changes_requested / unable_to_review`；要求返工必须有具体文件、行号与建议，approved 不得包含 blocker／major finding。

## 状态与恢复

业务阶段：`prepare → implement → anchor → verify → review → approval`。要求返工时进入 `rework → anchor → verify → review`。内层模型工具循环由 LangChain create_agent／LangGraph 提供；外层业务状态机负责固定提交、独立验证与交付条件。

| 状态 | 含义与后续 |
| --- | --- |
| `paused` | 完整阶段后停止，`resume` 从下一阶段继续 |
| `awaiting_approval` | 审核及独立检查通过，等待人工核对具体提交 |
| `approved` / `rejected` | 已记录人工决定；approved 可显式 publish |
| `recovery_required` | 检查点有未完成阶段，必须先核对现场并 recover |
| `publication_failed` / `publishing` | publish 可核对 GitHub 副作用后重试 |
| `published` | 已创建或核对到同一草稿 PR，不代表合并 |
| `completed` | 只读、未实现或关闭审核的报告完成，需另看 report.status |
| `limit_reached` / `review_inconclusive` / `rework_limit_reached` | 超限或结论不足，交人工处理 |
| `workspace_error` / `phase_error` / `failed` / `interrupted` | 工作区、结构化协议、执行异常或中断，保留现场和原因 |

SQLite 每次动作开始前写入 in_flight，每个完整阶段后写入下一阶段。模型与工具事件继续记录累计调用、Token、费用、编辑及返工次数；恢复还与预算账本中的实际预留调用数核对，不能通过重启获得新额度。安全边界处保存工作树内容摘要，恢复前拒绝未经核对的外部改动。

`--stop-after prepare|implement|anchor|verify|review|rework` 适合演示跨进程续跑。阶段内部中断不声称 exactly-once：模型请求可能已计费、检查可能已有副作用、编辑可能只完成一部分。`recover --commit SHA --reason ...` 表示操作者接纳当前现场；之后重新执行该阶段，模型收到核对说明，之前完整模型对话不会重建。未知模型费用继续占预算。已开始的返工不会因恢复重复增加轮数。

同一运行用操作系统文件锁串行化；SQLite 乐观版本检查拒绝过期写入。两个不同运行的工作区互相独立，但首版不承诺同一仓库多任务并发交付协调，也不做自动清理。需要工作区、Git 对象和本地 SQLite 一同保留，不支持跨机器恢复。

## 审核、人工审批与发布

提交身份用 `git -c` 单次命令设置，不修改 linked worktree 共享 Git 配置。原仓库未变化依据 HEAD、跟踪文件实际 diff、未跟踪文件内容摘要判断；这不包括被忽略文件或外部系统副作用。

anchor 后检查与审核前后都要求工作树干净且 HEAD 等于被审核提交。独立验证任何检查非零、超时或错误，或者没有登记检查，代码层拒绝模型批准。差异默认只传 4,000 字符，明确标注截断；审核方可读取具体文件。模型审核仍可能遗漏需求或回归，不能替代人工和隐藏测试。

人工批准绑定完整提交 SHA 和任务、Issue、基线、审核、验证结果的 SHA-256 摘要。批准不自动推送，publish 再校验版本与干净工作树。`--no-review`／`--no-workspace` 运行无法获得发布所需审批。

GitHub 仅接收标准 `https://github.com/OWNER/REPO/issues/N`；Issue 必须开放且不是 PR，目标必须与本地 origin 一致。仓库路径和检查来自本地模板。Issue 原文冻结入状态；相同 Issue 重复接入返回既有运行，不重复请求模型。内容变化拒绝静默覆盖。失败尝试可显式 `retry RUN_ID --reason ...`，新建独立运行并记录 retry_of 和原因，原失败与费用完整保留；普通重复接入指向最新尝试。同一失败运行的重复 retry 会被事务拒绝，避免并发分叉。

发布前重新读取 Issue 和默认分支，拒绝需求或远端基线变化。只推送审批 SHA 到 `feature-repopilot-<run_id>`，不强推；已存在不同 SHA 的分支会拒绝。创建 draft PR，带运行标记和独立检查记录。重试先读取远端分支与同名 PR；POST 响应丢失时通过运行标记和 head SHA 核对，避免重复创建。不自动 merge／release。

`GITHUB_TOKEN`／`GH_TOKEN` 从进程环境或 .env 读取，不存 SQLite、不交给模型、不传给检查。Git 推送通过一次性进程环境中的 HTTP header 鉴权，不写 config 或带 token 的 URL。HTTP 固定 api.github.com，不跟随重定向。错误只暴露状态码或类型。个人令牌不应配置到任务 YAML。

## 限制与费用

默认模型请求 36、工具调用 40、编辑 12、返工 3（最大 5）、输出 2048 tokens、上下文 60,000 bytes。活跃执行期限 300 秒，暂停等待不计时；HTTP 超时不超过 30 秒，检查按剩余时间收紧。无法保证毫秒级硬截止或清理刻意脱离的后台进程。采用 LangChain ContextEditingMiddleware／ClearToolUsesEdit：按字节计数，在上限一半处开始清理较早的读取输出，随上下文大小保留最近 5–12 条（极小上限至少 2 条），保留工具配对、编辑和检查结果，本地事件仍保留完整输出。它不生成语义摘要，可能丢失此前源码细节；最终超过硬上限仍停止。启动或 retry 可显式调整 --max-context-bytes（最大 500,000）与 --max-tool-calls（最大 200），resume 不放大原限额。

预算在每次模型请求前由 SQLite 原子预留。每次运行估算上限 2 元；累计账本默认 200 元，不按日期清零、不自动放大。缺失用量或中断保留预留。估算输入按字节加余量，DeepSeek 默认估算单价输入 3／输出 12 元每百万 tokens，其他供应商须显式配置；不折扣缓存，不是供应商实付或账户级限额。

本地原始证据见 `runs/<id>/`；状态与审批在 `jobs.sqlite3`，费用在 `budget.sqlite3`。状态库是本机可信数据，不具备防恶意管理员篡改的签名。JSON 产物原子替换，但非跨文件事务，恢复以 SQLite 为准。本机后端输出先写临时文件，未设置磁盘配额；Docker 后端有流式输出限额，但工作卷仍无磁盘配额。密钥替换不是通用脱敏器，不提交原始日志。

## 验证与来源

历史增量：[调查](VALIDATION-01.md)、[工作区](VALIDATION-02.md)、[审核返工](VALIDATION-03.md)。持久化与审批的历史实现见[增量 4](VALIDATION-04.md)，真实交付与常驻监听见[增量 5](VALIDATION-05.md)，展示步骤见[演示](DEMO.md)，区分确定性模拟、真实本地 Git／检查、真实模型和真实 GitHub 写入，不能互相代替。

[SWE-bench 接入协议](../../evals/swebench/README.md)提供单题输入边界与官方补丁导出；[增量13](VALIDATION-13.md)记录实际评分环境预检，尚非模型修复效果评测。

[增量15](VALIDATION-15.md)提供冻结5题的真实开发基线，以及同一工具响应重放的产物保存对照；区分官方验收、审核完成、空补丁与未修复，不外推完整Lite或生产能力。

- [LangChain Agent](https://docs.langchain.com/oss/python/langchain/agents)
- [LangGraph 暂停与副作用](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [DeepSeek 工具调用](https://api-docs.deepseek.com/guides/tool_calls/)
- [GitHub Issues REST](https://docs.github.com/en/rest/issues/issues#get-an-issue)
- [GitHub Pull requests REST](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request)


## 交付 profile 与模型接线

供应商选择、参数边界和恢复凭证规则见[模型配置](MODELS.md)，离线适配证据见[增量16](VALIDATION-16.md)。5 题开发基线暴露的工具、上下文、阶段预算及记忆证据边界见[Harness 诊断](HARNESS-FINDINGS.md)。

`run / issue / retry --profile PATH` 接收经过校验的执行参数 YAML，直接命令行参数优先。只允许调整单次执行限制与思考选项，不接受 API key、端点、模型名或累计预算。`examples/runtime/deepseek-delivery.yaml` 是真实功能演示使用的参数；凭证与模型配置仍来自 .env，resume 不改变已冻结的参数。

新 load_settings 默认启用 DeepSeek low 思考；程序直接构造 RuntimeSettings 及旧运行缺省保持 disabled，避免静默改变既有证据。当前 langchain-deepseek 1.1.1 的发送端未回传 reasoning_content，且 LangChain ToolStrategy 绑定 required 工具选择，与思考模式不兼容。DeepSeekModel 只补这两点：保留提供商要求的字段，将不支持的强制选择转 auto；工具循环仍由框架执行。原始思考只在内存里供下一次请求使用，Trace 不保存或展示。协议依据：[DeepSeek 思考与工具调用](https://api-docs.deepseek.com/guides/thinking_mode/)。

宿主逐轮反馈已读位置、已编辑文件、真实检查和剩余额度；检查输出截断时同时保留首部错误与尾部结果。进度也写入阶段检查点。这些辅助模型收尾，但非思考模式仍出现过度调查，不能仅凭机制存在宣称效果已解决。

GitHub CLI 的新任务／retry 和监听 worker 都先获取远端默认分支对象，固定新尝试基线，不重置用户原工作树。已有运行的 resume 固定旧基线，远端变化仍要求重新验证与批准。
