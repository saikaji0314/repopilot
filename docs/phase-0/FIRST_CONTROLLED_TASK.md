# 第一条受控缺陷任务：文章所有权判断写反

> 状态：设计及缺陷注入验证已完成  
> 任务类型：只读缺陷诊断  
> 被测基线：Flaskr Tutorial 3.1.3  
> 说明：这是为 Agent 评测人工构造的缺陷，不是 Flask 官方项目的真实缺陷。

## 1. 先解决一个关键困惑

主被测仓库没有错误是正确状态，而不是问题。

本项目采用下面的关系：

```text
干净仓库 = 可重复的起点 + 正确答案参照
缺陷补丁 = 出题手段
缺陷仓库副本 = Agent 实际调查的现场
原有自动化测试 = 确定性验证器
隐藏答案与评分规则 = 评测者使用的判卷标准
```

不要直接在 `target-repos/flaskr-tutorial/` 中长期保存错误，也不要等待上游项目自然出现 bug。每次运行任务时都从干净仓库创建临时副本，再应用该任务唯一的缺陷补丁。

## 2. 本任务怎样构造

干净代码位于 `flaskr/blog.py` 的 `get_post` 函数：

```python
if check_author and post["author_id"] != g.user["id"]:
    abort(403)
```

它的含义是：需要检查作者时，如果文章作者和当前用户不同，则拒绝访问。

本任务只在临时副本中将 `!=` 变成 `==`：

```diff
- if check_author and post["author_id"] != g.user["id"]:
+ if check_author and post["author_id"] == g.user["id"]:
      abort(403)
```

这是一个合适的首条任务，因为：

- 只修改一个运算符，根因唯一；
- 同时影响正常作者和非作者，符合真实权限缺陷的表象；
- 原有测试可以稳定复现；
- 不需要网络、Docker、硬件或新增依赖；
- 可以清楚评价根因、证据、最小修复和验证方案。

## 3. 缺陷注入的实际验证结果

干净基线完整测试结果此前已验证为：

```text
24 passed
```

在临时副本注入上述单一缺陷后执行：

```powershell
python -m pytest tests/test_blog.py -q -p no:cacheprovider
```

实际结果为：

```text
4 failed, 8 passed
```

其中两个最有辨识度的现象是：

- 当前作者访问 `/1/update`，预期 200，实际 403；
- 文章属于其他用户时，请求 `/1/update`，预期在权限检查阶段返回 403，实际继续执行并得到 400。

验证结束后临时副本已删除，干净基线没有被修改。

## 4. Agent 可见输入

下面才是交给单次 LLM 或 Agent 的题面。它描述现象，但不告诉模型具体修改了哪个运算符。

```yaml
id: flaskr-auth-001
title: 文章作者权限行为异常
task_type: bug_diagnosis

repository:
  name: flaskr-tutorial
  version: "3.1.3"
  workspace: runtime-provided

user_request: |
  最近一次权限逻辑调整后，文章作者登录访问自己的编辑页时收到 403；
  非作者访问文章修改接口时，却没有在权限检查阶段得到预期的 403。
  请定位一个最可能的根因，给出代码和测试证据、最小修复计划及验证方式。

initial_evidence:
  command: python -m pytest tests/test_blog.py -q -p no:cacheprovider
  summary: 4 failed, 8 passed
  failures:
    - test: tests/test_blog.py::test_update
      observed: GET /1/update returned 403
      expected: 200
    - test: tests/test_blog.py::test_author_required
      observed: POST /1/update returned 400 for a non-author
      expected: 403

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

required_output:
  - status
  - summary
  - root_cause
  - evidence
  - proposed_fix
  - verification
  - risks
```

单次 LLM 基线和 Agent 版本必须收到相同的 `user_request`、`initial_evidence` 和输出要求。区别是：单次 LLM 一次性收到允许公开的仓库文本且没有工具；Agent 通过工具自行搜索、读取和运行测试。

## 5. 合格输出示例

以下是语义上的参考答案，不要求逐字一致：

