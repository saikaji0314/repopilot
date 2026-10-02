# Flaskr 首批六条受控缺陷任务

> 状态：审核通过，6 条任务已拆分为机器可读文件并完成执行验证  
> 被测基线：Flaskr Tutorial 3.1.3  
> 运行模式：第 0～2 期只读诊断  
> 重要说明：以下缺陷均为本项目人工注入，不是 Flask 官方项目的真实缺陷。

## 1. 任务集设计规则

每条任务必须遵守：

1. `target-repos/flaskr-tutorial/` 永远保持干净，当前完整测试为 `24 passed`；
2. 每次从干净基线创建临时副本，只注入一个根因；
3. 测试或复现步骤必须在干净版本通过、缺陷版本失败；
4. Agent 可见输入只描述现象，不泄露变异位置和正确补丁；
5. 隐藏答案、缺陷补丁和评分细则不能进入 Agent 上下文；
6. 第 0～2 期只评价诊断，不要求 Agent 实际修改文件。

### 统一输出字段

六条任务都要求返回：

```yaml
status: diagnosed | insufficient_evidence
summary: 一句话结论
root_cause: 根因及因果解释
evidence:
  - source: 文件、函数、测试或工具结果
    observation: 该证据说明了什么
proposed_fix:
  - 最小修复步骤
verification:
  - 目标验证与完整回归
risks:
  - 影响范围、未确认事实或边界
```

### 统一运行限制

```yaml
constraints:
  mode: read_only
  max_steps: 10
  timeout_seconds: 120
  allowed_tools:
    - list_files
    - search_code
    - read_file
    - run_test
  forbidden_actions:
    - write_file
    - install_dependency
    - network_access
```

## 2. 任务一：文章所有权判断写反

任务一已经完整设计并验证，详见 [FIRST_CONTROLLED_TASK.md](./FIRST_CONTROLLED_TASK.md)。

核心变异是将 `get_post` 中的非作者判断从 `!=` 改成 `==`。缺陷版本的 `tests/test_blog.py` 实测结果为 `4 failed, 8 passed`。

## 3. 任务二：使用明文比较已哈希密码

### 3.1 缺陷构造

只注入一处错误：

```diff
- elif not check_password_hash(user["password"], password):
+ elif user["password"] != password:
      error = "Incorrect password."
```

不要再删除 `if error is None` 或 Session 写入逻辑。一次注入两个错误会产生两个根因：即使 Agent 修复密码校验，登录状态仍然失败，无法公平判断它是否完成任务。

### 3.2 已验证现象

```text
python -m pytest tests/test_auth.py -q -p no:cacheprovider
结果：1 failed, 7 passed
失败：tests/test_auth.py::test_login
现象：正确用户名和密码没有重定向到首页，响应仍为登录页。
```

### 3.3 Agent 可见输入

```yaml
id: flaskr-login-002
title: 正确账号无法登录
task_type: bug_diagnosis

user_request: |
  数据库中的测试用户可以正常查询到，但使用正确用户名 test 和密码 test 登录时，
  页面仍提示密码错误，且没有跳转到首页。错误密码仍然会被拒绝。
  请定位根因，给出代码和测试证据、最小修复计划及验证方式。

initial_evidence:
  command: python -m pytest tests/test_auth.py -q -p no:cacheprovider
  summary: 1 failed, 7 passed
  failures:
    - test: tests/test_auth.py::test_login
      observed: successful login response has no Location header
      expected: redirect Location is /
```

### 3.4 合格输出示例

