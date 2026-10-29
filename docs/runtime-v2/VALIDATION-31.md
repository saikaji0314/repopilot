# 增量31：负责人原生Approve后的合并协调

日期：2026-10-29。范围为本版M6后的追加需求：负责人在GitHub PR页面Approve，程序检测后核对条件、执行合并并确认实际结果。默认不启用，原草稿批准不授予合并权限。

## 实现与验收范围

已实现单run显式启用、负责人原生审核识别、旧批准／CI／分支保护核对、异步合并检查点与恢复、结果及祖先关系确认、CLI轮询、统一人工请求和离线报告。测试使用真实临时Git、SQLite、原workflow草稿交付及模拟GitHub HTTP；不调用真实模型、不发送真实审核或合并。实现自测通过，尚未经过新增范围的独立审核及真实远端合并验收。

| 检查 | 结果 |
| --- | --- |
| 初轮合并专项 | 22 passed／5 failed，94.12秒；比较接口被原`..`路径防护拒绝，以及工作区改动错误未转换为阻断状态 |
| 修复后专项 | 27 passed，88.68秒 |
| 相关平台／CLI／人工协议／报告／旧工作流回归 | 150 passed，212.62秒 |
| 补充平台人工审核保护、CLI watch及适配器身份校验后的最终合并专项 | 36 passed，90.17秒 |
| 真实只读前提探针 | 拒绝作者自批，旧run状态摘要及预算库字节完全相同；0模型／0远端写入 |

两组通过数有重叠，不能相加；没有重复全量RepoPilot回归或Docker专项。最终CLI仅另有函数文档字符串位置调整，无行为改动，AST与本地链接／凭证形态／空白检查另行核对。比较接口保留原通用路径防护，专用适配器只允许已校验仓库身份和两完整SHA的GitHub比较请求，不扩大通用URL访问。

相关命令为现有venv中的`python -m pytest ... -q -p no:cacheprovider --basetemp SHORT_TEMP --tb=short`，测试根位于忽略的`.local/tmp`，进程级TEMP／TMP／TMPDIR和GIT_CEILING_DIRECTORIES指向专用临时根。本地Git经沙箱外执行授权运行；不改系统配置。相关组包含`test_merging`、`test_github`、`test_gitlab`、`test_forge`、`test_cli`、`test_human`、`test_report`、`test_traceability_report`和`test_workflow`；最终36项见`tests/runtime/test_merging.py`。

覆盖负责人／他人／机器人／旧SHA／过期／撤销／changes requested、最新CI失败及缺失、草稿／冲突、远端head／基线变化、本地代码变化、策略篡改、普通答复不授权、请求202与结果区分、响应丢失后409复用、停用前后副作用核对、请求失败／过期不重发、合并祖先验证、重复执行、统一人工来源及报告转义。未把GitHub的accepted／pending当作merged。

历史Support PR #2只读预检：作者和当前凭证身份同为saikaji0314，draft=true、merged=false。GitHub不允许作者自批，真实负责人Approve链路需要另一个机器人／App身份和目标分支严格CI保护。当前两个已交付草稿不自动升级为可合并。

## 剩余真实验收与限制

需要使用独立机器人／App创建PR，负责人以本人身份将草稿转为Ready并提交Approve，在具有strict必需CI及有效审核保护的目标分支验证真实合并；保存负责人review、异步请求UUID、merge SHA和目标分支包含关系。当前尚无可用的独立身份，不能用本地文字批准代替GitHub原生审核。新增范围不宣称已完成真实合并验收，原M6两仓库草稿结果保持不变。

初版只支持GitHub、传统branch protection、merge commit方式和单run前台轮询；ruleset-only、merge queue、stacked PR、squash／rebase及GitLab合并未支持。基线变化停止要求重新验证，不自动重写或强推候选。平台保护之外的仓库管理配置变更不由客户端锁定。独立审核和真实验收前应保留默认关闭，不在既有草稿上直接启用。

协议、使用方法和实现限制见[MERGING](MERGING.md)。
