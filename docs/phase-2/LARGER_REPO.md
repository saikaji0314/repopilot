# 第二期较大仓库：Flask 3.1.3 与收尾路线

已下载固定版本、准备独立测试环境、制作两条受控任务并完成本地预检；未调用付费模型。第二期尚未验收完成。

## 1. 选择理由

选择 [Pallets 官方 Flask 仓库的 3.1.3 标签](https://github.com/pallets/flask/tree/3.1.3)，不是另一个小型 Flask Demo。沿用熟悉的生态，学习配置、Blueprint 注册、请求/响应和 Session 的多层传播，控制理解成本。

实测规模（包括注释、文档字符串和空行）：

| 实现目录 | Python 文件数 | 行数 |
| --- | ---: | ---: |
| flaskr-tutorial/flaskr | 4 | 345 |
| flask-3.1.3/src/flask | 24 | 9199 |

这是中等规模过渡样例，不是大型企业代码库；有多层实现、同名方法和独立 tests/docs，适合比较定位证据的效率。框架源码任务不代表覆盖所有业务开发场景。

[上游测试说明](https://palletsprojects.com/contributing/tests) 使用 pytest。本机兼容环境中原始上游测试为 490 passed；不需数据库服务、硬件、浏览器、Docker 或公网部署。仍只对可信固定版本开放测试，不宣称具备任意仓库执行沙箱。

## 2. 来源与固定版本

- 官方源：`https://github.com/pallets/flask`，标签 `3.1.3`。
- git clone 连接失败；实际下载 `https://codeload.github.com/pallets/flask/zip/refs/tags/3.1.3`。
- 本地源码：`target-repos/flask-3.1.3`；原始压缩包：`target-repos/flask-3.1.3.zip`。
- 归档 SHA256：`4C8BFA9D33AC64436875F3F7D213B4FD3BBD855E0A4C4E4AFAF7ACC51F6B64AB`。
- 归档快照没有 Git 元数据，不声称校验了 commit SHA。复现时下载同一归档并核对哈希，保留源码和上游 LICENSE.txt。
- 009/010 是对正确上游版本的人为变异，不声称是上游现存 bug，也没有修改远程仓库。

## 3. 测试环境与源码导入

当前 Agent 环境 pytest 9.1.1 无法导入上游测试使用的 `_pytest.monkeypatch.notset`。独立环境初装 Werkzeug 3.1.9 时，上游还有一项请求上下文行为测试失败。没有删除测试或修改干净实现，而是固定兼容版本：pytest=8.4.2、Werkzeug=3.1.5、Click=8.3.1，这些版本可在该仓库 uv.lock 找到；其他依赖按实测环境冻结。

本机已准备；复现安装命令（需要网络）：

```powershell
.\.venv\Scripts\python.exe -m venv .venv-flask-source
.\.venv-flask-source\Scripts\python.exe -m pip install -r configs/flask-source-test-requirements.txt
```

原 `.venv` 未降级。这个环境只用于旧版本本地实验，不是生产安全版本推荐，不用于公网服务。快照对应 Windows/Python 3.12，跨平台需重新预检。

测试子进程将**当前临时副本的 src**置于 PYTHONPATH 前面，避免导入 site-packages 中的干净 Flask。新增回归测试都断言 flask.__file__ 位于当前工作区，否则即使行为测试通过也拒绝验收。

## 4. 两条任务

| ID | 现象与考点 | 注入后目标回归 | 干净/恢复后全套 |
| --- | --- | --- | --- |
| flask-blueprint-009 | 单路由默认值污染其他路由；延迟注册、共享字典和默认值优先级 | 2 failed, 1 passed | 493 passed |
| flask-session-path-010 | 子路径应用的 session cookie 忽略应用根路径；配置、响应保存、设置/删除 cookie 链路 | 1 failed, 3 passed | 494 passed |

每题有 task.yaml、oracle.yaml、mutation.patch、regression_test.py。Oracle 包含硬要求、评分及关键证据范围，不复制给 Agent。参考恢复是在临时副本中反向应用注入补丁，不是模型完成了修复。

009 作新仓库开发题，010 预留为策略冻结后的首次模型对照。两题目前只跑确定性测试，未发送给解题模型；作者知道参考答案，所以不称为独立外部盲测。如果根据 010 的模型结果调参，应把它转为回归题。

## 5. 接入调整与运行

原运行器将所有任务套在全局 Flaskr 上。现在 Agent 和 baseline 按 task.repository.baseline_path 选仓库，限制在项目 target-repos 内，每个仓库每批先做一次干净预检。

repository_runtime.py 负责路径和受控环境选择。`runtime: flask-source` 指向固定 `.venv-flask-source`，模型不能指定解释器；预检与 RunTestTool 使用同一解释器和 src 环境，旧八题继续使用原环境。CLI 无需新增参数。

无模型费用的验证：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.verify_flask_source_tasks
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
```

之后需要真实试跑时（会收费，本次未执行）：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --task flask-blueprint-009 --repetitions 1
```

现在共 10 题，`--all` 会包含新两题；缺少源码/独立环境时不能直接跑全部。不指定 repetitions 仍使用配置默认次数。

不要直接付费跑新仓库的单次全文基线：dry-run 快照约 118 万字符，现有收集器没有整体 Token 门槛。第二期主对照应是四工具/五工具 Agent；若比较全文基线，先设计独立预算和截断规则。

## 6. 完成第二期的剩余顺序

1. **修检索质量**：已修合并后的精确匹配优先级，符号片段预算增至 4000 字符；索引 warnings 和各种截断状态仍待完善，见 [暂缓问题](./BACKLOG.md)。可以先单次试跑开发题 009。
2. **对照开关已实现**：`--retrieval-mode code/symbols` 选择四工具/五工具；关闭时不建索引，同步允许工具提示。保存实际 schema、配置、提示词和任务/源码哈希。本次固定版本全题初筛流程见 [评测说明](./EVALUATION.md)。
3. **开发后冻结**：007、009 用于开发，策略/预算固定后再对 008、010 做首次对照；旧六题做回归。调参用过的题不称未见数据。
4. **10 题 × 两种模式**：先各一次初筛；不要求必须调用新工具，不给其中一组偷偷加预算。有必要再预先指定代表题和重复次数，两组同时重复，不只重跑失败组。
5. **分层评估**：模型实际收到的关键证据命中率、严格通过率、调用数、读取量/重复片段、Token、费用、耗时。环境/工具/协议失败均保留；索引构建耗时单列。
6. **收尾报告**：题目数量已超过原门槛，但仍需较大仓库上可解释的改善及失败复盘。若没有收益，记录未证明并定位瓶颈，不以工具实现完成代替效果验收。

先以 009 的实际成本估算整轮开销；建议 A/B 设置 ¥10 软上限，到达后先审原因，同时服从项目总预算 ¥200。本次未花模型费用，也不承诺新检索必然胜出。
