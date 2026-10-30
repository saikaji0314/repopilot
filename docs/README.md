# 文档导航

第一次接触项目，先读[项目首页](../README.md)，再按需要选择入口。当前文档对应`dev`开发版；软件发行另行准备。

## 安装与使用

| 要做的事 | 文档 |
| --- | --- |
| 安装、设置密钥、选择运行目录 | [安装与升级](runtime-v2/INSTALL.md) |
| 写本地任务，运行、暂停、恢复和查看报告 | [任务格式与CLI](runtime-v2/README.md) |
| 选择模型，设置调用、工具、时间和费用上限 | [模型与限额](runtime-v2/MODELS.md) |
| 接GitHub／GitLab Issue、发布草稿 | [平台接入](runtime-v2/FORGES.md) |
| 配置App机器人 | [GitHub App](runtime-v2/GITHUB-APP.md) |
| 检测新Issue并排队执行 | [监听队列](runtime-v2/WATCH.md) |
| 在GitHub Approve后自动更新、合并和确认 | [受控合并](runtime-v2/MERGING.md) |
| 看一次真实流程、制作报告演示 | [演示步骤](runtime-v2/DEMO.md) · [媒体来源](demo/README.md) |

## 协作与执行机制

| 主题 | 文档 |
| --- | --- |
| 任务规划、拆分与提问 | [规划](runtime-v2/PLANNING.md) · [人工协作](runtime-v2/HUMAN.md) |
| 多个工作副本如何整合 | [并行与内部整合](runtime-v2/PARALLEL.md) |
| 两个审核视角、争议和有限返工 | [审核协议](runtime-v2/REVIEW.md) |
| 执行权限与容器限制 | [Docker执行](runtime-v2/SANDBOX.md) |
| 资料、决定、来源与失效 | [项目资料](runtime-v2/KNOWLEDGE.md) · [事实记忆](runtime-v2/MEMORY.md) |
| 代码检索与上下文 | [检索](runtime-v2/SEARCH.md) · [上下文](runtime-v2/CONTEXT.md) |
| 模块职责、入口和设计取舍 | [架构](runtime-v2/ARCHITECTURE.md) · [设计](runtime-v2/DESIGN.md) |

## 结果、历史与维护

- [桌面版需求与验收](desktop/REQUIREMENTS.md)：执行详情、可调分栏、连接向导和普通评论已有开发实现；[配置步骤](desktop/SETUP.md)与[验证和限制](desktop/IMPLEMENTATION.md)区分现有功能与自动接单等后续范围。
- [当前验证总览](VALIDATION.md)：哪些离线测过、哪些真实跑过、哪些尚未验证；失败记录继续保留。
- [公开评测](../evals/swebench/README.md)：冻结题目、逐题指标和效果边界；[旧版诊断](legacy/README.md)保留历史命令和复现说明。
- [版本沿革](../CHANGELOG.md)：只记录产品变化；[后续范围](ROADMAP.md)描述尚未完成的工作。
- [交付边界](runtime-v2/DELIVERY-POLICY.md)、[组合验收方法](runtime-v2/DELIVERY-ACCEPTANCE.md)与[预发行检查](runtime-v2/PRE-RELEASE.md)：维护者准备交付或发行时使用。

公开仓库保留当前指南、设计、接口、来源、可复现评测和验证结论。个人开发计划、学习步骤、逐次交接与原始运行数据放在被忽略的`.local/`；旧增量细节仍可通过Git历史查阅。目录名`runtime-v2`沿用已有链接，表示当前通用运行时文档。