```yaml
status: diagnosed

summary: >
  get_post 中的作者权限比较条件写反，导致真正作者被拒绝，非作者反而通过权限分支。

root_cause: >
  flaskr/blog.py 的 get_post 在 check_author 为真时，使用 author_id == 当前用户 id
  作为 abort(403) 的条件。这个条件拒绝的是作者本人，与函数注释和调用方要求相反。

evidence:
  - source: flaskr/blog.py:get_post
    observation: 作者 id 与当前用户 id 相等时执行 abort(403)。
  - source: tests/test_blog.py::test_update
    observation: 作者访问自己的更新页预期 200，实际 403，与反向判断一致。
  - source: tests/test_blog.py::test_author_required
    observation: 非作者没有获得预期 403，说明不相等时错误地绕过了拒绝分支。

proposed_fix:
  - 将作者检查恢复为 post["author_id"] != g.user["id"] 时 abort(403)。
  - 不修改路由、数据库查询或测试期望。

verification:
  - 运行 tests/test_blog.py::test_author_required。
  - 运行 tests/test_blog.py::test_update 和 tests/test_blog.py::test_delete。
  - 运行完整测试集，预期恢复为 24 passed。

risks:
  - 更新和删除共用 get_post，修复后必须同时回归这两条路径。
```

## 6. 隐藏答案

这一部分只供评测器和项目作者使用，不能出现在 Agent 的运行上下文中。

```yaml
oracle:
  root_cause:
    file: flaskr/blog.py
    function: get_post
    fact: author equality comparison is reversed

  injected_change:
    original: 'post["author_id"] != g.user["id"]'
    defective: 'post["author_id"] == g.user["id"]'

  minimal_fix:
    description: replace == with != in the author check
    allowed_files:
      - flaskr/blog.py

  deterministic_verification:
    defective_result: 4 failed, 8 passed in tests/test_blog.py
    repaired_result: 24 passed in the complete suite
```

## 7. 成功标准

### 硬性条件

以下条件全部满足才可能通过：

1. 明确指出 `flaskr/blog.py` 的 `get_post` 作者比较方向写反；
2. 正确解释为什么作者得到 403、非作者绕过 403；
3. 修复方向是把拒绝条件恢复为“作者 id 不等于当前用户 id”；
4. 只读阶段不得修改文件或声称已经完成修复；
5. 如果声称运行过测试，必须在工具轨迹中确实存在对应测试结果。

把 Session、SQL JOIN、登录装饰器或测试夹具当作主要根因，即使文字流畅也判失败。

### 量化评分

| 维度 | 分值 | 满分要求 |
| --- | ---: | --- |
| 根因正确性 | 3 | 定位函数和反向比较，并解释错误逻辑 |
| 代码证据 | 2 | 引用正确实现位置及其语义 |
| 行为证据 | 1 | 将两个相反权限现象与同一条件关联 |
| 最小修复计划 | 2 | 只恢复比较符，不夹带重构或改测试 |
| 验证方案 | 1 | 包含权限目标测试和完整回归 |
| 事实诚实与边界 | 1 | 不虚构工具执行、修改或测试通过 |
| **总分** | **10** | **8 分及以上且硬性条件全部通过** |

## 8. “任务成功”在本阶段是什么意思

第 0～2 期任务是只读诊断，因此成功不是让测试变绿，而是：

```text
正确定位根因 + 给出可核验证据 + 提出最小修复 + 设计正确验证 + 遵守只读权限
```

到允许写操作的阶段，才增加执行成功标准：

```text
补丁只改必要文件 + 目标测试通过 + 完整测试通过 + 没有无关变更
```

这两个阶段必须分别记录为 `diagnosis_success` 和 `execution_success`，不能混成一个模糊的成功字段。

## 9. 从第一条扩展到六条的做法

不要一次凭想象写完六条。使用相同流水线逐条制作：

1. 从一个正常行为和对应测试开始；
2. 只注入一个最小逻辑错误；
3. 运行测试并保存真实失败输出；
4. 确认恢复这一处修改后测试重新通过；
5. 根据真实现象编写 Agent 可见输入；
6. 根据已知变异编写隐藏答案；
7. 最后制定硬性条件和 10 分制评分表。

后续五条应逐步覆盖不同能力，例如登录校验、表单边界、SQL 排序、数据库提交和应用配置；每条都必须先完成测试闭环，不能只在文档中假设它会失败。
