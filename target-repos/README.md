# 被测仓库与源码来源

该目录保存 RepoPilot 的固定诊断基线。Agent 在运行器创建的临时副本中调查与执行指定测试，评测任务通过受控变异提供可重复故障。

| 路径 | 来源与用途 |
| --- | --- |
| [flaskr-tutorial](flaskr-tutorial) | Flask 3.1.3 官方博客教程应用；六条基础题与工单扩展的宿主 |
| [flaskr-tutorial-readme](flaskr-tutorial-readme/README.md) | RepoPilot 编写的样例业务与代码导航 |
| [flask-3.1.3](flask-3.1.3) | 官方 Flask 框架源码；Blueprint 和 Session 跨层诊断任务 |
| `flask-3.1.3.zip` | 固定源码归档，哈希用于复现核对 |

Flaskr 的多模块业务适合检查权限、事务与配置推理；框架源码提供延迟注册、共享状态和请求响应生命周期的较大上下文。两者都是固定、可信的本地实验对象。

上游仓库为 [pallets/flask](https://github.com/pallets/flask)。第三方 README 保持原文，许可证保留在 [Flaskr LICENSE](flaskr-tutorial/LICENSE.txt) 与 [Flask LICENSE](flask-3.1.3/LICENSE.txt)。工单扩展和受控任务由 RepoPilot 项目组织，任务设计与结果见 [评测目录](../evals/tasks/README.md)。

Flaskr 使用 Agent 虚拟环境；框架源码任务使用独立的 `.venv-flask-source`，避免测试依赖差异影响诊断。环境、来源和归档校验见 [Flaskr 说明](../docs/phase-0/MAIN_REPOSITORY.md) 与 [框架源码说明](../docs/phase-2/LARGER_REPO.md)。
