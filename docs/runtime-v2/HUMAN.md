# 本地人工请求、集中提问与继续执行

更新：2026-10-27。范围：单机工作区任务的需求补充、固定提交批准、中断现场恢复，以及可选的父Issue评论问答与提问批次计数。M3的任务规划、任务说明与子Issue映射见[计划](PLANNING.md)；双视角审核尚未接入。

## 什么时候等待人

实现或返工报告为`needs_clarification`时，运行保存问题、当前工作区和已有用量，以`needs_input`退出。审核报告为`unable_to_review`且包含问题时，也进入等待；没有问题的审核异常仍为`review_inconclusive`，不擅自把环境错误当业务问答。启用`--plan`时，规划角色报告`needs_clarification`同样进入等待，答复后从规划阶段继续。

集中提问按父任务计数：规划、实现、返工和审核共享同一组批次，默认最多3批；到上限后只生成最终缺口说明，不自动开始下一批。具体规则与Issue评论渠道见下文。

等待是持久化状态，不是保持一个模型调用或worker进程。反复`resume`只返回当前等待状态，不增加模型调用、活跃时间或问题记录。显式关闭worktree的只读调查继续保留原有报告结束语义，不获得开发／交付权限。

## 查看、答复，再继续

下面命令的`RUN_ID`、`REQUEST_ID`和`VERSION`取自实际输出；自定义运行目录时，每次使用相同的`--output`。

```powershell
repopilot human list RUN_ID --output .local/runtime
repopilot human answer RUN_ID REQUEST_ID --version VERSION --answer "空输入返回空结果，保留旧接口" --output .local/runtime
repopilot resume RUN_ID --output .local/runtime
```

`human list`和`human answer`不加载模型或仓库平台凭证，也不调用模型或网络。答复命令只保存答案并把任务改为`paused`；`resume`才开始后续模型工作，继续使用原运行及累计费用、调用、编辑和返工次数。收到新答案后，实现和审核都会获得带来源的业务补充；任务说明`task_brief`会追加已答复问题和新版本，原问题仍保留在历史记录里。

实现中等待的任务从原实现阶段继续，保留部分改动；返工中等待从同次返工继续，不重复计数。审核中等待的任务答复后先重跑独立检查，再审核同一候选；如果需要修改，由审核反馈进入原有返工流程。业务补充使当前检查／审核／批准不再作为有效结果，历史审核仍保存。

审核历史及`review_rounds`包含提问后无法形成结论的审核调用；修改上限按实际`rework_attempts`判断，不能把一次澄清当成已经修改过一次。

同一请求、相同版本和相同答案可以重复提交，不新增答案或再次恢复。不同答案覆盖已答请求会被拒绝；新问题应通过新的澄清过程处理。空答案、未知请求、错误版本、阶段／需求／Issue／工作区变化、存在未完成阶段时均拒绝推进。工作区被外部改过时先核对差异，恢复原等待现场后再答复；本增量不提供静默接纳变更或自动改写需求的命令。

## 集中提问、批次上限与Issue评论

一个父任务默认最多3批自动集中提问（`max_question_batches`，可在执行参数里调整到1—5）。每批问题去重、统一编号，并注明阻塞的阶段或子任务；同一上下文重复等待不新建请求、不重复计数。达到上限后只保存一份`final`缺口说明，负责人补充后可恢复，原批次计数、答复时间和恢复记录保留。

Issue任务可以把当前批次发到父Issue，再读取登记负责人的回复：

```powershell
repopilot human preflight RUN_ID --output .local/runtime
repopilot human post RUN_ID --output .local/runtime
repopilot human sync RUN_ID --output .local/runtime
```

在`.env`或进程环境登记一位负责人：`REPOPILOT_OWNER=登录名`。只有该登录名、且位于本批评论之后的非机器人、非系统评论会被采纳；纯引用、问题原文回声、其他人和机器人评论只作线索，不会推进任务。没有批次评论、没有有效回复、评论被删除或作者不符时保持等待；重复`sync`只返回已答复记录，不重复增加版本或让证据再次失效。

`human post`先保存发送意图和稳定标记，再发评论；同一标记已存在时复用，不重复发送。`preflight`和`post`都先只读探查平台能力与令牌身份；`write_verified=false`表示尚未实际写入，真正的写权限失败会明确报错，不会用空令牌重试。评论正文只包含运行、阶段、摘要和问题，不放原始会话、密钥或私密现场材料。

