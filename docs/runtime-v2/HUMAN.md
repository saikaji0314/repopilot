# 本地人工请求与继续执行

更新：2026-10-27。范围：单机工作区任务的需求补充、固定提交批准和中断现场恢复。Issue评论往返、负责人平台身份、任务拆分和双视角审核尚未在本增量接入。

## 什么时候等待人

实现或返工报告为`needs_clarification`时，运行保存问题、当前工作区和已有用量，以`needs_input`退出。审核报告为`unable_to_review`且包含问题时，也进入等待；没有问题的审核异常仍为`review_inconclusive`，不擅自把环境错误当业务问答。

等待是持久化状态，不是保持一个模型调用或worker进程。反复`resume`只返回当前等待状态，不增加模型调用、活跃时间或问题记录。显式关闭worktree的只读调查继续保留原有报告结束语义，不获得开发／交付权限。

## 查看、答复，再继续

下面命令的`RUN_ID`、`REQUEST_ID`和`VERSION`取自实际输出；自定义运行目录时，每次使用相同的`--output`。

```powershell
repopilot human list RUN_ID --output .local/runtime
repopilot human answer RUN_ID REQUEST_ID --version VERSION --answer "空输入返回空结果，保留旧接口" --output .local/runtime
repopilot resume RUN_ID --output .local/runtime
```

`human list`和`human answer`不加载模型或仓库平台凭证，也不调用模型或网络。答复命令只保存答案并把任务改为`paused`；`resume`才开始后续模型工作，继续使用原运行及累计费用、调用、编辑和返工次数。收到新答案后，实现和审核都会获得带来源的业务补充。

实现中等待的任务从原实现阶段继续，保留部分改动；返工中等待从同次返工继续，不重复计数。审核中等待的任务答复后先重跑独立检查，再审核同一候选；如果需要修改，由审核反馈进入原有返工流程。业务补充使当前检查／审核／批准不再作为有效结果，历史审核仍保存。

审核历史及`review_rounds`包含提问后无法形成结论的审核调用；修改上限按实际`rework_attempts`判断，不能把一次澄清当成已经修改过一次。

同一请求、相同版本和相同答案可以重复提交，不新增答案或再次恢复。不同答案覆盖已答请求会被拒绝；新问题应通过新的澄清过程处理。空答案、未知请求、错误版本、阶段／需求／Issue／工作区变化、存在未完成阶段时均拒绝推进。工作区被外部改过时先核对差异，恢复原等待现场后再答复；本增量不提供静默接纳变更或自动改写需求的命令。

## 请求类型不能互相代替

| 类型 | 查看方式 | 处理方式 |
| --- | --- | --- |
| `clarification` | `human list`、`status`及报告 | `human answer`，然后显式`resume` |
| `approval` | 审核及检查通过后的请求记录 | 原有`approve`或`reject`，绑定完整SHA |
| `recovery` | 检测到未完成阶段后创建的现场核对请求 | 原有`recover`，核对当前SHA和理由后再`resume` |

```powershell
repopilot approve RUN_ID --commit FULL_SHA --reason "已核对需求、差异与证据" --output .local/runtime
repopilot recover RUN_ID --commit FULL_SHA --reason "已检查中断现场，可从当前文件继续" --output .local/runtime
```

`human answer`不能回答批准／恢复请求。业务答案不能更改工具权限、检查配置、额度或推送权限。批准也不会自动推送；创建草稿仍需原有`publish`。需求补充纳入批准摘要，批准后改写答案会使发布校验失败。

`recover`继续保留原有现场核对规则；它可由操作者明确接纳当前文件，不能替代未完成发布动作的远端核对。恢复请求的现场版本改变时，保留被替代的请求并记录本次实际确认的现场。

## 存储与兼容

请求使用版本1协议，包含ID、类型、阶段、问题、上下文版本、状态和答复。上下文绑定运行身份、任务、原Issue、业务答案版本、基线、当前提交、工作区内容摘要和批准依据。答复注明本机操作者、来源及时间，不保存原始模型思考。

请求与答案保存在现有`jobs.sqlite3`的任务JSON中，和阶段状态通过同一次带revision检查的更新提交，避免另建请求库造成半次更新。事务外完成Git核对，不在事务里等待模型或人工。`requirements_revision`随新的有效答案递增；明确`retry`会继承已确认答案及原运行来源，但历史请求仍留在原运行。

没有这些新字段的历史运行仍可读取和使用原有审批／恢复；只读查看不会替旧运行自动生成请求。旧运行执行批准或恢复时，在同一次更新里补入对应请求及答复。没有业务补充的旧批准摘要算法保持不变。

CLI遵循现有本机操作者信任边界，不提供账号认证或远端身份校验；`actor=local-operator/channel=cli`是来源标识，不是企业身份认证。业务答复和证据保存在本地受控运行目录，不应填写密钥。平台评论、提问次数上限、资料正文服务及依赖范围暂停在后续增量接入；本增量不宣称已完成这些能力或真实模型效果验证。

## 设计取舍与验证

请求协议在[collaboration/requests.py](../../repopilot/collaboration/requests.py)，版本／工作区核对与答复应用在[orchestration/human.py](../../repopilot/orchestration/human.py)。Agent仍用现有结构化报告提出需求缺口，程序统一进入等待；后续角色可复用同一协议。

没有额外建立一个“找人Agent”，也没有为等人迁移整套编排引擎：持久化和恢复是程序职责，原状态机已经有检查点、锁、费用和批准约束。若以后改用图引擎，仍需保留这些业务约束和副作用核对。

机制验证使用确定性模型消息、真实本地Git、SQLite和CLI子进程。测试入口为[人工交互验证](../../tests/runtime/test_human.py)及[既有恢复／审批验证](../../tests/runtime/test_workflow.py)，不把这些等同于真实模型完成工单。
