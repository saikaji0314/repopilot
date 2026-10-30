# RepoPilot v0.2.0rc1 · Windows 桌面预发行

**[下载 Windows x64 安装程序（.exe）](https://github.com/saikaji0314/repopilot/releases/download/v0.2.0rc1/RepoPilot-Setup-0.2.0-rc.1-x64.exe)** · [Release 与校验文件](https://github.com/saikaji0314/repopilot/releases/tag/v0.2.0rc1)

安装程序内置 Electron、Python 和本机服务，打开桌面无需另外安装 Python、Node.js 或 pnpm。

## 本版功能

- **执行详情**：查看 Agent 工具动作、命令输出、代码差异、检查与审核，核对固定候选并记录本地批准。
- **可调分栏**：导航与 Agent 列表支持调宽、折叠和重启记忆；Ctrl+B 切换导航，Ctrl+K 搜索。
- **机器人连接**：引导获取 App ID、Installation ID 和 PEM 私钥，使用 Windows 当前用户加密保存凭证。
- **Issue 讨论**：选择机器人或本人身份发表评论；发送结果不确定时先核对，避免重复发布。
- **托盘与运行目录**：关闭窗口后可从托盘重开，支持选择已有运行目录。

## 开始使用

安装并打开应用，按[配置说明](SETUP.md)连接自己的 GitHub App。查看已有 CLI 运行时，选择原运行目录；原有运行记录、工作区和累计预算继续保留。

当前为执行详情预览版。模型配置支持保存和格式校验；桌面任务启动、自动接单、创建 Issue、运行中需求变更和桌面 PR 发布等功能将在后续版本完善。已有 CLI 任务继续沿用原环境配置。

适用平台：Windows x64。安装程序暂未代码签名，Windows 可能显示未知发布者。卸载应用会保留用户运行数据和加密设置。

开发者构建流程见[桌面工程](../../desktop/README.md)。