```yaml
status: diagnosed
summary: 登录逻辑把数据库密码哈希直接与明文密码比较，导致正确密码也不可能相等。
root_cause: >
  flaskr/auth.py 的 login 使用 user["password"] != password 校验密码。
  数据库存储的是 generate_password_hash 的结果，必须通过 check_password_hash 验证明文。
evidence:
  - source: flaskr/auth.py:register
    observation: 注册时保存 generate_password_hash(password) 的返回值。
  - source: flaskr/auth.py:login
    observation: 登录时却直接比较已哈希字符串与请求中的明文。
  - source: tests/test_auth.py::test_login
    observation: 正确凭据无法进入成功重定向分支。
proposed_fix:
  - 恢复 check_password_hash(user["password"], password) 校验。
verification:
  - 运行 test_login 和 test_login_validate_input。
  - 运行完整测试集，确认正确密码成功且错误密码仍失败。
risks:
  - 不应改成数据库存明文，也不应修改测试密码或绕过密码校验。
```

### 3.5 隐藏答案与硬性条件

```yaml
oracle:
  file: flaskr/auth.py
  function: login
  root_cause: stored password hash is compared directly with plaintext input
  minimal_fix: restore check_password_hash(user["password"], password)
  defective_result: 1 failed, 7 passed in tests/test_auth.py
```

硬性通过条件：

1. 同时联系注册时哈希存储和登录时明文直比；
2. 修复必须使用安全哈希校验，不能建议存明文；
3. 必须验证正确密码和错误密码两条路径；
4. 不能把 Session、数据库查询或重定向当作主要根因。

## 4. 任务三：空标题边界判断错误

### 4.1 缺陷构造

只修改 `create` 函数中的标题判断，不修改 `update`：

```diff
- if not title:
+ if title is None:
      error = "Title is required."
```

HTML 表单提交空标题时，Flask 得到的是空字符串 `""`，不是 `None`。因此新判断遗漏了真实边界值。

### 4.2 已验证现象

```text
python -m pytest tests/test_blog.py -q -p no:cacheprovider
结果：1 failed, 11 passed
失败：tests/test_blog.py::test_create_update_validate[/create]
现象：空标题没有显示 Title is required.，而是返回 302 并创建文章。
```

### 4.3 Agent 可见输入

```yaml
id: flaskr-validation-003
title: 创建文章时空标题被接受
task_type: bug_diagnosis

user_request: |
  用户在新建文章页面不填写标题直接提交，系统仍然跳转到首页并保存文章；
  编辑文章时同样的空标题仍能得到正确校验提示。
  请定位创建流程中的根因，给出证据、最小修复计划和验证方式。

initial_evidence:
  command: python -m pytest tests/test_blog.py -q -p no:cacheprovider
  summary: 1 failed, 11 passed
  failures:
    - test: tests/test_blog.py::test_create_update_validate[/create]
      observed: response is 302 and does not contain "Title is required."
      expected: validation message is rendered and no article is created
```

### 4.4 合格输出示例

```yaml
status: diagnosed
summary: create 只判断标题是否为 None，没有拦截表单产生的空字符串。
root_cause: >
  flaskr/blog.py 的 create 使用 title is None；request.form 中存在但为空的字段值是 ""，
  所以 error 保持为空并进入 INSERT 分支。update 使用的空值判断仍然正确。
evidence:
  - source: flaskr/blog.py:create
    observation: title is None 无法匹配空字符串。
  - source: flaskr/blog.py:update
    observation: 对照路径使用 if not title，说明预期语义是拒绝空字符串。
  - source: tests/test_blog.py::test_create_update_validate[/create]
    observation: 空标题实际触发重定向，而不是校验错误。
proposed_fix:
  - 将 create 的判断恢复为 if not title。
verification:
  - 运行 create 对应的空标题参数化测试。
  - 确认空标题未新增数据库记录，并运行完整测试集。
risks:
  - 本任务只规定空字符串；是否拒绝纯空格标题属于额外需求，不能擅自扩展。
```

### 4.5 隐藏答案与硬性条件

```yaml
oracle:
  file: flaskr/blog.py
  function: create
  root_cause: None check does not reject the empty string submitted by the form
  minimal_fix: replace title is None with not title
  defective_result: 1 failed, 11 passed in tests/test_blog.py
```

