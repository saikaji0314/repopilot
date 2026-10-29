# GitHub 负责人批准后的受控合并

2026-10-29补充：可显式启用PR主分支更新与冲突修复，并使用[GitHub App安装身份](GITHUB-APP.md)。相关条目以下方“PR更新与冲突”小节为准；结果见[增量32](VALIDATION-32.md)。

2026-10-29范围补充：保留现有草稿交付，在单次运行显式启用后，检测登记负责人在GitHub PR页面提交的Approve，核对当前版本、CI和分支保护，再执行合并并确认远端结果。合并协调由程序完成，不调用模型。部署、软件发布、多人风险分级、跨仓库联动和GitLab自动合并仍未纳入。

## 使用前提

- 仅处理本运行已经创建并记录的GitHub PR。默认关闭，不扫描或合并任意PR，旧运行不自动获得合并权限。
- PR作者与批准负责人必须是不同身份。推荐由独立机器人账号或GitHub App创建PR，负责人用个人账号审核。当前以负责人账号创建的历史草稿不能由同一人自批；本功能不会伪造或代提交Approve。
- 发布的候选必须仍有匹配的本地固定SHA批准、独立检查和双审，知识资料有效，worktree干净；原Issue和目标基线未变化。
- 目标分支使用可读取的传统branch protection，开启strict必需状态检查且至少登记一项CI；至少要求1个有效人工审核，并开启新提交撤销旧批准。初版不支持只有ruleset的配置、merge queue、stacked PR、squash或rebase合并。无法读取规则即停止；不代改仓库配置。
- GitHub凭证具有读取PR、审核、CI和分支保护及合并所需权限。凭证保留在宿主，不进入模型或执行容器。使用GitHub App时，操作者登记App ID、Installation ID及本机私钥路径，宿主自动签发／续期安装令牌；不需要OAuth或Webhook服务。

严格的必需检查用于约束核对后主分支继续变化的竞态。客户端两次查询不能锁住远端；最终由GitHub分支保护及`bypass_rules=false`限制实际合并，不使用管理员绕过。

## 操作

先按原流程完成检查、双审、固定SHA批准及`publish`。在负责人审核之前，为该run登记合并策略：

```powershell
repopilot merge --output RUNTIME enable RUN_ID --owner OWNER_LOGIN --check CI_NAME
repopilot merge --output RUNTIME watch RUN_ID
```

`--check`可重复，为本地额外必需检查；目标分支保护中的必需检查始终参加核对。`watch`是前台轮询，默认30秒，异常退避，输出仅在状态变化时出现；需保持该进程运行。它不使用模型API。原Issue监听器不会自动为历史PR启用合并。

负责人在GitHub将草稿标为Ready for review，核对代码和CI后提交Approve。程序只认可登记负责人本人、启用策略之后、绑定当前head SHA的有效审核；聊天答复、Issue评论、本地草稿批准、他人或机器人Approve都不能替代。审核被撤销或有未解决changes requested时停止。

```powershell
repopilot merge --output RUNTIME show RUN_ID
repopilot merge --output RUNTIME watch RUN_ID --once
repopilot merge --output RUNTIME disable RUN_ID
```

本地`show`和`disable`不读模型或平台凭证；`--once`只执行一次核对，不承诺当次已合并。退出前台watch只停止轮询；`disable`停止新提交，已被GitHub接受的请求不能据此声称取消，后续仍需核对结果。

## 检查、状态与恢复

| 情况 | 行为 |
| --- | --- |
| 草稿、无负责人Approve、旧SHA审核、被撤销、CI缺失／失败／未完成 | 保存明确原因，不发送合并请求 |
| 代码、需求／资料、远端head或目标分支不匹配 | 阻断，要求核对，不延续旧批准 |
| 原基线推进 | 默认阻断；显式auto-update时整合新基线，重新检查、双审和批准 |
| 满足条件 | 先保存意图、负责人review来源及head/base，再请求GitHub异步合并，明确expected head、merge commit方式及不绕过规则 |
| 202 accepted／pending | 仅记为merging，保存UUID并查询结果，不记作merged |
| 请求响应丢失 | 先读取PR；同参数重试由GitHub返回现有UUID时核对选项，不创建另一套合并；最多3次提交尝试 |
| 已有UUID | 优先查询既有请求，不重新提交；failed／过期交人核对 |
| GitHub声称merged | 再读PR，核对head和merge SHA；比较提交历史，确认候选及原基线进入merge commit，merge commit进入目标分支后才保存merged |
| 重复执行 | 已确认merged直接返回原结果，不重复合并；发布重试仍返回原草稿身份 |

策略、人工原生审核、请求UUID、等待原因、合并SHA与确认时间保存在原jobs库，`status`、人工请求和离线报告可回查。轮询不清零模型或费用记录，不改原目标仓库本地工作区。

未启用自动更新时拒绝基线变化。`enable`不能覆盖已登记策略，也不能通过改负责人或清空记录规避旧批准。故障处理应保留原记录，不直接手工修改数据库重试。

## PR更新与冲突

```powershell
repopilot merge --output RUNTIME enable RUN_ID --owner OWNER_LOGIN --check CI_NAME --auto-update --max-updates 3
repopilot merge --output RUNTIME watch RUN_ID
```

启用`--auto-update`后，watch发现主分支推进时，把新基线整合进本运行独立PR worktree。普通Git冲突由受控修复Agent结合原任务、资料和人工答复处理；业务取舍不明则保存现场并提出统一人工请求，等待答复。更新及必要返工共用原运行的额度和实现槽位，默认最多3轮基线更新，没有单独重置的模型预算。

每个新候选必须重新通过全部登记检查和双审，之后自动更新原PR分支并等待你批准新SHA。更新前校验旧head为祖先，用固定旧head的租约保护并发写入；不覆盖他人新提交，也不直接推main。即使更新前你已Approve，更新后仍需对新版本重新Approve。

`merge --output RUNTIME refresh RUN_ID`可显式续接已登记的更新。业务答复用原`human answer`；中断现场先用原`recover`核对提交，再refresh继续。已被服务端接受但响应丢失的推送先核对新head，不重跑完成的审核。若更新期间main再次变化，保留旧候选和审核后继续整合最新基线，最多登记3轮；到限、远端历史重写、检查／审核不通过或额度不足时停止并保留现场交人，不自动放宽条件。

原已完成计划和审批存入更新历史；本轮更新不会把原实施计划从头再跑。报告新增每轮旧候选、新基线、新候选和冲突摘要。`merge show`保留完整历史及等待原因。

## 来源与实际验证边界

- [GitHub审核规则](https://docs.github.com/en/enterprise-cloud%40latest/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews)：作者不能自批。
- [GitHub异步合并API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request-asynchronously)：请求、UUID、查询结果与合并确认分开。
- [分支保护API](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection)：读取strict必需检查。

本轮先验证真实本地Git／SQLite和模拟GitHub HTTP，不以模拟通过冒充远端合并。历史Support PR #2只读预检确认作者和负责人同为saikaji0314，不能进行本人Approve的真实闭环；没有替用户发送审核或合并。结果和命令见[增量31](VALIDATION-31.md)。
