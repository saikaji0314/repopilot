# 安装 0.1.0rc1 候选包

RepoPilot 当前为实验性预发行候选，适用于可信代码仓库中的小型开发任务。GitHub 有历史真实交付记录；GitLab、Qwen 与 Claude 仅完成离线适配验证。复杂 SymPy 任务的[真实复测](VALIDATION-18.md)仍有调查超限和修复失败，不据此承诺生产修复率或无人值守交付。

需要 Python 3.11+、Git；本候选实际验证为 Windows＋CPython 3.12。其他宿主系统的默认目录规则有单元测试，尚未完成原生冷安装验证。Docker 可选，需另按[沙箱说明](SANDBOX.md)准备固定镜像和仓库依赖。

## 从 wheel 安装

在独立启动目录创建虚拟环境；将候选 wheel 放在该目录。以下为 PowerShell 命令：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\repopilot-0.1.0rc1-py3-none-any.whl
.\.venv\Scripts\repopilot.exe --version
.\.venv\Scripts\repopilot.exe --help
```

Claude 原生适配器另装 extra：`python -m pip install ".\repopilot-0.1.0rc1-py3-none-any.whl[anthropic]"`，使用对应虚拟环境解释器。维护者复现本次依赖时，在安装命令中加 `-c constraints.txt`；约束文件随源码包提供，测试环境版本以验证记录为准。

在启动目录创建 `.env`，凭证只填在本机文件或环境变量中：

```dotenv
DS_API_KEY=填写自己的模型密钥
DS_MODEL=deepseek-flash
# 接入对应平台时再填写，单纯本地任务不需要平台令牌
# GITHUB_TOKEN=
# GITLAB_TOKEN=
```

[模型配置](MODELS.md)说明其他供应商、价格估算及未实测边界；[平台接入](FORGES.md)说明 GitHub／GitLab URL、令牌和监听配置。不要把 `.env` 提交给 Git，也不要将它复制进 Agent 的仓库工作区。

创建 `task.yaml`，示例中的路径和检查替换成自己的可信 Git 仓库：

```yaml
schema_version: '2.0'
id: first-task
repository: D:/path/to/your/repository
request: '描述一个边界明确、可验证的小改动'
checks:
  unit:
    argv: ['python', '-m', 'pytest', '-q', 'tests/test_feature.py']
    timeout_seconds: 60
```

检查使用项目实际的解释器／构建命令；`{python}` 表示 RepoPilot 解释器，不会自动选择目标项目的虚拟环境。非 Python 仓库可以登记 `node`、`go` 等命令。源码包的 `examples/runtime/` 提供其他示例。

```powershell
# 会调用真实模型并计费；默认本机执行，只接入可信仓库
.\.venv\Scripts\repopilot.exe run .\task.yaml
.\.venv\Scripts\repopilot.exe jobs
# 用运行输出中的真实 RUN_ID 替换占位符
.\.venv\Scripts\repopilot.exe report RUN_ID --html .\report.html
```

运行需要 Git 提交身份和项目已有依赖。Issue、审批与草稿 PR／MR 操作见[平台接入](FORGES.md#使用)，批准绑定完整提交 SHA；合并与部署由仓库维护者决定。

## 配置和数据目录

| 用法 | 默认 `.env` | 默认运行目录 |
| --- | --- | --- |
| 现有源码 checkout／可编辑安装 | 源码根目录 `.env` | 原源码根目录 `.local/runtime` |
| wheel 安装，Windows | 启动时当前目录 `.env` | `%LOCALAPPDATA%/RepoPilot/runtime`，缺省时使用用户 `AppData/Local` |
| wheel 安装，Linux | 启动时当前目录 `.env` | `$XDG_DATA_HOME/repopilot/runtime`，未设或非绝对路径时使用 `~/.local/share/repopilot/runtime` |
| wheel 安装，macOS | 启动时当前目录 `.env` | `~/Library/Application Support/RepoPilot/runtime` |

不会向父目录搜索 `.env`，也不向 site-packages 写入运行数据。显式函数参数仍优先；命令行 `--output` 优先于默认运行目录。环境变量可以覆盖两种安装方式：

```powershell
$env:REPOPILOT_ENV_FILE = 'D:\RepoPilot-config\private.env'
$env:REPOPILOT_RUNTIME_DIR = 'D:\RepoPilot-data\runtime'
.\.venv\Scripts\repopilot.exe jobs
```

覆盖路径支持 `~`，相对路径按启动目录解析；建议对常驻监听使用绝对路径。模型、GitHub、GitLab 与监听 worker 共用同一环境文件选择规则。环境变量中的凭证优先于文件同名值，配置读取不修改进程环境。帮助、jobs、status、离线报告与仓库记忆管理不要求模型凭证。

升级现有安装时，继续显式指向原运行目录；既有 SQLite、预算、运行 ID 和 worktree 路径不会自动迁移或清零。源码可编辑安装继续使用原路径，但若改为普通 wheel，必须用 `REPOPILOT_RUNTIME_DIR` 或 `--output` 指回旧目录才能查看旧运行。不要在任务运行中替换安装或搬迁目录。

第一代 `legacy` 代码保留作历史参考；本 wheel 只提供新版 `repopilot` 命令，不提供依赖源码题集与配置的 `run-baseline`。源码运行方式见[旧版说明](../legacy/README.md)。

本地候选包尚未代表远端 Release 或 PyPI 已发布。包摘要和本轮实际检查见[增量20](VALIDATION-20.md)。