Issue评论答复同样不能批准或推送：交付批准仍使用绑定完整SHA的`approve`。完整命令和限制见[计划](PLANNING.md#issue评论渠道可选)。


## 请求类型不能互相代替

| 类型 | 查看方式 | 处理方式 |
| --- | --- | --- |
| `clarification` | `human list`、`status`及报告；含批次号或“最终缺口” | 本机`human answer`或Issue评论`human post`/`sync`，然后显式`resume` |
| `approval` | 审核及检查通过后的请求记录 | 原有`approve`或`reject`，绑定完整SHA |
| `recovery` | 检测到未完成阶段后创建的现场核对请求 | 原有`recover`，核对当前SHA和理由后再`resume` |
| `context_update` | 已登记项目资料失效／变化产生的请求 | [资料刷新](KNOWLEDGE.md)，确认材料并核对当前SHA后重新验证 |

```powershell
repopilot approve RUN_ID --commit FULL_SHA --reason "已核对需求、差异与证据" --output .local/runtime
repopilot recover RUN_ID --commit FULL_SHA --reason "已检查中断现场，可从当前文件继续" --output .local/runtime
```

`human answer`不能回答批准／恢复请求。业务答案不能更改工具权限、检查配置、额度或推送权限。批准也不会自动推送；创建草稿仍需原有`publish`。需求补充纳入批准摘要，批准后改写答案会使发布校验失败。

`recover`继续保留原有现场核对规则；它可由操作者明确接纳当前文件，不能替代未完成发布动作的远端核对。恢复请求的现场版本改变时，保留被替代的请求并记录本次实际确认的现场。

## 存储与兼容

请求使用版本2协议（旧版本1请求仍可读），包含ID、类型、阶段、批次／最终缺口、阻塞对象、问题、上下文版本、状态、评论投递记录和答复。上下文绑定运行身份、任务、原Issue、业务答案版本、基线、当前提交、工作区内容摘要、计划版本和批准依据。答复注明本机操作者或登记负责人、渠道、来源及时间，不保存原始模型思考。

请求与答案保存在现有`jobs.sqlite3`的任务JSON中，和阶段状态通过同一次带revision检查的更新提交，避免另建请求库造成半次更新。事务外完成Git核对，不在事务里等待模型或人工。`requirements_revision`随新的有效答案递增；明确`retry`会继承已确认答案、提问批次数、任务说明、计划和子Issue映射，但历史请求仍留在原运行。旧运行没有新字段仍可读取、恢复和审批，不会自动补发评论。

Issue评论发送前先在状态里保存标记和发送意图，写入成功后保存评论ID与链接；重试先按标记查重。读取回复只使用发送时保存的批次评论ID之后的评论，批次评论缺失时不接受复制标记的替代边界。`platform_preflight`保存最近一次只读能力探查，供报告回查。

CLI遵循现有本机操作者信任边界，不提供账号认证或远端身份校验；`actor=local-operator/channel=cli`和`actor=registered-owner/channel=issue`是来源标识，不是企业身份认证。业务答复和证据保存在本地受控运行目录，不应填写密钥。Issue评论渠道只登记一位负责人，不做企业RBAC；评论写入依赖平台令牌，`preflight`能证明身份和读取能力，但不能在不写入时证明写权限。双视角审核和修改上限的完整接入留到后续增量。

## 设计取舍与验证

请求协议在[collaboration/requests.py](../../repopilot/collaboration/requests.py)，批次与去重在[collaboration/questions.py](../../repopilot/collaboration/questions.py)，评论正文和负责人过滤在[collaboration/comments.py](../../repopilot/collaboration/comments.py)，版本／工作区核对与答复应用在[orchestration/human.py](../../repopilot/orchestration/human.py)。Agent仍用现有结构化报告提出需求缺口，程序统一进入等待；后续角色可复用同一协议。

没有额外建立一个“找人Agent”，也没有为等人迁移整套编排引擎：持久化和恢复是程序职责，原状态机已经有检查点、锁、费用和批准约束。若以后改用图引擎，仍需保留这些业务约束和副作用核对。

机制验证使用确定性模型消息、真实本地Git、SQLite、CLI子进程和`httpx.MockTransport`平台替身。测试入口为[人工交互验证](../../tests/runtime/test_human.py)、[计划与集中提问](../../tests/runtime/test_planning.py)及[平台评论协议](../../tests/runtime/test_issue_channel_providers.py)，不把这些等同于真实模型完成工单或真实平台写入。
