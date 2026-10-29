# 配置独立的GitHub App机器人身份

负责人使用个人账号审核，RepoPilot使用App安装身份读Issue、推功能分支、创建／更新PR并执行获批合并。无需另注册个人账号，不使用用户OAuth登录；App不能替负责人提交Approve。

## 在GitHub创建

进入个人Settings → Developer settings → GitHub Apps → New GitHub App。

| 项目 | 配置 |
| --- | --- |
| Name | 唯一名称，例如`yourname-repopilot-agent` |
| Homepage URL | 你的RepoPilot仓库地址 |
| Redirect URI／callback／Setup URL | 留空 |
| Wildcard／OAuth during installation／Device Flow／Redirect on update | 关闭 |
| Expire user authorization tokens | 保持默认；本方案不用用户授权令牌 |
| Webhook Active | 关闭，无需Webhook URL或Secret |
| Where can this App be installed? | Only on this account |

Repository permissions只设置：

| 权限 | 级别 | 用途 |
| --- | --- | --- |
| Contents | Read and write | 获取代码、推PR分支、合并 |
| Pull requests | Read and write | 创建／更新PR、读取人工审核 |
| Issues | Read and write | 接任务、澄清评论和必要子Issue |
| Checks | Read-only | CI检查结果 |
| Commit statuses | Read-only | 提交状态 |
| Administration | Read-only | 读取分支保护；不修改保护规则 |
| Metadata | Read-only | GitHub自动提供的仓库基本信息 |

其他Repository permissions、Organization、Account和Enterprise permissions保持No access。只有确需修改CI工作流时，再单独决定是否增加Workflows写权限；当前不默认授予。

创建后记录**App ID**（不是Client ID）。生成并下载Private key（`.pem`）。通过Install App安装到个人账号，选Only select repositories，只选需要处理的仓库。安装配置URL中的`/settings/installations/数字`可用于查找**Installation ID**。权限变更后需在安装侧确认新权限。

## 本机配置

安装可选依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[github-app]"
```

把下载的PEM文件保存在本机私密目录，例如被Git忽略的`.local/secrets/repopilot-github-app.pem`。在本机`.env`填写：

```dotenv
GITHUB_AUTH=app
GITHUB_APP_ID=你的App数字ID
GITHUB_APP_INSTALLATION_ID=你的安装数字ID
GITHUB_APP_PRIVATE_KEY_FILE=.local/secrets/repopilot-github-app.pem
REPOPILOT_OWNER=你的个人GitHub登录名
```

相对私钥路径从所用`.env`所在目录解析，也可用本机绝对路径。无需把私钥正文或安装令牌发到聊天。`GITHUB_AUTH=app`明确选择App，配置缺失或签名失败会停止，不回退到旧个人令牌；原`GITHUB_TOKEN`可以保留作为将来显式切回`GITHUB_AUTH=token`的配置。

宿主使用RS256签名短期JWT，换取安装令牌；API和Git访问前按到期时间续期，令牌不写入运行状态、不进入模型或容器。App身份显示为`应用slug[bot]`，不请求不适用于安装令牌的`/user`。

可对已有运行执行只读平台预检：

```powershell
repopilot human --output RUNTIME preflight RUN_ID
```

确认viewer为预期`[bot]`身份，registered_owner仍是你本人。预检不创建PR、不提交审核、不推送或合并。旧个人身份创建的PR不会自动变成App作者；新App创建的PR才可由你本人进行原生Approve。

## 合并前的仓库准备

在目标分支的传统branch protection中登记必需CI，启用Require branches to be up to date（strict），至少要求1个有效审核，并启用新提交撤销旧批准。分支保护由仓库负责人配置，RepoPilot不代改；启用合并前只读核对。没有CI、无法读取保护或只有ruleset的配置，当前合并入口会停止。

然后按[受控合并](MERGING.md)为单个run启用。冲突修复更新了PR时，需要你对新提交重新Approve。配置机器人身份不代表授权合并已有PR。

## 来源与验证

配置依据：[注册App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app)、[权限选择](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app)、[安装身份](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/about-authentication-with-a-github-app)、[安装令牌](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)。JWT签名使用可选PyJWT和cryptography，固定RS256，不自行实现RSA。

本轮验证与真实配置前提见[增量32](VALIDATION-32.md)。离线RSA和模拟HTTP通过不代表已安装真实App或具备目标仓库权限。
