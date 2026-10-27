# 增量27：M3 澄清、计划与子Issue

日期：2026-10-27。基线：dev/79b9b84；实现提交c7c6040，批次绑定加固794db06，独立复核修复0e0d92b，评论轮询槽位补丁84e7577（F1—F13）。对应原始规格第5、7、12节及A01／A02／A05／A06／A08。本文记录本地机制验证，不代替真实模型或真实平台写入验收。

## 本轮交付

| 能力 | 实现 |
| --- | --- |
| 任务说明 | 规划Agent只读资料和源码，生成目标、验收、假设、范围外事项、依据和未决问题；程序按内容摘要保存`task_brief`及历史版本 |
| 按需拆分 | `--plan`可选；`split=none`一个子任务，`split=sequential`多个子任务；每个子任务有输入、输出、验收、依赖、修改边界和接口约定 |
| 依赖执行 | 程序重新拓扑排序；未知依赖、重复ID、环或超限拒绝执行；运行时再核对前置结果状态及其提交是否在当前HEAD历史 |
| 顺序实现 | 每个子任务单独调用实现Agent、核对本地改动并提交；全部完成后统一anchor、独立检查、审核和人工批准 |
| 集中提问 | 每批去重、编号并注明阻塞阶段／子任务；全父任务默认最多3批；到限只生成最终缺口；答复更新任务说明并使旧证据失效 |
| Issue评论问答 | 只读能力预检；父Issue批次评论带稳定标记；只采纳登记负责人、批次之后、非机器人／非引用文本；发送／同步均按标记和检查点防重复 |
| 子Issue映射 | 仅`track=issue`子任务映射；GitHub原生子Issue可用时尝试原生关系，否则链接映射；GitLab明确标为链接；平台不支持建单时保留本地映射；重复执行和响应丢失后先查标记 |
| 状态与报告 | `plan`、`task_brief`、`plan_progress`、`question_batches`、`subtask_mappings`、`platform_preflight`进入检查点；报告展示任务说明、子任务进度、提问批次和映射 |

公开说明见[计划与提问](PLANNING.md)、[人工协作](HUMAN.md)和[平台接入](FORGES.md)。

## 验证

测试使用确定性FakeModel、真实本地Git／SQLite／CLI子进程，以及`httpx.MockTransport`平台替身；没有真实模型调用、真实评论／子Issue写入或Docker调用。

| 检查 | 结果 |
| --- | --- |
| 新增计划／提问／映射回归 | 33项通过（包含A01单任务、A02答复更新、A05三批到限、A06环／缺依赖／缺前置产物、A08凭据去重与本地映射、Issue评论问答、自动投递/轮询、默认规划、自动重规划和知识导出） |
| 新增平台协议回归 | 6项通过（GitHub／GitLab身份、评论、子Issue能力预检与写接口；全部为模拟HTTP） |
| 复审前完整回归 | 637通过／16跳过，424.06秒；复审修复新增/修改用例后不再是最终结果 |
| 完整回归（复审后最终代码） | **649通过／16跳过，478.77秒** |
| 编译与空白检查 | `compileall`通过；`git diff --check`无输出 |

最终完整命令：

```powershell
$t = (Resolve-Path .local).Path + '\tmp\m3-system-temp'
$env:TEMP=$t; $env:TMP=$t; $env:TMPDIR=$t; $env:GIT_CEILING_DIRECTORIES=$t
python -m pytest tests -q -p no:cacheprovider --basetemp .local/tmp/m3-review-final2 --tb=short
```

过程说明：第一次完整运行在本地同时启动另一个pytest时长时间无输出，已终止且不计为任何结果；随后先分别跑运行／非运行目录，再单独跑完整套件。所有失败与修正过程不删减：新增用例首轮出现7项失败，原因是测试对“计划必须拆分才能创建子Issue”的期望写错、`sync`重复答复返回值缺少版本字段和映射说明沿用了内部占位；修正实现或测试后重跑通过。另将审核阶段工具改为真正的只读集合，并让批次评论按发送时保存的评论ID定位、避免删除后复制标记绕过边界。

## 边界

- 本轮没有真实模型规划效果对照；FakeModel只能证明程序状态、计数、拒绝和恢复路径。
- 没有真实GitHub／GitLab评论、子Issue、原生层级或建单；GitHub原生子Issue能力以实际`human preflight`返回为准，GitLab一律按链接映射准备。
- 没有并行实现、两个实现槽位、子任务独立worktree、内部整合或按子任务双审；这些属于M4／M5。
- 评论投递是操作者显式`human post`／`sync`，后台监听器不自动轮询评论。没有编辑／删除评论的增量同步；已导入答复不会因后来删除而回滚。
- 修改边界是文字证据，不限制模型编辑路径；计划质量仍需人工在批准前核对。
- 未调用Docker，未重跑历史真实交付，不修改原费用台账或目标仓库；未推送。

