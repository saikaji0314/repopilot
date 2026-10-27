# 仓库平台接入：GitHub 与 GitLab

更新：2026-10-18。范围是**读取 Issue、取默认分支基线、推分支、创建草稿合并请求**，不改交付终点（人工批准具体提交后创建草稿，不合并、不发布软件）。

## 平台如何判断

调用方不需要声明平台，程序从两个来源判断：

1. **Issue 链接**：GitHub 是 `https://github.com/<owner>/<repo>/issues/<n>`；GitLab 的路径带 `/-/issues/<iid>`，允许子组（`group/sub/project`）。
2. **仓库 remote**：`git remote get-url origin` 的 host 是 `github.com` 则 GitHub；是 `gitlab.com` 或 host 含 `gitlab` 则 GitLab。

只接受 `https` 与 `git@host:path` 两种写法，拒绝带用户名／密码的 URL；未知 host 直接失败，不会猜测。

## 凭证

| 平台 | 变量（按顺序） | 说明 |
| --- | --- | --- |
| GitHub | `GH_TOKEN`、`GITHUB_TOKEN` | 读取 Issue、推送分支、创建 PR 需要相应权限 |
| GitLab | `GITLAB_TOKEN`、`GL_TOKEN` | 个人访问令牌至少需要 `api` 与 `read_repository` |

进程环境整体优先于 `.env`；缺少令牌时以 `github_token_required` / `gitlab_token_required` 明确失败，不会用空令牌重试。令牌只进入宿主进程的 HTTP 头与 git 的临时认证头，不写进任务、模型上下文、命令参数或仓库配置文件。

自建 GitLab 用 host 区分，例如 `https://gitlab.example.com/team/app/-/issues/1`；API 基址为 `https://<host>/api/v4`。

## 流程相同，平台差异在 provider 里

发布流程（`delivery/forge.py::publish`）只有一份：校验批准摘要、核对 Issue 与基线、推送分支、按标记复用或创建草稿、校验远端头与批准提交一致。平台差异集中在两个类里：

| 环节 | GitHub（`delivery/github.py`） | GitLab（`delivery/gitlab.py`） |
| --- | --- | --- |
| 认证头 | `Authorization: Bearer` | `PRIVATE-TOKEN` |
| 项目定位 | `/repos/<owner>/<repo>` | `/projects/<namespace%2Fproject>`（整段编码，支持子组） |
| Issue 状态 | `open` | `opened` |
| Issue 编号字段 | `number` | `iid` |
| 草稿表达 | 创建参数 `draft: true` | 标题前缀 `Draft: ` |
| 合并请求路径 | `/pull/<n>` | `/-/merge_requests/<n>` |
| 远端头校验 | `head.sha` + `head.repo.full_name` | `mr.sha` + `web_url` 前缀 |
| 推送认证 | `x-access-token:<token>` | `oauth2:<token>` |

两边共用同一套错误语义：`issue_identity_mismatch`、`issue_repository_mismatch`、`issue_changed_reapproval_required`、`remote_base_changed_rerun_required`、`pull_request_conflict`、`published_head_mismatch`。HTTP 失败只保留状态码（`github_http_403`、`gitlab_http_500`），不回显响应正文。

## 使用

```powershell
# GitHub
.\.venv\Scripts\repopilot.exe issue https://github.com/OWNER/REPO/issues/1 --task task.yaml

# GitLab（含子组）
.\.venv\Scripts\repopilot.exe issue https://gitlab.com/group/sub/project/-/issues/3 --task task.yaml

# 批准后发布；平台由该运行的 Issue 链接决定
.\.venv\Scripts\repopilot.exe approve RUN_ID --commit FULL_SHA --reason "已核对差异与检查"
.\.venv\Scripts\repopilot.exe publish RUN_ID
```

自动监听也按每个目标仓库自己的 remote 选择平台，因此同一个监听器可以同时盯 GitHub 与 GitLab 的仓库；`--status` 等只读命令不加载任何平台凭证。

## 发布中断与资料变更

再次运行`publish RUN_ID`处理未完成发布。若资料依据已变化，先只读查询原分支头和带运行标记的草稿，将结果、原批准及发布身份保留在`publication.reconciliations`；`status RUN_ID`可查看。查询失败时保留原未完成状态，重试查询。该分支不推送、不创建草稿，不将已观察到的旧草稿当作新依据下的交付成功。

完成只读核对后转`context_update`，按[资料刷新流程](KNOWLEDGE.md#资料变化后如何继续)重新实现、检查、审核并批准。发布会先查找已有草稿；头与新批准一致时复用，不再推送或重复创建。若远端分支或草稿头与新批准不同，仍拒绝覆盖；维护者需核对并处理原分支／草稿，程序不会强推或自动创建第二个草稿。

只读核对允许记录已有事实，不能授予新的写入权限。没有过往发布身份的首次发布在资料失效时直接拒绝，尚不访问远端。

## 验证边界

以下都**没有**验证过，不能从单测推断可用：

- 没有真实 GitLab 实例与令牌，本轮全部为 `httpx.MockTransport` 模拟；
- 没有验证 GitLab 的受保护分支规则、审批规则、CI 状态、MR 合并（本来也不在范围）；
- 没有验证 fork 场景下的 MR（`source_project_id` 与目标项目不同）；
- 没有验证 GitLab Enterprise 的自定义路径或 SSO；
- GitLab 的 `Draft:` 前缀语义按官方文档实现，但未在真实实例上确认草稿状态显示；
- GitLab Webhook 事件、监听端的增量同步未实现，仍按时间轮询。
- 监听队列的去重键是 `仓库路径#编号`，不含平台与 host。同一个监听器若同时登记 `group/project` 在 GitHub 与 GitLab 的同号 Issue，会被当作同一条；本轮没有改键，避免破坏既有已落盘运行的去重语义。

首次在真实 GitLab 上使用时，建议先在测试项目里跑一条只读 `status` 与一条 `issue` 接入，确认 API 版本、令牌作用域与项目路径，再走批准与发布。
