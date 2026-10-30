# 版本沿革

## 0.2.0rc1：Windows 桌面预发行（2026-10-30）

- 增加 Windows x64 `.exe` 安装程序，内置 Electron、Python 与本机服务；运行数据保存在用户目录。
- 增加执行详情、Agent 工具动作关联与实时输出、差异／检查／审核和固定候选本地批准。
- 导航及 Agent 分栏支持调宽、折叠、键盘操作与重启记忆。
- 增加 GitHub App 配置向导、Windows 加密凭证保存、只读连接检查、运行目录切换和普通 Issue 评论的身份校验／超时核对。
- 桌面模型配置可保存和校验格式，尚未接入启动 worker；完整限制及安装步骤见[发行说明](docs/desktop/RELEASE-0.2.0rc1.md)。

- 从只读诊断发展为仓库研发流程：独立工作区实现、登记检查、固定提交审核、有限返工、人工批准和草稿PR／MR。
- 增加统一人工请求、版本化项目资料和决定记录，保存来源、答复与失效关系。
- 增加任务规划、集中提问、可选子Issue，以及最多两个实现槽位的并行与内部整合。
- 常规审核改为正确性、需求上下文两个独立视角；实际争议才追加复核，修改次数有明确上限。
- 增加GitHub App机器人身份、负责人原生Approve后的CI／保护核对、PR基线更新、冲突修复、重新批准与合并结果确认。
- 完成两仓库交付验证；随后完成分页任务的App提PR、原生Approve和自动合并。失败尝试、用量及仍待验证的路径见[验证总览](docs/VALIDATION.md)。

本版发布 Windows 桌面预发行，不上传 PyPI；既有 CLI 功能仍可从对应版本源码使用。合成平台／确定性模型验证与真实交付效果继续分别记录。

## 0.1.0rc1：本地预发行候选

早期 CLI 预发行，提供通用命令、持久化运行、草稿交付、可选 Docker 和本地安装验证。[历史候选说明](docs/runtime-v2/RELEASE-0.1.0rc1.md)保留当时本地验收范围；远端版本见[发行记录](https://github.com/saikaji0314/repopilot/releases/tag/v0.1.0rc1)。

## 更早的诊断原型

最初针对固定Flask／Flaskr任务，只读调查代码并输出修复建议；完成单次模型基线、工具循环和检索对照。保留[兼容命令与结果](docs/legacy/README.md)、[题集](evals/tasks/README.md)和[公开报告](evals/reports/retrieval-comparison-v1.md)。旧计划不作为当前待办。

历史学习计划和逐增量开发记录已退出公开文档入口。需要复核时使用Git历史，例如`git log --all -- docs/runtime-v2/VALIDATION-30.md`；有本机归档时可同时核对原文件哈希。
