# 仓库平台接入：GitHub 与 GitLab

更新：2026-10-27。范围是**读取 Issue、取默认分支基线、推分支、创建草稿合并请求**，以及新增的只读能力预检、父Issue评论问答和按需子Issue映射。不改交付终点（人工批准具体提交后创建草稿，不合并、不发布软件）。

## 平台如何判断

调用方不需要声明平台，程序从两个来源判断：

1. **Issue 链接**：GitHub 是 `https://github.com/<owner>/<repo>/issues/<n>`；GitLab 的路径带 `/-/issues/<iid>`，允许子组（`group/sub/project`）。
2. **仓库 remote**：`git remote get-url origin` 的 host 是 `github.com` 则 GitHub；是 `gitlab.com` 或 host 含 `gitlab` 则 GitLab。

只接受 `https` 与 `git@host:path` 两种写法，拒绝带用户名／密码的 URL；未知 host 直接失败，不会猜测。

运行和监听队列的去重键包含平台、host、项目和编号，例如`github:github.com/owner/repo#1`与`gitlab:gitlab.example.com/owner/repo#1`。旧`owner/repo#1`运行在再次接入时，仅在保存的来源URL相同时原子迁移索引，运行ID、批准、费用和计数保持原样；不同来源分别建身份。没有保存来源URL的旧行需人工核查，不能猜测归属或自动重跑。旧队列行保留原key和状态，评论续接按已绑定run_id收尾。相关机制及真实重复接入核对见[增量30](VALIDATION-30.md)。

索引升级不重写运行JSON或删库。已升级去重索引后，应使用本版继续接单／监听；更早版本不能识别新key，不能直接降级接管同一活动库。旧运行可通过本版继续查看、核对和恢复。

## 凭证

| 平台 | 变量（按顺序） | 说明 |
| --- | --- | --- |
| GitHub | `GH_TOKEN`、`GITHUB_TOKEN` | 读取 Issue、评论、建子Issue、推送分支、创建 PR 需要相应权限 |
| GitLab | `GITLAB_TOKEN`、`GL_TOKEN` | 个人访问令牌至少需要 `api` 与 `read_repository`；评论／建单还需对应项目权限 |
| 登记负责人 | `REPOPILOT_OWNER` | 唯一可从父Issue评论采纳业务答复的登录名；不是批准授权 |

进程环境整体优先于 `.env`；缺少令牌时以 `github_token_required` / `gitlab_token_required` 明确失败，不会用空令牌重试。令牌只进入宿主进程的 HTTP 头与 git 的临时认证头，不写进任务、模型上下文、命令参数或仓库配置文件。`REPOPILOT_OWNER` 只是登记身份，不代替平台认证；其他登录名和机器人评论只作线索。

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

## 能力与授权预检

评论、子Issue和建单都是新增的外部写入，必须先做只读预检：

```powershell
repopilot human preflight RUN_ID --output .local/runtime
repopilot plan show RUN_ID --output .local/runtime
```

预检返回：平台与仓库身份、Issue编号、当前令牌登录名、登记负责人、评论读取能力、原生子Issue能力、建单权限和检查时间。它只发 `GET`，不改任何远端状态。`write_verified=false` 是刻意保留的事实：读取成功和令牌身份不能证明写权限，真正的写失败会在 `human post` 或 `plan map` 时明确返回。

预检状态只有 `supported`、`unsupported`、`unknown` 三种。`unknown` 不当作成功：评论读取未知时拒绝发送；原生子Issue未知时退回链接映射；建单权限未知时仍可尝试，因为 GitHub 的仓库权限字段不能覆盖细粒度 Issues 权限，失败会保留记录而不是重复尝试。

## 父Issue评论问答

Issue任务进入`needs_input`时默认自动投递批次；操作者也可以手工重试或补查：

```powershell
repopilot human post RUN_ID --output .local/runtime
repopilot human sync RUN_ID --output .local/runtime
```

