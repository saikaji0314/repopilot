# SWE-bench 接入：输入边界与官方评分预检

本接入冻结一个明确选定的 SWE-bench 开发案例，将解题输入与含答案的评分记录分开，并导出官方评分器接受的补丁 JSONL。准备与导出不调用模型；单题solver入口会计费。不自动处理 GitHub Issue、不创建 PR，也不替代官方评分器。

这与 [两个自编开发夹具](../repair/README.md) 不同：评分规则和验收由第三方基准提供。但参考补丁通过只能证明评分环境可用，不能算 RepoPilot 的模型修复成绩。单题解题入口复用现有实现、检查、审核与有限返工流程；尚未接通批量自动解题与运行结果聚合。

## 输入、输出和边界

| 文件 | 用途 | 可以给解题 Agent 吗 |
| --- | --- | --- |
| `solver-input.json` | 仅 instance_id、repo、base_commit、problem_statement、version | 可以；仍需另外准备固定提交的仓库与依赖 |
| `scoring-record.jsonl` | 官方完整单题记录，可直接交给官方评分器 | 不可以；含参考补丁、验收补丁、测试标识及评分脚本 |
| `manifest.json` | 数据集名称／完整 revision、源文件摘要、单题摘要和评分器完整 SHA | 由控制程序保管 |
| `predictions.jsonl` | instance_id、model_name_or_path、model_patch | 评分器输入 |
| `provenance.json` | 候选类型、补丁／预测文件摘要、冻结案例；初始状态 not_evaluated | 本地追溯 |

解题输入使用字段白名单；新出现的数据集字段不会自动进入提示词。hints_text、答案、验收补丁和测试清单均不传递。导出时重新核对记录和解题输入，变化则拒绝。现有输出目录不能覆盖；新的补丁使用新目录和新的官方 run ID，避免评分缓存混淆。

这些是数据协议约束，不是 OS 访问控制。**整个案例目录不得挂入解题容器或放进可读工作区**；只把 solver-input 和干净基线送入解题环境。外层文件、Git 历史和记忆也不能提供答案或后续修复提交。此增量没有建立这些环境的自动准备与权限隔离，不能把目录分开等同于完成防泄漏。

## 准备案例与导出补丁

从官方数据集按固定 revision 导出 UTF-8 JSONL，保存在被忽略的本地目录。安装下载工具与官方评分器应使用独立环境，不加入 RepoPilot 的核心依赖。数据集、评分器及容器各有独立版本，不能只记录一个软件包版本。

```powershell
# DATA_SHA、HARNESS_SHA 必须是实际使用版本的完整40位提交号，不接受 main/latest
.\.venv\Scripts\python.exe -m repopilot.evaluation.swebench prepare .local/swebench/lite-test.jsonl --instance sympy__sympy-20590 --output .local/swebench/case-01 --dataset-name SWE-bench/SWE-bench_Lite --dataset-revision DATA_SHA --harness-revision HARNESS_SHA

# 参考答案仅用于评分环境预检；与 Agent 补丁显式区分
.\.venv\Scripts\python.exe -m repopilot.evaluation.swebench export .local/swebench/case-01 --output .local/swebench/reference-01 --kind reference --label reference-control

# 后续实际解题后，将冻结的最终 Git diff 导出为候选；标签记录真实模型及实验配置
.\.venv\Scripts\python.exe -m repopilot.evaluation.swebench export .local/swebench/case-01 --output .local/swebench/agent-01 --kind agent --label MODEL-EXPERIMENT --patch candidate.patch
```

`--kind empty` 不接收补丁，生成空补丁对照；`reference` 不接收外部候选；`agent` 必须明确提供补丁。分类由操作者声明，工具不证明补丁由某个模型生成，也不证明它基于正确的工作区；真实解题实验另需绑定运行 ID、代码与配置、基线及提交。任何导出成功都保持 `not_evaluated`，不推断 solved。

## 评分环境预检

