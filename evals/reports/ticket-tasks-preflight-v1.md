# 新增 007/008 本地预检记录

无模型调用、无新增外部依赖。目标干净仓库未修改。

| 任务 | 干净扩展版完整测试 | 缺陷版目标测试 | 参考恢复后完整测试 |
| --- | --- | --- | --- |
| flaskr-filter-007 | 31 passed | 2 failed, 2 passed | 31 passed |
| flaskr-cache-008 | 31 passed | 1 failed, 2 passed | 31 passed |

参考恢复方式：在临时副本中反向应用该题 mutation.patch，不是模型生成补丁。因此此表证明题集可复现，不是 Agent 任务成功率。

007 的两个失败为 open/closed 的精确工单集合断言；008 失败为数据库已经持久化后 GET 仍返回旧标题。缺陷版 pytest 返回 1，不以导入错误或收集错误充当故障复现。

执行入口：[本地预检脚本](../../scripts/verify_ticket_tasks.py)，脚本输出每个阶段的命令、退出码、stdout 和 stderr。自动化防退化检查见 [题集测试](../../tests/test_ticket_task_fixtures.py)。

新增内容及运行说明见 [第二期题集说明](../tasks/README.md)。两题的正式模型成绩均尚未产生；008 尚未用于模型调参。

最终全量测试：`python -X utf8 -m pytest -q`，174 passed、2 skipped（12.12 秒）。新增四项检查包括两条完整预检和两条真实符号工具取证检查。共 8 份 task.yaml 均可加载，所有 Oracle YAML 均可解析；两题附加的干净模块快照逐文件哈希一致。跳过项仍不视为安全验证通过。
