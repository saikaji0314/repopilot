# Flaskr：代码诊断样例说明

Flaskr 是 Pallets 官方 Flask 教程中的博客应用，本仓库使用 Flask 3.1.3 标签下的固定源码作为诊断基线。RepoPilot 围绕该样例构造鉴权、密码校验、输入边界、排序、事务与配置问题；Agent 实现位于仓库根目录的 `repopilot/`。

源码位于 [flaskr-tutorial](../flaskr-tutorial)，来源与固定版本见 [仓库选型说明](../FLASKR.md)。上游 README 和 BSD-3-Clause 许可证保留在样例目录中。

## 业务与代码结构

| 模块 | 主要职责 | 诊断关注点 |
| --- | --- | --- |
| [__init__.py](../flaskr-tutorial/flaskr/__init__.py) | 应用工厂、配置加载与蓝图注册 | 配置覆盖顺序、测试数据库路径 |
| [auth.py](../flaskr-tutorial/flaskr/auth.py) | 注册、登录、Session 与登录保护 | 密码哈希校验、用户上下文 |
| [blog.py](../flaskr-tutorial/flaskr/blog.py) | 文章查询与增删改查 | 作者权限、表单校验、排序与事务 |
| [db.py](../flaskr-tutorial/flaskr/db.py) | SQLite 连接、初始化和关闭 | 请求内连接复用、持久化状态 |
| [tests](../flaskr-tutorial/tests) | 临时数据库与 HTTP 测试 | 正常行为、权限边界及数据库断言 |

应用采用 Flask 应用工厂、Blueprint、SQLite 和 Jinja2，测试使用 pytest 与临时数据库。业务实现规模较小，但包含 HTTP 输入、用户身份、SQL 操作与配置传播，便于核对模型是否准确关联现象和代码。

## 在 RepoPilot 中的使用方式

运行器从干净基线创建临时副本，注入任务补丁后交给 Agent 调查。每个任务单独准备工作区；干净源码用于复现与比较，题目答案由评测 Oracle 持有。

初始六题的源码变化只作用于副本。工单任务 007/008 额外加入路由、业务、仓储与缓存模块，用于观察跨文件证据收集。题集与运行方式见 [评测任务说明](../../evals/tasks/README.md)。

若只检查任务环境与缺陷可复现性，在仓库根目录执行以下预检，不调用模型：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_baseline --task flaskr-auth-001 --dry-run
```

环境准备见 [RepoPilot 首页](../../README.md)。Flaskr 作为固定本地实验对象使用，公网部署、账号体系扩展和生产配置不属于本样例的诊断范围。
