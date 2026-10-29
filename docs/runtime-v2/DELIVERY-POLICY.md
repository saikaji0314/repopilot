# 交付边界：草稿 PR 与仓库 CI/CD

同日后续补充：已接入可选PR基线更新与冲突处理；更新后重新检查／双审并取得新SHA批准，机器人可用App安装身份。预算和次数沿用原run，原主分支只在最终合并时改变。见[增量32](VALIDATION-32.md)。

2026-10-29范围补充：用户明确要求本版增加“负责人在GitHub页面Approve后自动合并并确认结果”。默认仍交草稿；按单次运行显式启用后，新增原生负责人审核、strict必需CI、固定版本与异步结果核对。用法和首版限制见[受控合并](MERGING.md)。下文2026-10-18决定为历史，自动合并及CI读取部分以本次补充为准；部署、软件发布、多人数分级仍不包含。实现与远端真实验收分别登记，不把开发授权当成现有PR合并批准。

2026-10-18 的范围决定：**近期以人工批准具体提交后的草稿 PR 为终点**。不增加自动合并、软件发布、生产部署，也暂不接入远端 CI/CD 状态查询。现有 `publish` 表示发布分支并创建草稿 PR，不表示发布软件。

## 同类项目如何处理

以下为当日官方文档／源码调研，不是本机运行验证，也不能由某个模块推断整个项目的默认配置。

| 项目或平台 | 已公开的机制 | 对 RepoPilot 的启发 |
| --- | --- | --- |
| [Open SWE 人工审核](https://github.com/langchain-ai/open-swe/blob/main/docs/reference/human-review.md) | 支持审核协作与自动执行合并：GitHub 上有有效 approval，审核者条件满足，CI通过、无冲突、无未解决讨论或 changes requested 等；以 App 身份合并，不使用管理员绕过 | “人决定、程序执行”可以共存，但需要完整的版本、审核和平台事件协调机制 |
| [OpenHands GitHub Action](https://docs.openhands.dev/openhands/usage/run-openhands/github-action) | Issue／评论触发修复，通过 PR 查看尝试并用评论继续反馈；另有[自动 PR review](https://github.com/OpenHands/docs/blob/main/openhands/usage/use-cases/code-review.mdx)接入 Actions | Agent 可以停在 PR 与迭代反馈；不能因为有修复和 review，就推断具备通用自动部署 |
| [GitHub Agentic Workflows](https://github.github.com/gh-aw/reference/safe-outputs-pull-requests/) | Agent 请求受控写入，由权限分离的 safe outputs 执行；merge-pull-request 仍为实验功能，要求检查、审核、讨论、标签、分支和可合并性门，且拒绝合入默认分支 | 合并是独立、受策略约束的控制面动作，不应该成为模型可以自由选择的 shell 命令 |
| [GitHub Environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) | CI/CD 工作流可要求部署审批、限制分支；环境密钥在审批后才可用。具体能力受仓库类型及套餐限制 | 构建和部署由目标仓库已有 CI/CD 管理，避免在 Agent 中重做流水线与生产凭证管理 |

这些机制有不同的权限与范围，不能简单归类为“别人都自动部署”或“别人都只靠人”。共同点是区分生成候选、平台验证、批准决定和执行副作用。

## 当前 RepoPilot 的责任

- 接收可信仓库的需求，生成候选补丁，独立运行登记检查并审核，有限返工。
- 人工核对证据并批准完整 SHA，批准本身不推送。发布前校验需求、基线、工作树及批准摘要。
- 只把批准提交推到专用分支并创建草稿 PR；远端记录与本地批准版本对应。
- 仓库维护者负责转正式 PR、平台 CI、合并与发布。RepoPilot 的本地登记检查不等于远端 CI；本地批准也不自动满足 GitHub 的 review 规则。

当前范围有完整而有限的“需求到可审核候选”价值。CI/CD 不应为了让流程图更长而加入；在真实交付中出现跨系统核对成本后，再决定是否接入。

## 未来如需扩展

候选顺序为：只读展示 CI／审核／部署证据 → 显式批准后的合并协调 → 独立发布审批与制品追溯。**这些均未实现，也不在当前开发范围。**

合并协调至少需要固定 head/base SHA、GitHub 分支保护、指定检查、有效人工 review、冲突与基线变化处理、幂等操作及失败恢复。部署则需要独立审批与固定制品摘要，委托目标仓库 CI/CD 执行；不能把“批准发 PR”继承为“批准上线”。详细候选设计保留在 [NEXT](NEXT.md)，启用前需重新确认范围。
