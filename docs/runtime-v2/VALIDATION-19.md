# 增量19：GitLab 接入与平台中立发布流程

验证日期：2026-10-18。范围是把 GitHub 专用接入拆成平台中立流程 + 两个 provider，新增 GitLab。**没有真实 GitLab 实例与令牌，全部 HTTP 为模拟**，因此只能称为“适配已实现、单测覆盖”，不能称为“GitLab 交付已跑通”。

## 需求与边界

需求：除 GitHub 外支持其他代码仓库平台（以 GitLab 为例），且不与并行进行的执行层改造冲突（本轮未修改 `repopilot/execution/` 下任何文件）。

明确不做：GitLab Webhook 事件、MR 合并、CI 状态查询、fork 场景的 MR、GitLab Enterprise 的自定义路径／SSO。交付终点仍是人工批准后的草稿合并请求。

## 实现

新增 `delivery/forge.py`：平台识别、凭证路由、以及**唯一的发布流程**。

- `parse_issue_url`：GitHub 与 GitLab 的 Issue 链接严格解析（只接受 https、无凭证、无查询串；GitLab 允许子组）。
- `parse_remote` / `remote_origin`：从 git remote 判断平台，拒绝带凭证与未知 host。
- `load_token`：按平台读取 `GH_TOKEN`／`GITHUB_TOKEN` 与 `GITLAB_TOKEN`／`GL_TOKEN`，进程环境整体优先于 `.env`，缺失即失败。
- `publish`：校验批准摘要、Issue 与基线，推送分支，按标记复用或创建草稿，校验远端头与批准提交一致；逻辑与 GitHub 版逐项对应。

新增 `delivery/gitlab.py`：REST v4 适配。认证头 `PRIVATE-TOKEN`；项目路径整段编码（支持 `group/sub/project`）；Issue 状态 `opened`、编号 `iid`；草稿用标题前缀 `Draft: `；推送认证用户名取 `oauth2`；远端头校验用 `mr.sha` 与 `web_url` 前缀。

改造 `delivery/github.py`：保留原有类名、异常类型、函数与行为，内部改为 provider 钩子（`repository_metadata`／`commit_sha`／`find_pull_request`／`create_pull_request`／`pull_matches`），`publish` 委托给中立流程。`remote_origin` 与 host 参数化，默认仍是 `github.com`。

改造 `cli.py` 与 `delivery/watcher.py`：按 Issue 链接或仓库 remote 打开对应平台；监听器对每个目标仓库各自选择平台，因此一个监听进程可同时盯 GitHub 与 GitLab。`publish` 从该运行的 Issue 链接决定平台，不要求重复声明。

## 验证

- 全量回归：**506 passed、12 skipped**（153 秒，本次提交时点）。新增 35 个用例：`tests/runtime/test_forge.py` 24 个、`tests/runtime/test_gitlab.py` 11 个。
- 新增用例覆盖：GitHub／GitLab URL 解析与拒绝清单（http、缺 `/-/`、编号 0、MR 链接、查询串、带凭证）；`git@` 与 https 两种 remote；host 判定（含自建 GitLab）；凭证按平台读取、进程环境优先、缺失报错；认证头与 `%2F` 路径编码（用 `raw_path` 断言，httpx 会解码 `%2F`）；`PRIVATE-TOKEN` 错误不回显令牌；`Retry-After` 保留；Issue 归一字段；`Draft:` 前缀与 MR 载荷；发布幂等（响应丢失后靠标记复用同一 MR，不重复 POST）；Issue 被编辑后拒绝发布；远端头不匹配时报 `published_head_mismatch`。
- 既有 GitHub 与监听器测试仍通过：`test_github.py` 13 个、`test_watcher.py` 14 个（含两个真实 spawn 子进程用例）。

## 测试过程中修掉的问题

1. **凭证优先级**：最初按变量名逐个查（环境或文件），导致 `.env` 里的 `GITLAB_TOKEN` 压过环境里的 `GL_TOKEN`。改为环境两轮优先、文件次之，与文档口径一致。
2. **测试误用环境凭证**：凭证测试在整仓运行时读到了宿主机已导出的真实 `GH_TOKEN`。测试改为先清空四个变量，断言不依赖宿主机状态。
3. **既有测试的打桩点**：`cli` 不再直接暴露 `load_token`，两个“本地命令不读凭证”的用例改为断言 `cli.open_forge` 不被调用，意图不变。
4. **监听器测试**：`watch()` 不再自建单一客户端，改为按目标仓库打开；测试改为替换 `watcher.open_forge`。
5. **一个既有的时间边界 flaky**：`activation()` 把激活时间截断到秒，若建 Issue 与首次激活恰好跨秒，“新工单”会被当成旧工单，整仓运行偶发失败。测试改为给新工单留 1 秒余量，不再依赖墙上时钟；产品逻辑未改。

## 未验证与限制

- 没有真实 GitLab 实例、令牌或项目，全部为 MockTransport；GitLab 的受保护分支、审批规则、令牌作用域、`Draft:` 在 UI 上的表现都未核实。
- 未验证 fork 场景（`source_project_id` 与目标项目不同）与 GitLab Enterprise 自定义路径。
- 未验证自建实例的证书、代理与限流差异。
- 监听仍是时间轮询，没有 Webhook 增量同步。
- 监听队列的去重键仍是 `仓库路径#编号`，不含平台与 host；同一监听器同时登记 GitHub 与 GitLab 的同名同号 Issue 会被当成同一条。本轮没有改键，避免破坏既有已落盘运行的去重语义。
- 这些限制同样写在 [FORGES](FORGES.md) 与 README 的接入段落里，避免把单测当成真实可用。
