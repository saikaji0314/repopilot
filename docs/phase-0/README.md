# 任务建模与单次 LLM 基线

本模块定义 RepoPilot 的诊断问题、输出协议和评测口径，并提供单次 LLM 对照。它为 Agent 主动调查建立参照：模型获得完整仓库快照时能回答什么，通过工具收集上下文时又会在哪些环节发生变化。

## 任务与协议

任务输入包含用户问题、固定初始证据、仓库版本和权限约束。结构化答案包含根因、证据、修改建议、验证方案与风险；使用同一 `BaselineAnswer` 协议校验基线与 Agent 输出，避免因为格式不同而无法比较。

任务目录分离三类信息：`task.yaml` 提供可见问题，`mutation.patch` 供运行器注入缺陷，`oracle.yaml` 供评测者核对事实和硬条件。每次实验从干净仓库创建临时副本，先确认基线测试通过，再验证注入确实产生预期故障。

## 仓库选择

初始样例采用 Flask 3.1.3 官方教程应用 Flaskr。应用工厂、鉴权、表单、SQLite 事务和配置加载形成多个明确业务分支，已有测试使缺陷能够确定性复现。样例无需硬件或外部数据库服务，适合在本地控制环境变量。

首批六题覆盖鉴权条件、密码比较、输入边界、查询排序、提交持久化和配置路径。扩展后的十题说明见 [任务目录](../../evals/tasks/README.md)，较大框架样例见 [被测仓库索引](../../target-repos/README.md)。

## 基线流程与结果

基线运行器准备缺陷副本，收集允许范围的源码快照与初始证据，执行单次模型调用，再校验并保存答案和用量。它不使用 Agent 工具循环。

六题各运行两次，共 12 次调用，全部找到正确根因，严格通过 8/12，估算总费用 ¥0.379332。主要失败来自验证方案缺少数据库状态或配置路径的明确断言。这说明定位正确与诊断闭环需要分别衡量。

详见 [基线评分与失败分析](../../evals/reports/baseline-v1.md)。

## 复现与代码入口

在仓库根目录执行，无模型调用的单题预检：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_baseline --task flaskr-auth-001 --dry-run
```

真实基线使用同一入口去掉 `--dry-run`，可加 `--repetitions 2`；需配置密钥并承担模型费用。全文基线适用于小型样例，Flask 框架源码的全文输入需先评估上下文与费用，详见 [较大仓库说明](../phase-2/LARGER_REPO.md)。

| 模块 | 职责 |
| --- | --- |
| [baseline.py](../../repopilot/baseline.py) | 单次调用与结果归档 |
| [repository_snapshot.py](../../repopilot/repository_snapshot.py) | 仓库快照收集 |
| [schemas.py](../../repopilot/schemas.py) | 统一诊断答案协议 |
| [workspace.py](../../repopilot/workspace.py) | 工作副本与缺陷注入 |
| [verification.py](../../repopilot/verification.py) | 干净基线与缺陷测试验证 |

设计细节：[仓库选型](MAIN_REPOSITORY.md) · [模型选型记录](MODEL_BASELINE.md) · [基线运行说明](BASELINE_RUNNER.md) · [任务格式示例](TASK_SAMPLE.md)。