按 [官方快速开始](https://www.swebench.com/SWE-bench/guides/quickstart/)安装评分器与 Docker。示例使用 SWE-bench v5 CLI；先核对固定版本的 `swebench eval --help`，不要混用不同版本的环境或数据格式。在 WSL/Linux、上述案例目录的父目录运行：

```bash
swebench eval case-01/scoring-record.jsonl -p reference-01/predictions.jsonl \
  -i sympy__sympy-20590 -r reference-preflight-01 -j 1 -t 300
```

第一次会下载案例镜像。评分器是单独的可信控制程序，容器策略与 RepoPilot 的解题沙箱不同；官方评分通过不意味着我们已经验证了 RepoPilot 在该镜像内解题。记录评分器退出码、逐题 report.json、测试输出、预测文件摘要、镜像 ID／digest 和依赖清单；CLI 退出0本身不代表 resolved。

空补丁通常会被官方工具在评分前标为空答案，并不一定真正启动测试。因此它只能验证“未作答不被记为成功”，不能替代基线测试失败的实测。

## 后续实验协议

单题解题入口如下，会调用真实模型并计费，默认使用现有 `.local/runtime` 累计账本，不另建免费额度：

```powershell
.\.venv\Scripts\python.exe -m repopilot.evaluation.solver .local/swebench/case-01 --task .local/swebench/task.yaml --profile .local/swebench/profile.yaml --output .local/swebench/attempt-01 --label MODEL-EXPERIMENT
```

任务需求必须与冻结题目完全一致，仓库 HEAD 必须为原始 base_commit 且无未跟踪／忽略文件；必须登记公开检查、使用 Docker 并关闭仓库记忆。控制记录、运行目录和输出不得与源码目录互相包含。任务和检查仍由可信操作者提供；入口不自动判断检查是否偷用了答案，也不验证一个任意镜像没有内置答案。

推荐从固定提交做浅克隆，构建只保留依赖、移除原始checkout及其Git历史的解题镜像；不要直接把包含完整历史与评分材料的官方镜像当成已经清洁的解题环境。[SymPy单题镜像示例](../../examples/runtime/docker/SwebenchSympy.Dockerfile)分开使用文件服务Python3.10与项目依赖Python3.9；其他题目需要各自预检，不能沿用一套解释器假设。

入口在模型调用前保存 `attempt.json` 与运行ID，结束后冻结完整Git补丁，包括新增文件；使用临时索引，候选真实索引不变。来源记录绑定题目摘要、任务／设置摘要、原始基线、RepoPilot提交及是否有未提交改动、运行ID、实际工作树与状态摘要。失败尝试保留已经导回worktree的部分补丁；受控预算／调用／时间超限时，容器仍先停止并完整校验再导出，非法产物拒绝导入。其他阶段异常或硬杀不保证容器内未导出的编辑可恢复。`workflow_status` 与官方修复判定分开，审核通过仍为 `not_evaluated`，不会执行批准、推送或PR发布。

未完成的中断现场仍按原工作流显式恢复，本入口不会自动重试或跳过恢复核对。真实基线实验应在干净代码提交上运行，并保留固定镜像、依赖清单及题目来源。单题与批次总预算、可比基线、实验样本的冻结仍需操作者落实。

1. 参考补丁预检成功后，先冻结5–10个明确选出的开发案例和停止条件，再调用真实模型；记下选择方法，不挑选跑成功的题。
2. 固定模型、参数、RepoPilot提交、提示与工具、执行环境、每题预算和总预算；所有尝试均保留，不静默重试后只报最好结果。
3. 同时报告 resolved、未修复、空答案、无效补丁、环境／评分错误、超时，以及费用、调用量和耗时。环境错误单列，并保留总提交数量与总成功率，不能悄悄从分母删除。
4. 根据失败轨迹比较上下文、检索、审核返工或记忆的影响；只改变主要因素。需要比较同类 Agent 时使用相同模型、题目和可比预算。
5. 调优后的效果另用未参与调优的案例确认。公开基准不能保证训练无污染；开发子集成绩不能写成完整 Lite 成绩，Python 修复成绩也不能证明跨语言能力。

API 推理可能收费；本地评分也需要下载与算力。“离线评测”不等于无网络或零成本。恢复、审批、预算、监听和执行隔离继续用专门协议测试，不能由 SWE-bench 分数代替。

上游来源：[评分器源码与许可](https://github.com/SWE-bench/SWE-bench)、[Lite 数据集](https://huggingface.co/datasets/SWE-bench/SWE-bench_Lite)。下载的数据、答案、日志与第三方 checkout 留在本地，不随本仓库分发。实际验证见 [增量13](../../docs/runtime-v2/VALIDATION-13.md)。
