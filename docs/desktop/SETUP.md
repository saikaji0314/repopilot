# 桌面连接与评论配置

执行详情页左下角“设置”或标题栏齿轮均可打开设置。当前支持 GitHub App 配置、只读连接检查，以及关联 Issue 的普通评论读取和发送。模型凭证可以保存和校验格式；桌面尚未启动 Agent worker，现有 CLI 任务仍沿用其环境配置。自动接单与完整桌面执行入口的状态见 [实现与验证](IMPLEMENTATION.md)。

## 配置自己的 GitHub 机器人

1. 在“GitHub 机器人”中将配置来源切到“在桌面配置自己的机器人”。选择现有环境配置时，继续遵循 `.env`／进程环境的 `GITHUB_AUTH`，不会覆盖它们。
2. 点击“打开创建页面”，在自己的账号或组织下创建 GitHub App。填写唯一名称与自己的仓库 Homepage；关闭 Webhook，不需要用户 OAuth 回调。
3. 为现有 RepoPilot 工作流配置仓库权限：Contents、Pull requests、Issues 读写；Checks、Commit statuses、Administration 只读；Metadata 自动提供。只安装到需要使用的仓库。[官方权限说明](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app)。
4. 从 App 设置页复制数字 **App ID**，不是 Client ID。安装 App 后，安装设置网址 `/settings/installations/数字` 中的数字就是 **Installation ID**。
5. 在 App 页面生成并下载未加密 RSA PEM 私钥，点击“导入 PEM 私钥”。填写负责人 GitHub 用户名和用于检查的 `owner/repository`。
6. 点击“保存并检查 GitHub 连接”。核对返回的机器人名称与仓库；此检查读取身份与仓库，不发布评论或 PR，也不证明写权限一定可用。

App 注册说明见 [GitHub 官方文档](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)。私钥只发送给经过会话鉴权的本机服务，用于 App 签名；不会交给模型、写入仓库、以明文文件中转或保存后回显。

## 普通评论

关联 GitHub Issue 的执行详情提供“Issue 讨论”。点击刷新读取评论，填写内容并选择发送身份后显式发送：

- **机器人**：使用 App installation 身份，显示为 `App[bot]`。现有个人令牌连接不能冒充机器人。
- **本人**：可选配置细粒度个人令牌，仅限目标仓库并授予 Issues 读写权限；令牌身份必须与登记负责人一致。该令牌不会替代机器人执行身份。[个人令牌说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)。

普通评论带独立标记，即使由登记负责人发送，也不会当作 Agent 问题答复。正式答复仍使用单独的请求入口，核对请求 ID 与版本。桌面当前保存正式答复后仍需 CLI `resume` 才继续。

发送中禁用重复点击。草稿与投递编号在本机保存；发送前记录持久化投递状态，丢失响应后先核对远端的完整正文和实际作者。结果不确定时不会盲目再发：保留草稿点击“核对同一条评论”；必要时先去 GitHub 确认没有发送，再显式启用新草稿。读取超过扫描上限时明确报错，不把部分评论当成完整结果。[Issue comments API](https://docs.github.com/en/rest/issues/comments)。

## 模型、预算与本机文件

“模型与预算”可保存供应商、HTTPS 接口地址、模型 ID、API Key 和默认预算／估价。“保存并校验配置格式”只校验配置，不调用模型，不核对供应商余额，也不重置已有账本。DeepSeek 控制台入口内置于设置；其他兼容供应商填写其官方接口与模型 ID。当前保存的桌面模型配置不会自动覆盖 CLI 环境设置。

Windows 当前用户 DPAPI 加密整个 `desktop-settings.bin`，文件位于所选运行目录。空凭证保留旧值，显式勾选清除才删除；无法解密时停止使用，不退回明文或其他账户配置。加密绑定 Windows 用户环境，搬到另一账户／机器不保证可解密，届时重新导入凭证。[Windows 加密机制](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。

布局与运行目录选择另存 `.local/desktop-host.json`，不含密钥；评论投递记录存于运行目录的 `desktop-comments.sqlite3`。每个运行目录独立保存连接设置，切换不迁移凭证或运行数据，也不停止已有 CLI 任务。

## 调整分栏

导航栏和 Agent 列表的边界都可拖动；双击恢复默认，选中边界后方向键微调、Shift + 方向键快速调整、Home／End 到边界。宽度会按窗口可用空间限制，保留控制台位置。标题栏按钮或 Ctrl+B 隐藏／显示导航；Agent 列表有单独开关。宽度与开关在本机记忆，包括桌面重启后。
