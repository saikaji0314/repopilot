# 任务数据验证记录

> 验证日期：2026-08-05  
> 基线：Flaskr Tutorial 3.1.3  
> 环境：Windows、Python 3.12.4

## 1. 静态结构验证

已使用 YAML 解析器读取全部 `task.yaml` 和 `oracle.yaml`，并检查：

- 正好存在 6 个任务目录；
- 目录名、`task.yaml.id`、`oracle.yaml.task_id` 三者一致；
- 任务 ID 不重复；
- 每条任务包含 7 个规定输出字段；
- 每份 `oracle.yaml` 的量化评分合计为 10 分；
- 每条任务均包含 `mutation.patch`；
- 任务四包含声明的 `regression_test.py`。
- 所有面向模型或评测者的语义字段均包含中文；协议键、ID、枚举、代码和命令保持英文或源码原样。

结果：全部通过。

## 2. 补丁与测试闭环

验证过程对每条任务执行：

```text
从干净基线复制临时工作区
→ git apply --check mutation.patch
→ 应用缺陷补丁
→ 运行目标测试
→ 反向撤销补丁
→ 再次运行目标测试
```

结果：

| 任务 | 缺陷版本 | 撤销补丁后 |
| --- | --- | --- |
| `flaskr-auth-001` | `4 failed, 8 passed` | `12 passed` |
| `flaskr-login-002` | `1 failed, 7 passed` | `8 passed` |
| `flaskr-validation-003` | `1 failed, 11 passed` | `12 passed` |
| `flaskr-ordering-004` | 专用测试 `1 failed` | 专用测试 `1 passed` |
| `flaskr-transaction-005` | 目标测试 `1 failed` | 目标测试 `1 passed` |
| `flaskr-config-006` | 目标测试 `1 failed` | 目标测试 `1 passed` |

六份补丁均通过 `git apply --check`，缺陷版本退出码均为 1，撤销补丁后退出码均为 0。

## 3. 隔离结论

- 所有缺陷只应用在 `.tmp-task-validation` 临时副本；
- 任务四的回归测试只复制到该任务临时副本；
- `target-repos/flaskr-tutorial/` 未应用任何任务补丁；
- 临时验证目录在验证结束后删除。

## 4. 后续运行器必须重复的保护检查

当前人工验证通过不代表以后可以省略运行时保护。正式运行器仍需：

1. 启动时验证基线完整测试；
2. 对补丁执行 `git apply --check`；
3. 只向临时工作区应用补丁和附加测试；
4. 只把 `task.yaml` 与 `agent_visible` 文件提供给 Agent；
5. 任务结束后删除临时工作区；
6. 保存测试退出码、标准输出、耗时和任务 ID。