硬性通过条件：

1. 明确区分 `None` 和空字符串；
2. 定位 `create` 而不是错误地修改 `update`、模板或数据库约束；
3. 修复后必须验证响应和数据库均未接受空标题；
4. 不把“纯空格是否允许”虚构成已确定需求。

## 5. 任务四：文章列表丢失倒序排序

### 5.1 缺陷构造

```diff
  "SELECT p.id, title, body, created, author_id, username"
  " FROM post p JOIN user u ON p.author_id = u.id"
- " ORDER BY created DESC"
```

原测试数据只有一篇文章，所以现有 `test_index` 无法发现排序丢失。这不是放弃该任务的理由，而是说明需要增加一条任务专用回归测试。

任务工作副本中加入以下可见测试：

```python
from flaskr.db import get_db


def test_index_shows_newest_post_first(app, client):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO post (title, body, author_id, created) VALUES (?, ?, ?, ?)",
            ("newest title", "newest body", 1, "2020-01-01 00:00:00"),
        )
        db.commit()

    response = client.get("/")
    assert response.data.index(b"newest title") < response.data.index(b"test title")
```

该测试在干净基线实测为 `1 passed`，删除排序子句后实测为 `1 failed`。

### 5.2 Agent 可见输入

```yaml
id: flaskr-ordering-004
title: 最新文章没有显示在列表顶部
task_type: bug_diagnosis

user_request: |
  博客首页应该按发布时间从新到旧展示文章。新增一篇发布时间更晚的文章后，
  它却显示在旧文章下面。请定位根因，给出代码和测试证据、最小修复计划及验证方式。

initial_evidence:
  command: python -m pytest tests/test_task_ordering.py -q -p no:cacheprovider
  summary: 1 failed
  failures:
    - test: tests/test_task_ordering.py::test_index_shows_newest_post_first
      observed: position of "newest title" is after "test title"
      expected: newest title appears first
```

### 5.3 合格输出示例

```yaml
status: diagnosed
summary: 首页查询没有显式按 created 倒序排序，数据库返回顺序不能保证最新文章在前。
root_cause: flaskr/blog.py 的 index 查询缺少 ORDER BY created DESC。
evidence:
  - source: flaskr/blog.py:index
    observation: SELECT 和 JOIN 后直接 fetchall，没有排序子句。
  - source: tests/test_task_ordering.py::test_index_shows_newest_post_first
    observation: 插入较新文章后，它在响应正文中的位置仍晚于旧文章。
proposed_fix:
  - 在首页文章查询中恢复 ORDER BY created DESC。
verification:
  - 运行排序回归测试和原有 test_index。
  - 运行完整测试集。
risks:
  - created 相同时顺序仍可能不确定；如需求要求稳定顺序，可另行确认是否增加 id 作为次级排序键。
```

### 5.4 隐藏答案与硬性条件

```yaml
oracle:
  file: flaskr/blog.py
  function: index
  root_cause: missing ORDER BY created DESC
  minimal_fix: restore descending created ordering in the SQL query
  clean_regression_result: 1 passed
  defective_regression_result: 1 failed
```

硬性通过条件：

1. 明确指出 SQL 缺少 `ORDER BY created DESC`；
2. 不能依赖 SQLite 当前碰巧返回的插入顺序；
3. 不得通过反转模板循环或修改测试数据掩盖查询问题；
4. 验证必须包含至少两篇时间不同的文章。

## 6. 任务五：更新文章后遗漏事务提交

### 6.1 缺陷构造

只删除 `update` 中的提交，不影响创建和删除：

```diff
  db.execute(
      "UPDATE post SET title = ?, body = ? WHERE id = ?", (title, body, id)
  )
- db.commit()
  return redirect(url_for("blog.index"))
```

### 6.2 已验证现象

```text
python -m pytest tests/test_blog.py::test_update -q -p no:cacheprovider
结果：1 failed
现象：POST /1/update 正常重定向，但重新读取数据库后标题仍为 test title，而不是 updated。
```

