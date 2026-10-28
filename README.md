<div align="center">
  <a href="https://github.com/saikaji0314/repopilot">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
      <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.svg">
      <img src="assets/logo-light.svg" alt="RepoPilot" width="420">
    </picture>
  </a>
  <h3>从 Issue 到可审核的草稿 PR。</h3>
  <p>面向可信代码仓库的开源研发 Agent，保留改动、检查与审批证据。</p>
  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License: MIT"></a>
    <a href="docs/runtime-v2/INSTALL.md"><img src="https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&amp;logoColor=white" alt="Python 3.11+"></a>
    <a href="https://github.com/langchain-ai/langchain"><img src="https://img.shields.io/badge/Built%20with-LangChain-1c3c3c" alt="Built with LangChain"></a>
    <a href="https://github.com/langchain-ai/langgraph"><img src="https://img.shields.io/badge/Agent%20loop-LangGraph-1c3c3c" alt="Agent loop: LangGraph"></a>
    <a href="https://github.com/saikaji0314/repopilot/stargazers"><img src="https://img.shields.io/github/stars/saikaji0314/repopilot?style=flat" alt="GitHub Stars"></a>
  </p>
  <p>
    <a href="#快速开始">快速开始</a> ·
    <a href="docs/runtime-v2/DEMO.md">交付演示</a> ·
    <a href="docs/runtime-v2/README.md">使用文档</a> ·
    <a href="docs/runtime-v2/ARCHITECTURE.md">架构设计</a>
  </p>
</div>

## 工作方式

接入 GitHub／GitLab Issue 或本地需求，在独立 worktree 中实现、自检并接受审核返工。人工批准具体提交后，推送分支并创建草稿 PR／MR；合并与部署由仓库维护者决定。

`Issue → worktree → 实现 → 独立检查 → 审核／返工 → 人工批准 → 草稿 PR`

- **独立验证**：编排层重跑登记检查，审核绑定固定提交，人工审批绑定完整 SHA。
- **默认先规划**：新任务先整理任务说明与顺序子任务，集中提问最多 3 批；`--no-plan`保留小任务单Agent路径。依赖有环或缺前置产物不执行，子Issue按需映射。
- **受控并行**：可并行计划最多2个实现槽位，每个子任务独立worktree；结果串行整合到父临时分支，再对组合代码独立检查，不直接更新main。
- **可恢复执行**：SQLite 保存阶段状态与累计预算，支持暂停、显式恢复及轮询接单。
- **可选 Docker 隔离**：文件工具与检查禁网执行、限制资源，校验产物后导入；失败不回退宿主。
- **可追溯记忆**：可选文件事实书签，带提交来源、文件指纹和人工确认，变更后失效。
- **离线交接报告**：集中查看差异、检查、审核、费用、审批和 PR，不依赖前端服务。

## 看一条真实交付

[Issue #1](https://github.com/saikaji0314/zhixianji-jobs/issues/1) → 104 项独立检查 → 两轮审核、一次返工 → 人工批准 → [草稿 PR #2](https://github.com/saikaji0314/zhixianji-jobs/pull/2)（未合并）。

![真实交付报告：改动、检查、审核和草稿 PR](docs/demo/delivery-report.gif)

动图为真实历史运行的只读回放。[查看交付证据与失败记录 →](docs/runtime-v2/VALIDATION-05.md)

## 快速开始

以下为源码安装的 PowerShell 示例，需要 Python 3.12、Git、Node.js 和模型 API。Docker 可选；默认本机执行，仅接入可信仓库。

```powershell
git clone https://github.com/saikaji0314/repopilot.git
cd repopilot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e .
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

在 `.env` 填写 `DS_API_KEY`，准备示例的 Git 基线，再运行一项小任务：

```powershell
$demo = "examples/runtime/fixtures/javascript"
git -C $demo init -q
git -C $demo add .
git -C $demo -c user.name=RepoPilot -c user.email=demo@example.test commit -m "demo baseline"
# 调用真实模型，会产生 API 费用
.\.venv\Scripts\repopilot.exe run examples/runtime/javascript-implement.yaml
# 将 RUN_ID 替换为运行输出中的 ID
.\.venv\Scripts\repopilot.exe report RUN_ID --html .local/demo/report.html
```

接入自己的仓库时，在任务 YAML 中登记需求、路径和检查命令。工具不限制代码语言，构建依赖与验收环境需自行准备。[安装、配置与升级 →](docs/runtime-v2/INSTALL.md)

## 文档

| 我想…… | 入口 |
| --- | --- |
| 接 Issue、审批、创建草稿 PR／MR | [平台接入](docs/runtime-v2/FORGES.md) · [自动监听](docs/runtime-v2/WATCH.md) |
| 配置模型、限额或恢复任务 | [模型](docs/runtime-v2/MODELS.md) · [运行协议](docs/runtime-v2/README.md) |
| 按需规划、集中提问或映射子Issue | [计划与提问](docs/runtime-v2/PLANNING.md) · [人工协作](docs/runtime-v2/HUMAN.md) |
| 并行实现、独立worktree或内部整合 | [并行与整合](docs/runtime-v2/PARALLEL.md) |
| 配置隔离执行、代码记忆或项目资料 | [Docker](docs/runtime-v2/SANDBOX.md) · [Memory](docs/runtime-v2/MEMORY.md) · [项目资料](docs/runtime-v2/KNOWLEDGE.md) |
| 理解实现与技术取舍 | [架构与代码导航](docs/runtime-v2/ARCHITECTURE.md) · [设计](docs/runtime-v2/DESIGN.md) |
| 查看评测与安装候选 | [评测方法与结果](evals/swebench/README.md) · [0.1.0rc1](docs/runtime-v2/RELEASE-0.1.0rc1.md) |

## 当前边界

当前为实验性候选，适合可信仓库中的明确小任务。GitHub 有真实交付；GitLab、Qwen／兼容接口与 Claude 仅完成离线适配验证。规划、评论自动投递／轮询、子Issue映射和并行内部整合目前只有本地机制与模拟验证，没有真实评论／子Issue写入、真实模型并行效果或真实平台整链路结论。Docker 已验证真实容器机制，尚无该后端真实模型交付或并行子任务结论；不提供 microVM 级隔离。

评测协议、逐题结果和记忆收益对照见评测文档；开发样本不代表通用修复率。

## 许可证

RepoPilot 自有代码采用 [MIT](LICENSE)。Flask 源码与教程样例来自 Pallets，保留其 BSD-3-Clause 许可证；第三方材料保留原有许可。
