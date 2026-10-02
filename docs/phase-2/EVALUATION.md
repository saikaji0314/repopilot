# 第二期检索对照与评测

固定当前代码、提示词及 agent.yaml，10 题各跑四工具/五工具一次，共 20 次；不要求必须使用符号工具，不挑选最好结果重跑。顺序按任务交替 code→symbols / symbols→code。本批为初筛，单次样本不能给出统计显著性结论。

初筛已完成：20 次运行，四工具严格 5/10、五工具 4/10，估算总费用 ¥2.187446；评分边界与复盘见 [报告](../../evals/reports/retrieval-comparison-v1.md)。原 metrics.json 保留采集时的待审字段；审核后的完整指标与评分在 reports 下的同名 JSON 中。

## 使用方式

```powershell
# 全题对照，会调用付费模型
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_retrieval --cost-limit 10

# 指定题做配对试跑，不与另一批次混为一次实验
.\.venv\Scripts\python.exe -X utf8 -m scripts.compare_retrieval --task flaskr-filter-007 --task flask-blueprint-009

# 普通运行默认五工具；以下单独运行四工具
.\.venv\Scripts\python.exe -X utf8 -m scripts.run_agent --task flask-blueprint-009 --retrieval-mode code --repetitions 1
```

四工具模式不建索引，不在工具说明或任务允许列表中引用 search_symbols；五工具模式保留定义定位优先规则。两组其余提示、模型及预算相同。Oracle 和参考补丁只由评估端读取，不进入模型输入。

## 产物与评分

- `evals/runs/comparison/<id>/manifest.json`：执行计划、非敏感配置、RepoPilot/被测仓库 Python 源码 SHA256。
- 每个 mode/task/run-01 的 `runtime.json`：实际工具 schema、初始消息、非敏感配置、任务文件 SHA256、测试解释器、索引耗时。原 summary/messages/trace/final_answer 继续保存。
- 根目录 `metrics.json`：全部尝试的轮数、工具数、Token、估算费用、Loop 耗时、索引耗时、工具使用数、输出字符数、源代码行重复比例。
- 证据命中只算工具实际返回的行。范围命中表示目标范围至少一行被返回，不表示完整覆盖；同时报告目标行覆盖率。旧题没有 evidence_targets 时为 null，人工核对证据，不临时修改 Oracle。
- 严格评分遵循每题原 oracle.yaml：至少 8 分且全部硬要求满足；completed 仅表示交付合法答案。人工读取答案、观察及必要源码后另存 review.json 和报告；不用关键词自动评分替代判断。
- 超限、协议/工具失败均计入分母。没有 summary 的环境异常记录失败并停止整批，费用未知，不继续付费；未执行计划单列。

费用按配置单价估算，达到 10 元后不发起下一次运行；当前调用可能越线，SDK 内部重试可能没有完整 usage，不是账单硬上限。默认不自动重跑失败任务。可信固定仓库本地测试的既有隔离/路径限制见 BACKLOG。

结论分别讨论严格通过、关键证据、重复读取与成本。若五工具组未调用新工具或未改善，记录未证明收益，不因工具实现完成而宣布效果验收通过。
