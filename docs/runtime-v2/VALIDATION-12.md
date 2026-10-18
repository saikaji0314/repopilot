# 展示与包结构整理验证

日期：2026-10-18。范围为改进项目入口与单次运行的只读展示，并把新旧实现按职责分开。近期交付终点仍为草稿 PR，不实现 CI/CD 状态接入、自动合并或部署；取舍见 [DELIVERY-POLICY](DELIVERY-POLICY.md)。完整代码地图见 [ARCHITECTURE](ARCHITECTURE.md)。

## 需求与实现

| 需求 | 实现与完成条件 |
| --- | --- |
| 项目 README 是产品入口 | 介绍定位、能力、真实案例、入门、接入方式及边界；不把当前开发进度当首页内容 |
| 展示接近仓库管理界面 | 白底、细边框、横向证据导航、右侧运行信息和检查列表；差异高亮，审核长摘要可展开；布局参考 [Primer PageLayout](https://primer.style/product/components/page-layout/)，样式自行实现，不引入 React 或外部样式依赖 |
| 首页可直观看演示 | [16.7 秒 GIF](../demo/delivery-report.gif)为真实历史报告的五幅浏览画面；868×610，统一裁切，未拉伸；[PNG](../demo/delivery-overview.png)按真实格式编码，修正 JPEG 数据误用 PNG 扩展名及预览面板外白边 |
| 新旧实现职责分开 | 第一代诊断、AST 检索和工具进入 `repopilot.legacy`；新版展开到 core、orchestration、agents、execution、storage、memory、delivery、presentation；新版独立评分 evaluation 保留 |
| 兼容既有调用和运行 | runtime 模块名映射到同一个真实模块对象；旧模块 CLI 可用，控制台入口更新；YAML、状态字段、SQLite、预算、run ID、worktree 与目标仓库路径不变 |
| 报告不误导 | completed 不等于交付；失败检查和失效批准不能画成通过；未记录发现数不补成零，时间线截断不重置审核轮次；通过后的旧问题标为历史发现 |

报告按读取、格式化、证据片段和布局分开，分别位于 `presentation.report / formatting / sections / layout`，CSS 在导出时内联。报告不调用模型或 GitHub，也不执行批准或发布。没有增加管理控制台或后台服务。

## 验证

- 最终全量回归：**415 passed，9 skipped，123.34 秒**，覆盖原有新版和旧版测试，不把跳过计作通过。
- 独立真实 Docker 专项：**27 passed，56.39 秒**。使用已准备的 Python／Node 固定镜像，实际验证移动后的文件服务源码定位、两类语言工作流、暂停恢复、非法产物和清理。模型为 FakeModel，不是修复效果实验。
- 报告、CLI 和架构专项：**27 passed，3.39 秒**。包含原有转义／脱敏／离线能力，以及检查退出0但携带错误、审核发现数缺失与截断后轮次的回归。
- 新旧 CLI help 和可编辑安装通过；wheel 检查包含新包、容器 worker 和报告 CSS，不含 `.local`、`.env` 或 pyc。
- 实际浏览器核对历史 published、awaiting_approval 页面，检查证据导航与窄屏无页面横向溢出。GIF 各帧保存真实 URL 与滚动位置；生成器只裁切、量化和编码，不生成页面内容。
- Markdown 入口和迁移后的源码链接检查；历史不存在的 PROJECT_GOAL 链接改为明确的缺失文件说明。

本地保留迁移时的失败与修正：旧导入和相对路径遗漏、未改动评测夹具的换行变化导致固定指纹失败、报告拆分遗漏显式导入，以及截图视口／外框问题。固定夹具已恢复原字节，后续评分回归通过；没有调整 Oracle 来让测试通过。

## 边界

没有新调用真实模型、处理新 Issue 或执行远端写入，生产预算账本未变。新 GIF 来源为历史 `issue-1-1d3bc15b6211`，不是本轮重新完成交付，也不是实时执行录屏。旧视频保留为历史版本，说明见[演示材料](../demo/README.md)。

此次按包迁移，不把状态字典改为新协议，不替换 Agent 框架，不声称完成独立发行包拆分。后续完整代码讲解与技术取舍复盘另行进行。
