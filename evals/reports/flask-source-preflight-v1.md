# Flask 3.1.3 较大仓库任务预检

未调用模型。来源、归档哈希及环境复现见 [较大仓库说明](../../docs/phase-2/LARGER_REPO.md)。原始上游基线：490 passed。

| 任务 | 带回归测试的干净版 | 注入后目标回归 | 参考恢复后完整测试 |
| --- | --- | --- | --- |
| flask-blueprint-009 | 493 passed | 2 failed, 1 passed | 493 passed |
| flask-session-path-010 | 494 passed | 1 failed, 3 passed | 494 passed |

真实 run_test 工具获得同样的失败摘要，验证预检和模型工具使用同一解释器与 workspace/src。回归测试有源码来源断言，防止误用安装目录的 Flask。

原始输出：flask-source.json（原始产物未随仓库分发，标识：`../runs/preflight/development-run-06/flask-source.json`），含各阶段命令、退出码、stdout/stderr 和工具结果。参考修复为反向应用 mutation.patch；这只是题集有效性验证，不是 Agent 诊断/修复成绩。

多仓库 baseline dry-run 两题均通过，源码快照分别 1,177,992 / 1,177,859 字符。暂不直接付费运行全文基线；第二期主对照为相同预算下旧/新工具 Agent。

环境失败历史：项目 pytest 9.1.1 导致上游内部导入错误；初装独立环境 Werkzeug 3.1.9 时为 1 failed、489 passed。固定兼容依赖后才完整通过，未删测试、放宽断言或修改干净源码掩盖环境失败。

当前共 10 题，新增两题尚未产生模型成绩，第二期检索收益尚未验证。

最终项目全套测试：183 passed、2 skipped（26.17 秒）。新增检查覆盖任务仓库选择、越界路径拒绝、子进程 src 环境不污染父进程、指定解释器的工具执行、多仓库 runner 编排以及两条题目的完整可复现性。跳过项不计为安全验证通过。
