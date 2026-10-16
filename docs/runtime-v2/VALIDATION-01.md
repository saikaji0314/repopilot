# 增量 1：调查运行时验证

日期：2026-10-16。环境：Windows、CPython 3.12.4、Node.js 24.19.0。模型：DeepSeek 官方接口 `deepseek-flash`，非思考模式。框架直接版本固定于 `pyproject.toml`，已安装依赖约束见 `constraints.txt`。

## 离线验证

变更前执行 `python -m pytest -q`：193 passed、4 skipped。

最终执行 `python -m pytest -q -rs`：230 passed、5 skipped。新增运行时测试为 37 passed、1 skipped；涵盖配置隔离、通用 UTF-8 源码、路径拒绝、命名检查、真实进程超时、Node 执行、并发费用预留、费用未知保留、框架循环、供应商参数及输出限制。

跳过项：三个符号链接用例因当前 Windows 权限无法创建链接；两个 Flask 源码测试因未准备独立源码测试环境。链接拒绝有实现，创建真实链接后的用例本轮未验证；不能把跳过视为通过。

打包验证：`python -m pip install --no-build-isolation --no-deps -e .` 成功，`repopilot` 命令入口可运行。包元数据中失效的 `PROJECT_GOAL.md` 引用改为实际 `README.md`，Python 最低版本与已使用的 `StrEnum` 对齐为 3.11；本轮实际验证仅使用 3.12。

## 真实模型冒烟与失败记录

| 尝试 | 目标 | 实际检查 | Agent／CLI 结果 | 模型调用 | 保守估算费用 |
| --- | --- | --- | --- | ---: | ---: |
| 1 | JavaScript 合成样例 | Node 1 passed | 图与报告完成，CLI 因 GBK 无法输出检查字符而退出失败 | 4 | ¥0.026172 |
| 2 | Flaskr 公开快照 | pytest 12 passed | 最终输出 1024-token 上限后解析失败，无有效报告 | 6 | ¥0.083382 |
| 3 | Flaskr 公开快照 | pytest 12 passed | 报告 `planned`，CLI 退出 0 | 5 | ¥0.069378 |
| 4 | JavaScript 合成样例 | Node 1 passed | 报告 `planned`，CLI 退出 0 | 4 | ¥0.026364 |

四次请求链合计 19 次模型调用，账本估算 ¥0.205296，包含失败尝试；实付账单未核对。调试中输出上限和提示有所改变，这不是固定条件效果对照，也不构成成功率统计。Flaskr 使用 Pallets Flask 3.1.3 官方教程源码，保留来源与 BSD-3-Clause 许可；没有向真实业务仓库注入缺陷。

针对失败的改动：CLI JSON 使用 ASCII 转义以适配 Windows 控制台；默认输出上限改为 2048 tokens，报告提示要求简洁；识别 `finish_reason=length` 为 `max_output_tokens`，不继续当作一般格式错误重试。新增回归测试覆盖两条路径。

用量观察在框架解析结构化答案前完成，解析失败仍记录已返回的用量；没有用量时保留费用预留。模型和工具的循环由框架执行，本项目没有另写消息循环。

原始运行材料保存在被忽略的本机运行目录，不随 Git 分发。表格仅保留必要汇总，不包含凭证或原始模型会话。

## 结论与限制

已验证 Python／pytest 和 JavaScript／Node 的调查与检查工具链，以及真实模型的结构化报告接线。没有验证自动修复、审核 Agent、PR、恢复或所有语言的构建环境。

报告通过结构校验不等于内容正确。抽查发现：Flaskr 报告建议增加匿名首页覆盖，但原 `test_index` 已有匿名请求；JavaScript 报告把 `unit` 检查名放在文件路径字段。当前文件证据与检查证据尚未建模区分，也未验证每条引用和语义判断。实际执行的检查结果以运行摘要和工具事件为准，后续需完善证据契约及独立评测，不能把本次报告当作自动验收结论。

本机执行、估算预算和链接检查的边界见[运行时说明](README.md)。
