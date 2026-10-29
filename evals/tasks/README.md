# 受控代码诊断任务集

任务集为 RepoPilot 提供可重复的输入、缺陷环境与评分依据。每题从固定干净仓库构造单一故障，检查 Agent 能否关联现象、实现与测试，并输出有证据的修改建议和验证方案。

## 场景覆盖

| 任务 | 被测对象 | 诊断场景 |
| --- | --- | --- |
| `flaskr-auth-001` | Flaskr | 作者权限判断反转 |
| `flaskr-login-002` | Flaskr | 密码校验错误 |
| `flaskr-validation-003` | Flaskr | 标题输入边界 |
| `flaskr-ordering-004` | Flaskr | 列表查询排序 |
| `flaskr-transaction-005` | Flaskr | 数据库事务提交 |
| `flaskr-config-006` | Flaskr | 测试配置与数据库路径 |
| `flaskr-filter-007` | Flaskr 工单扩展 | 跨层筛选参数传递 |
| `flaskr-cache-008` | Flaskr 工单扩展 | 更新后的缓存失效 |
| `flask-blueprint-009` | Flask 3.1.3 源码 | 延迟注册与路由默认值 |
| `flask-session-path-010` | Flask 3.1.3 源码 | Session Cookie 路径传播 |

这些任务是对固定版本的受控变异，原始题集不声称覆盖真实用户问题的全部分布。初始题已用于开发与回归；十题对照为小样本实验。来源见 [被测仓库说明](../../target-repos/README.md)。

## 输入与评测信息分离

```text
<task-id>/
├── task.yaml          题面、初始证据、权限及输出协议
├── mutation.patch     运行器使用的缺陷注入补丁
├── oracle.yaml        评测者使用的事实、硬条件和评分标准
├── regression_test.py 可选的确定性回归测试
└── assets/            可选的业务扩展与测试夹具
```

运行器按 `repository.baseline_path` 选择干净基线，按声明复制扩展文件，在临时工作副本中应用补丁。Agent 获得题面、固定初始证据及声明可见的内容，通过工具调查缺陷副本；Oracle 与注入补丁保留在评测端。

干净版本需通过测试，缺陷版本需复现指定失败；参考恢复需再次通过目标与回归测试。工单题预检由 `scripts.verify_ticket_tasks` 执行，Flask 源码题由 `scripts.verify_flask_source_tasks` 执行。参考恢复用于验证题目有效性，模型当前输出诊断与修改建议。

## 输出与评分

统一输出字段为 `status`、`summary`、`root_cause`、`evidence`、`proposed_fix`、`verification` 和 `risks`。YAML 键、任务 ID 和协议枚举使用英文；题面与评分说明使用中文，源码标识保持原样。

严格通过需要满足该题全部 `hard_requirements`，并达到 `oracle.yaml` 的评分线。评分核对根因、关键证据、行为影响、最小修改及验证完整性；无合法最终答案的任务计失败。协议成功、证据命中和严格诊断正确分别报告，费用与耗时包含失败尝试。

`scripts.compare_retrieval` 自动采集工具、Token、费用、读取量与预标注证据命中，严格诊断评分需人工核对 Oracle。评分过程与结果见 [检索对照报告](../reports/retrieval-comparison-v1.md)，题集验证说明见 [VALIDATION.md](VALIDATION.md)。

## 运行单题

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --task flaskr-auth-001 --repetitions 1
```

旧版诊断的模型配置和密钥准备见[旧版说明](../../docs/legacy/README.md#本地运行)。009/010 使用独立 Flask 测试环境；全题运行会包含这两题。新增任务应保持同样的信息分离与可复现结构，并在正式实验前固定评分标准。