`post`把当前待答批次写成一条父Issue评论，正文带稳定HTML标记、批次号、阻塞阶段和编号问题。发送前先把标记与意图写入任务检查点，成功后才保存评论ID和链接；同一标记已存在时复用。`sync`按发送时保存的评论ID定位批次，并按`created_at`核对评论确实晚于批次；GitLab Notes接口显式请求升序，但客户端仍做时间排序，不能只依赖平台返回顺序。只有作者等于`REPOPILOT_OWNER`的文本会被采纳；机器人、系统评论、自身标记评论、纯引用和问题原文回声都被忽略。负责人“引用批次 + 回答”会剥掉引用再采纳。没有批次评论、没有有效答复或评论已删除时保持等待，不把无答复当同意。重复`sync`返回已答复记录，不重复增加需求版本。

监听器在每轮`poll_seconds`内按空余worker有节制地扫描等待中的批次，自动投递并导入回复后恢复任务；不会为同一运行重复创建模型工作。评论答复只用于业务澄清，不是交付批准；批准仍使用绑定完整SHA的`approve`。评论正文不包含原始模型会话、密钥或本机私密材料，常见凭证形态会被拒绝发送。

## 子Issue映射

`repopilot plan map RUN_ID`把计划中`track=issue`的子任务映射到父Issue：

| 平台 | 行为 |
| --- | --- |
| GitHub | 预检原生子Issue接口；可用时创建Issue并尝试原生关系；否则保留普通Issue并标为链接映射 |
| GitLab | 没有可依赖的原生子Issue层级，创建普通Issue并明确标为`linked`，不声称创建原生子项 |
| 建单不受支持 | 记录`local_only`，不破坏本地计划，也不假装已创建外部工单 |

每个子任务使用稳定标记；重复执行或响应丢失后先查标记和已保存映射，不重复创建。映射记录父Issue、计划版本、外部编号和说明；程序不关闭、不回写或删除外部工单。子Issue标题和正文按评论渠道同样的常见凭证形态检查，不合格时不创建并保留失败原因。建单前的权限预检失败会保留失败记录并报错，不静默创建。

## 发布中断与资料变更

再次运行`publish RUN_ID`处理未完成发布。若资料依据已变化，先只读查询原分支头和带运行标记的草稿，将结果、原批准及发布身份保留在`publication.reconciliations`；`status RUN_ID`可查看。查询失败时保留原未完成状态，重试查询。该分支不推送、不创建草稿，不将已观察到的旧草稿当作新依据下的交付成功。

完成只读核对后转`context_update`，按[资料刷新流程](KNOWLEDGE.md#资料变化后如何继续)重新实现、检查、审核并批准。发布会先查找已有草稿；头与新批准一致时复用，不再推送或重复创建。若远端分支或草稿头与新批准不同，仍拒绝覆盖；维护者需核对并处理原分支／草稿，程序不会强推或自动创建第二个草稿。

复用时也重新只读查询当前分支头，不能用PR／MR返回的提交或恢复时的历史观察代替。分支缺失报`remote_branch_missing`，与批准提交不同报`remote_branch_conflict`，均保存为`publication_failed`及对应`stop_reason`，不推送、不创建、不标记成功。维护者处理远端后可以重试；仅当本次观察的分支头、PR／MR头与当前有效批准一致时完成交付。

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
- 本轮新增评论、子Issue和建单接口仍全部是 `httpx.MockTransport` 模拟；没有真实 GitHub／GitLab 评论、原生子Issue或建单写入。GitHub 原生子Issue接口是否对当前仓库／令牌可用，以实际接入时 `human preflight` 的 `supported/unsupported/unknown` 为准。
- 评论扫描最多5页×100条；超过时明确失败，不把截断结果当作完整答复。没有实现评论编辑／删除的增量同步。
- `REPOPILOT_OWNER` 是本机登记的唯一负责人登录名，不是企业身份认证；如果令牌属于同一个人，程序仍以评论标记排除自己发出的批次正文。

首次在真实 GitLab 上使用时，建议先在测试项目里跑一条只读 `status` 与一条 `issue` 接入，确认 API 版本、令牌作用域与项目路径，再走批准与发布。