### 6.3 Agent 可见输入

```yaml
id: flaskr-transaction-005
title: 更新文章显示成功但数据没有保存
task_type: bug_diagnosis

user_request: |
  用户提交文章修改后收到了正常重定向，没有错误提示；但重新打开文章时仍是旧标题。
  创建和删除文章功能正常。请定位根因，给出证据、最小修复计划和验证方式。

initial_evidence:
  command: python -m pytest tests/test_blog.py::test_update -q -p no:cacheprovider
  summary: 1 failed
  failures:
    - test: tests/test_blog.py::test_update
      observed: database title remains "test title"
      expected: database title is "updated"
```

### 6.4 合格输出示例

```yaml
status: diagnosed
summary: update 执行了 SQL 并重定向，但没有提交 SQLite 事务，连接关闭后修改被回滚。
root_cause: flaskr/blog.py 的 update 在 db.execute(UPDATE ...) 后缺少 db.commit()。
evidence:
  - source: flaskr/blog.py:update
    observation: UPDATE 后直接 return redirect，没有提交事务。
  - source: flaskr/blog.py:create_and_delete
    observation: 其他写路径在 execute 后都会调用 commit，可作为一致性证据。
  - source: tests/test_blog.py::test_update
    observation: 请求成功结束，但新连接读取到的仍是旧标题。
proposed_fix:
  - 在 UPDATE 成功后、重定向前调用 db.commit()。
verification:
  - 运行 test_update，确认数据库持久化新标题。
  - 回归创建、删除和完整测试集。
risks:
  - 不应通过修改测试夹具复用同一连接来掩盖未提交事务。
```

### 6.5 隐藏答案与硬性条件

```yaml
oracle:
  file: flaskr/blog.py
  function: update
  root_cause: UPDATE transaction is not committed before the request ends
  minimal_fix: add db.commit() after db.execute and before redirect
  defective_result: tests/test_blog.py::test_update fails
```

硬性通过条件：

1. 定位 `update` 中遗漏的 `db.commit()`；
2. 解释为什么 HTTP 重定向成功不代表数据库已经持久化；
3. 不能建议开启全局自动提交或修改测试读取方式；
4. 验证必须重新读取数据库中的标题。

## 7. 任务六：传入的测试配置没有应用

### 7.1 为什么临时数据库反而适合这个任务

测试夹具调用：

```python
app = create_app({"TESTING": True, "DATABASE": db_path})
```

这正好能验证应用工厂是否正确应用调用方配置：`TESTING` 应变为真，`DATABASE` 应指向每个测试独立的临时文件。临时数据库不是障碍，而是这条配置任务的可观察结果。

### 7.2 缺陷构造

```diff
  else:
-     app.config.update(test_config)
+     app.config.update()
```

### 7.3 已验证现象

```text
python -m pytest tests/test_factory.py::test_config -q -p no:cacheprovider
结果：1 failed
现象：create_app({"TESTING": True}).testing 实际为 False。
```

### 7.4 Agent 可见输入

```yaml
id: flaskr-config-006
title: 应用工厂忽略调用方测试配置
task_type: bug_diagnosis

user_request: |
  调用 create_app({"TESTING": True, "DATABASE": temporary_path}) 后，
  app.testing 仍然是 False，而且应用没有使用传入的临时数据库路径。
  默认启动 create_app() 的行为正常。请定位配置覆盖流程中的根因并给出验证方案。

initial_evidence:
  command: python -m pytest tests/test_factory.py::test_config -q -p no:cacheprovider
  summary: 1 failed
  failures:
    - test: tests/test_factory.py::test_config
      observed: create_app({"TESTING": True}).testing is False
      expected: True
```

### 7.5 合格输出示例

