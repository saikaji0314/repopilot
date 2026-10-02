# RepoPilot 评测任务目录

每个子目录代表一条独立、可重复执行的受控缺陷任务。

```text
<task-id>/
├── task.yaml          # Agent 可见：题面、初始证据、权限和输出约定
├── mutation.patch     # Agent 不可见：运行器在临时仓库中注入的缺陷
├── oracle.yaml        # Agent 不可见：事实答案、硬性条件和评分规则
└── regression_test.py # 可选；运行器按 task.yaml 声明复制到临时仓库
```

## 隔离要求

- Agent 的上下文加载器只能读取 `task.yaml` 以及其中声明为 `agent_visible` 的文件；
- 禁止向 Agent 暴露 `mutation.patch`、`oracle.yaml` 或任务目录列表；
- 所有补丁都相对于 `target-repos/flaskr-tutorial/` 应用；
- 每次任务从干净基线创建新的临时工作区，结束后丢弃；
- 运行器必须先确认干净基线测试通过，再注入缺陷；
- `task.yaml` 中的 `initial_evidence` 是固定输入，不能被运行器悄悄改写。

## Schema 约定

- `schema_version`：当前为 `1.0`；
- `repository.baseline_path`：相对于项目根目录；
- `initial_evidence`：单次 LLM 与 Agent 共同可见；
- `workspace_additions`：需要复制进临时仓库且允许 Agent 查看；
- `constraints`：Agent 工具权限与资源限制；
- `required_output.fields`：输出必须包含的字段和语义；
- `oracle.yaml` 中所有 `hard_requirements` 必须满足，且总分达到通过线才算诊断成功。

## 语言约定

本任务集采用“接口英文、语义中文”：

- YAML 键、任务 ID、评分项 ID 和枚举值使用稳定英文，便于 Python 代码解析；
- 文件路径、函数名、代码表达式、命令和测试 ID 保持源码原样；
- 用户题面、现象说明、预期行为、参考诊断、硬性条件和评分描述统一使用中文；
- 不翻译 `diagnosed`、`insufficient_evidence` 等协议值，避免后续代码出现多套枚举；
- 评分依据语义和事实，不以是否包含某个中文关键词作为唯一通过条件。

该约定适合当前中文任务和 Qwen 模型，也不会阻碍未来替换模型或编写自动化运行器。
