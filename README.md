# RepoPilot

面向 Python 后端仓库的研发任务分析 Agent。通过受控工具收集代码与测试证据，输出结构化根因分析、修改建议和验证计划。

## 当前能力与边界

- 手写 Agent Loop、工具协议、预算限制与有限错误恢复。
- 提供目录浏览、代码搜索、文件读取、白名单测试和 Python AST 符号检索。
- 保存消息、工具轨迹、配置快照与评测指标；支持四工具/五工具对照。
- 当前仅分析与测试，不自动修改被测代码。审批、实际补丁、持久化/API 和 Web 界面属于后续需求，不是已交付能力。

## 本地运行

在仓库根目录使用 PowerShell，建议 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pytest -q
Copy-Item .env.example .env
```

在 `.env` 填入自己的百炼密钥后，可运行一条任务（会产生模型费用）：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_agent --task flaskr-auth-001 --repetitions 1
```

运行 009/010 或全题评测前，需按 [较大仓库说明](docs/phase-2/LARGER_REPO.md) 安装独立的 Flask 源码测试环境。仓库不附带虚拟环境、密钥或原始模型对话。

## 需求与评测

- [原始开发需求与分期验收](PROJECT_GOAL.md)：保留功能目标、预算、安全边界与阶段规划。
- [后续里程碑](docs/ROADMAP.md)：受控修改、审批、恢复与服务化。
- [基线报告](evals/reports/baseline-v1.md)：严格通过 8/12。
- [只读 Agent 收尾报告](evals/reports/agent-v2.md)：严格通过 4/6。
- [检索对照报告](evals/reports/retrieval-comparison-v1.md)：四工具 5/10，五工具 4/10，尚未证明符号检索的端到端收益。

以上实验的样本与配置不同，不能直接解读为连续提升曲线。题集是受控构造任务，不是 SWE-bench 成绩。阶段文档中的早期检查与已完成记录供理解实现过程；当前检索评测结论以上述对照报告为准。

报告使用中性批次标识，数值和评分保持原样。原始运行产物不随仓库分发，文档中的产物路径仅为标识，不代表本地已有文件。重新运行会生成新的日志及时间戳。

`target-repos` 包含固定 Flask 源码和教程样例，保留其上游许可证；具体来源见 [样例说明](docs/phase-0/MAIN_REPOSITORY.md) 与 [Flask 源码说明](docs/phase-2/LARGER_REPO.md)。