## 复审补充（M3独立审核）

日期：2026-10-27。依据：M3独立审核的F1—F13。用户确认F4“默认开启规划，保留--no-plan”、F5“自动投递并加有节制轮询”、F6“答复后自动重新规划”。本轮只修M3边界，不扩展到M4/M5，不改M1/M2的请求、批准、恢复和知识刷新语义。复审修复提交为0e0d92b，评论轮询槽位补丁为84e7577。

| 编号 | 处理 |
| --- | --- |
| M3-F1 | GitLab `issue_comments()`显式请求`sort=asc`／`order_by=created_at`；`select_reply()`自己按`created_at`排序，并用发送时保存的`created_at`过滤早于批次的评论，不再依赖平台列表位置 |
| M3-F2 | 报告和`status`都调用`planning.status(state)`；报告显示“完成 X/Y”和计划/任务说明版本，`status`输出非空`plan_status` |
| M3-F3 | 引用批次回复先剥离HTML注释和`>`引用再采纳；不再“正文任意位置含标记就整条丢弃”，被忽略候选记录`ignored_reason` |
| M3-F4 | `plan_enabled`默认true；`--no-plan`／`REPOPILOT_PLAN_ENABLED=false`可关闭；只读无worktree运行自动跳过规划；watch默认同样规划 |
| M3-F5 | Issue任务进入`needs_input`时自动投递批次评论；监听器按`poll_seconds`、按剩余worker槽位扫描等待批次，读取负责人回复后自动恢复；`human post/sync`保留为手工重试入口 |
| M3-F6 | 计划载荷暴露`brief_revision`／`brief_sha256`／`current_brief_revision`／`stale`；答复改变任务说明时自动回到规划阶段重算，旧计划、旧进度和版本关系写入`plan_history`／`plan_refresh_history`，报告显示旧计划提示 |
| M3-F7 | 文档改为“每次到限生成最终缺口，补齐后再有缺口可再生成；自动批次不增加”，并补“最终答复后再次缺信息”回归 |
| M3-F8 | 子任务`blocked`仍保留原有`completed`语义，但`next_action`明确“计划子任务阻塞、未交付”；允许对`stop_reason=subtask_*`的运行显式`retry`，保留原运行和累计计数 |
| M3-F9 | `sync`在无pending但有已答复clarification时返回`already_answered`（含原答复渠道），不再误报`no_pending_clarification` |
| M3-F10 | 子Issue标题和正文在创建前走`checked_body`凭证形态检查；失败不创建并保存失败映射原因 |
| M3-F11 | 报告新增“子任务实现报告”，逐个子任务展示状态、摘要、证据数、提交，并说明“实现报告”只属于最后一个子任务 |
| M3-F12 | `plan show`返回按版本排序的`task_brief_versions`、`task_brief_history`、`plan_refresh_history`，可回查任务说明历史 |
| M3-F13 | 修正文档：`depends_on`可空、最终缺口表述、`platform_preflight`由`status`/`plan show`回查、状态表不再把`plan`当状态 |

新增／修改回归：

- `test_descending_notes_still_take_the_reply_after_the_batch`、`test_quoted_batch_reply_is_accepted_after_removing_the_quote`：F1/F3。
- `test_status_command_and_report_show_plan_progress`、`test_multi_subtask_report_distinguishes_each_subtask`：F2/F11。
- `test_planning_is_enabled_by_default_and_cli_can_turn_it_off`、`test_issue_waiting_run_auto_posts_and_watch_scans_it`、`test_auto_comment_failure_keeps_the_run_waiting`：F4/F5。
- `test_answer_after_plan_replans_and_exposes_brief_versions`、`test_question_limit_is_shared_across_plan_implement_and_review`：F6。
- `test_after_batch_limit_each_new_gap_keeps_its_own_final_record`、`test_blocked_subtask_is_visible_and_can_be_retried`、`test_sync_after_local_answer_reports_already_answered`、`test_subtask_issue_body_with_secret_is_rejected_before_creation`：F7—F10。
- 平台协议测试补GitLab升序参数；`test_planning.py`同时覆盖纯引用、机器人、非负责人和已删除批次评论。

验证边界与上一节相同：FakeModel、真实本地Git／SQLite／CLI、`httpx.MockTransport`；自动投递/轮询只有本地队列和模拟平台协议，没有真实GitHub／GitLab评论往返、真实模型规划效果或Docker。复审后最终完整回归为**649通过／16跳过，478.77秒**；M3专项39项通过，完整套件已包含既有回归。实现自测不代替独立复核。
