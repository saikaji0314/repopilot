# 增量32：PR基线更新、冲突修复与GitHub App身份

日期：2026-10-29。用户要求本版接入自动更新PR候选、按业务上下文处理冲突，并选择GitHub App作为独立执行身份。

## 范围与当前状态

已接入相同run中的更新阶段、独立检查、双视角审核、带旧head租约的PR分支推送，以及更新后重新取得负责人原生Approve。保留原草稿、检查、审核和批准历史，不把旧批准用于新候选。App使用可选PyJWT／cryptography签名，安装令牌在API／Git使用前按到期时间续期，显式App模式不回退个人令牌。

验证使用真实临时Git／SQLite、原workflow和真实RSA，模型与GitHub业务HTTP由确定性替身控制，不计真实模型效果。结果如下，组间有重叠，不相加：

| 检查 | 结果 |
| --- | --- |
| 初轮App与PR更新／原合并专项 | 50 passed，161.30秒 |
| 相关平台、CLI、人工恢复、工作区提交、并行、双审及报告回归 | 207 passed，475.46秒 |
| 补齐更新期间main再次推进、停用与推送竞态后的最终PR更新专项 | 13 passed，101.88秒 |
| 补齐跨域凭证拒绝、畸形令牌响应和新格式令牌过滤后的App专项 | 7 passed，1.19秒 |
| 依赖兼容 | pip check通过；App为可选extra，基础安装不必加载JWT／RSA依赖 |

首轮接线曾被旧测试桩“只能接受首个审核ID”阻断，修正为实际最新负责人审核ID；随后暴露澄清调用传了旧接口不存在的phase参数，改为现有blocking上下文，更新专项5项通过（32.96秒）。失败日志保留，没有更改业务验收来迁就实现。

回归使用现有Python 3.12和`pytest -q -p no:cacheprovider --basetemp SHORT_TEMP --tb=short`，仓库内临时根及进程级TEMP／TMP／TMPDIR和GIT_CEILING_DIRECTORIES隔离。相关组包含`test_github_app`、`test_base_update`、`test_merging`、`test_github`、`test_gitlab`、`test_forge`、`test_cli`、`test_human`、`test_workflow`、`test_workspace`、`test_parallel`、`test_dual_review`和`test_report`。没有重复全量或Docker专项，最终小范围补强后仅重跑受影响专项。

真实App只读预检已经确认：独立`[bot]`身份可读取两个指定目标仓库、检查和提交状态；main当前未观察到CI检查／提交状态。私钥被Git忽略、未跟踪，模型文件工具原有`.pem`拒绝规则保留；真实令牌和私钥不写入公开材料。两仓库main的`protected=false`，保护端点404，未启用传统分支保护。因此当前不执行真实合并、不修改仓库保护，也不宣称真实Approve→冲突更新→merge已验收。一次额外只读检查遇到临时网络错误，保留原身份成功结果后仅重试一次成功，不改变权限或使用个人令牌回退。

## 运行规则

- 新功能需在单run的`merge enable`中显式设置`--auto-update`，默认旧run不增加模型调用或远端写入。
- 最新main整合到独立PR worktree；原仓库工作区和main不被更新阶段修改。仅最终获批合并改变远端目标分支。
- 原运行的模型、工具、编辑、费用、活跃时间和返工额度全部继承；默认最多3次基线更新，不自动补额度。
- 有冲突复用M4整合修复Agent及共享实现槽位。上下文包含原任务说明、人工答复、历史计划、冲突文件和双方提交。缺少业务取舍时保存冲突并用统一人工请求集中问人。
- 解决后独立重跑全部登记检查及两个审核视角；审核返工继续共用原次数。检查失败不能被模型approved覆盖。
- 新代码只推原PR功能分支：先验证旧候选为祖先，再用固定旧head的原子租约，拒绝其他作者并发更新；不强推main，不创建第二个PR。
- 更新确认后重绑定审核策略的head、base和时间，旧原生Approve失效，新候选需要负责人重新Approve。
- 意图先落盘；提交中断用原recover核对现场，推送响应丢失先核对新head，重试不重跑已完成检查和审核、不清计数。

## 已知限制与下一步

GitHub合并仍要求传统strict必需CI、有效人工审核及新提交撤销旧批准。ruleset-only、merge queue、跨仓库、部署和自动放宽保护不包含。基线在更新期间再次推进会保留上一候选和审核并继续整合，累计最多3轮；历史重写、业务取舍不明、未知合并请求仍在进行、额度不足或更新到限时停止交人，不循环修改到无限收敛。停用在模型验证期间发生时，推送前再次核对开关，拒绝新推送；已接受的副作用仍可只读核对。

本轮无新增真实模型调用、原生审核、PR推送或合并；仅App令牌认证和仓库GET预检。新增范围尚待独立审核与具有合并前提的真实验收。配置见[GitHub App](GITHUB-APP.md)，操作见[受控合并](MERGING.md)。

收尾只读观察到原[Support PR #2](https://github.com/saikaji0314/SupportPilot/pull/2)已由维护者本人手动合并，原zhixianji PR #5仍为草稿。本轮没有执行该合并，不把维护者操作计为新增自动合并验收；原M6报告保留当时草稿交付快照。
