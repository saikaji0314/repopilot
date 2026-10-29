# 主被测仓库：Flaskr Tutorial

> 选择状态：已确定  
> 上游项目：Pallets Flask  
> 固定版本：`3.1.3`  
> 上游目录：`examples/tutorial`  
> 本地基线：`target-repos/flaskr-tutorial/`

## 1. 最终选择

RepoPilot 的首个主被测仓库确定为 Flask 官方教程中的 Flaskr 博客应用：

- 上游仓库：<https://github.com/pallets/flask>
- 固定源码：<https://github.com/pallets/flask/tree/3.1.3/examples/tutorial>
- 发布版本：<https://github.com/pallets/flask/releases/tag/3.1.3>
- 许可证：BSD-3-Clause，许可证文本已包含在教程目录的 `LICENSE.txt` 中；
- 本地路径：`target-repos/flaskr-tutorial/`。

本地源码来自 Flask `3.1.3` 发布压缩包中的 `examples/tutorial`。下载归档的 SHA-256 为：

```text
4C8BFA9D33AC64436875F3F7D213B4FD3BBD855E0A4C4E4AFAF7ACC51F6B64AB
```

归档和其他未入选候选已在验证完成后删除，只保留干净的主被测目录，避免后续 Agent 搜索到无关仓库。

## 2. 应用包含什么

Flaskr 是一个小型但完整的服务端博客应用：

```text
flaskr-tutorial/
├── flaskr/
│   ├── __init__.py       # 应用工厂、配置和蓝图注册
│   ├── db.py             # SQLite 连接、关闭和初始化命令
│   ├── auth.py           # 注册、登录、退出和登录保护
│   ├── blog.py           # 文章列表、创建、更新、删除和权限判断
│   ├── schema.sql        # 用户和文章表结构
│   ├── templates/        # 页面模板
│   └── static/           # 样式
├── tests/
│   ├── conftest.py       # 隔离数据库、客户端和认证夹具
│   ├── test_factory.py
│   ├── test_db.py
│   ├── test_auth.py
│   └── test_blog.py
├── pyproject.toml
├── README.rst
└── LICENSE.txt
```

业务代码涉及应用生命周期、请求上下文、Session、密码校验、SQL 查询、鉴权、所有权验证、表单校验和 CRUD，足以构造多种后端缺陷任务。

## 3. 本地验证结果

验证环境：

| 项目 | 版本 |
| --- | --- |
| 操作系统 | Windows |
| Python | 3.12.4 |
| Flask | 3.0.3 |
| pytest | 7.4.4 |
| 数据库 | SQLite 临时文件 |

验证命令：

```powershell
cd target-repos/flaskr-tutorial
python -m pytest -q -p no:cacheprovider
```

验证结果：

```text
........................                                                 [100%]
24 passed in 1.22s
```

`-p no:cacheprovider` 只用于避免当前受控工作区中的 pytest 缓存目录写入问题，不影响测试语义。测试使用临时 SQLite 文件，每个用例拥有隔离的应用实例和数据库状态，不依赖 MySQL、Redis、Docker、网络或外部服务。

## 4. 规模评估

| 维度 | 结果 | 评价 |
| --- | ---: | --- |
| Python 文件 | 9 个 | 足以跨文件检索，又不会淹没初学阶段 |
| Python 代码与测试 | 约 600 行 | 适合规模受控的基线项目 |
| pytest 测试用例 | 24 个 | 能确定性验证受控缺陷与修复 |
| 核心业务模块 | 4 个 | 工厂、数据库、认证、博客职责明确 |
| 外部服务 | 0 个 | 本地可重复运行 |
| 基线测试时间 | 约 1.22 秒 | 适合频繁回归评测 |

## 5. 为什么它比其他候选更合适

### 相比原 `flaskDemo`

- SQLite 替代 MySQL 和 Redis，无环境硬依赖；
- 已有隔离良好的自动化测试；
- 没有导入时连接远程数据库的问题；
- 有明确 BSD-3-Clause 许可证；
- 来自 Flask 官方仓库，来源和版本容易说明。

### 相比第三方 FastAPI 模板

曾检查 `Donnype/fastapi-template`：它有 MIT 许可证和 pytest，但完整集成测试默认依赖 PostgreSQL/Docker，核心 Todo 业务只有少量文件和接口。Flaskr 在不引入外部服务的情况下提供了认证、权限和 CRUD 等更丰富任务面，因此更适合当前时间限制。

另一个 FastAPI + SQLite + pytest CRUD 候选虽然结构轻量，但未发现明确许可证，因此未采用。

## 6. 如何从正确的上游项目得到缺陷任务

主基线本身的 24 个测试全部通过。正式评测不会等待发现偶然存在的上游缺陷，而是使用“受控缺陷注入”：

1. 永远保留 `target-repos/flaskr-tutorial/` 作为只读干净基线；
2. 每条任务定义一个最小变异补丁，例如错误条件、遗漏提交、错误查询字段或缺失权限检查；
3. 运行任务时复制一份临时工作目录，只对临时目录应用该任务补丁；
4. 至少有一个测试在缺陷版本稳定失败，并在正确修复后恢复；
5. Agent 只看到缺陷快照、用户描述和允许的工具；
6. 评测者答案保存根因、关键证据、目标测试和允许的最小变更；
7. 一条任务结束后丢弃临时目录，下一条任务重新从干净基线创建。

这种方式可以精确控制难度、避免任务互相污染，并能确定性判断修复是否正确。任务文档必须明确标注这些是为评测构造的缺陷，不能把它们描述为 Flask 官方项目的真实漏洞。

## 7. 可覆盖的任务类型

首批 6 条任务可以从以下方向各选一条：

- 注册或登录输入校验错误；
- 密码验证或 Session 用户加载错误；
- 未登录用户错误访问受保护路由；
- 非作者可以更新或删除文章；
- SQL 查询字段、排序或过滤条件错误；
- 数据库提交、应用配置或 CLI 初始化错误。

具体缺陷位置、补丁和评测答案不能放入 Agent 可见输入中。正式任务需要在下一步逐条设计和验证。

## 8. 已知限制

- Flaskr 是服务端 HTML 应用，不是 JSON REST API；但 HTTP 路由、认证、数据库和业务层分析能力仍与后端 Agent 目标一致；
- 约 600 行代码适合第 0～1 期，第 2 期验证大仓库检索时可能偏小；届时可以加入 `flaskDemo` 作为第二只读仓库，或另选一个中等规模项目；
- `pyproject.toml` 对 Flask 和 pytest 没有锁定精确版本。RepoPilot 的评测环境需要在 `evals` 中另外记录并固定已验证版本；
- 当前本地目录包含一个空的 `instance/` 运行时目录，用于适配受控工作区写权限，不包含数据，也不改变应用代码或测试语义。

## 9. 下一步

基于该固定基线设计 6 条正式任务。每条任务先人工验证以下闭环：

```text
干净基线测试通过
→ 应用单一缺陷补丁
→ 指定测试稳定失败
→ 还原正确修复
→ 指定测试和完整测试集通过
```

闭环验证完成后，再把 Agent 可见输入、评测者答案和评分规则保存为机器可读数据。