```yaml
status: diagnosed
summary: create_app 收到 test_config 后进入了对应分支，但没有把该映射更新到 app.config。
root_cause: flaskr/__init__.py 的 else 分支调用空的 app.config.update()，忽略了 test_config。
evidence:
  - source: flaskr/__init__.py:create_app
    observation: test_config 非空时没有传给 app.config.update。
  - source: tests/conftest.py:app
    observation: 夹具依赖该参数设置 TESTING 和临时 DATABASE。
  - source: tests/test_factory.py::test_config
    observation: 传入 TESTING=True 后 app.testing 仍为 False。
proposed_fix:
  - 恢复 app.config.update(test_config)。
verification:
  - 断言 app.testing 为 True。
  - 断言 app.config["DATABASE"] 等于传入的临时路径。
  - 运行完整测试集，确认测试间数据库隔离。
risks:
  - 不应硬编码测试数据库路径；调用方配置应覆盖默认配置。
```

### 7.6 隐藏答案与硬性条件

```yaml
oracle:
  file: flaskr/__init__.py
  function: create_app
  root_cause: test_config is ignored because config.update receives no mapping
  minimal_fix: call app.config.update(test_config)
  defective_result: tests/test_factory.py::test_config fails
```

硬性通过条件：

1. 定位 `create_app` 未应用 `test_config`；
2. 同时说明 `TESTING` 和临时 `DATABASE` 都会受影响；
3. 修复必须应用调用方映射，不能硬编码测试值；
4. 验证必须包含配置值和数据库路径覆盖。

## 8. 统一量化评分

六条任务使用同一套 10 分制结构，但硬性条件按各任务分别判定：

| 维度 | 分值 | 满分要求 |
| --- | ---: | --- |
| 根因正确性 | 3 | 定位正确文件、函数和错误逻辑，并解释因果 |
| 代码证据 | 2 | 引用至少一条正确实现证据并解释意义 |
| 行为证据 | 1 | 将失败测试或复现现象与根因关联 |
| 最小修复计划 | 2 | 只修根因，不修改测试或夹带无关重构 |
| 验证方案 | 1 | 包含目标测试和完整回归 |
| 事实诚实与边界 | 1 | 不虚构工具执行结果，区分事实与推测 |
| **总分** | **10** | **8 分及以上且该任务硬性条件全部通过** |

## 9. 当前验证汇总

| ID | 类型 | 缺陷版本的确定性结果 | 是否需要新增回归测试 |
| --- | --- | --- | --- |
| `flaskr-auth-001` | 权限判断 | `4 failed, 8 passed` | 否 |
| `flaskr-login-002` | 密码校验 | `1 failed, 7 passed` | 否 |
| `flaskr-validation-003` | 表单空值 | `1 failed, 11 passed` | 否 |
| `flaskr-ordering-004` | SQL 排序 | 专用测试 `1 failed` | 是 |
| `flaskr-transaction-005` | 事务提交 | 目标测试 `1 failed` | 否 |
| `flaskr-config-006` | 配置覆盖 | 目标测试 `1 failed` | 否 |

干净主基线已经重新运行完整测试，结果为：

```text
24 passed in 1.11s
```

## 10. 机器可读拆分结果

六条任务已经拆分到 [evals/tasks](../../evals/tasks/README.md)：

```text
evals/tasks/<task-id>/task.yaml        # Agent 可见
evals/tasks/<task-id>/mutation.patch   # 运行器使用，Agent 不可见
evals/tasks/<task-id>/oracle.yaml      # 评分器使用，Agent 不可见
evals/tasks/<task-id>/regression_test.py  # 仅任务四需要
```

每份 YAML 均已完成解析校验，六份补丁均已验证“注入后目标测试失败、撤销后目标测试恢复”。验证记录见 [VALIDATION.md](../../evals/tasks/VALIDATION.md)。

下一步是实现任务运行器和单次 LLM 基线脚本：由运行器从干净基线复制临时工作区、应用补丁和附加测试，并确保只把 Agent 可见材料装入模型上下文。
