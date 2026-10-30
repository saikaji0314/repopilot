# RepoPilot v0.2.0rc1 · Windows 桌面预发行

下载 [RepoPilot-Setup-0.2.0-rc.1-x64.exe](https://github.com/saikaji0314/repopilot/releases/download/v0.2.0rc1/RepoPilot-Setup-0.2.0-rc.1-x64.exe)，按向导安装并打开。安装程序内置 Electron、Python 和本机 API 服务，打开应用不需要另装 Python、Node.js 或 pnpm。`SHA256SUMS` 提供文件校验值；本版没有发布到 PyPI。

## 本版内容

- Jenkins 工作台结构与 Primer 样式的执行详情：动作、命令输出、代码差异、检查、审核和本地固定候选批准。
- 导航和 Agent 列表可调宽、折叠及重启记忆；Ctrl+B 切换导航，Ctrl+K 打开搜索。
- App ID、Installation ID、PEM 私钥获取引导与只读 GitHub 连接检查；Windows 当前用户加密保存，不回显凭证。
- 普通 Issue 评论读取及机器人／本人身份发送，持久化投递记录，未知结果先核对，不盲目重复发布。
- 关闭窗口到托盘；运行目录可选择，默认位于当前用户的 `%LOCALAPPDATA%\RepoPilot\runtime`。主进程布局与目录设置保存在 Electron 用户配置目录。

首次使用按 [配置说明](SETUP.md) 配置自己的 GitHub App。没有运行记录时显示空状态；不会自动填充样例。查看已有 CLI 运行时，选择其原运行目录；不自动迁移 SQLite、worktree 或累计账本。

## 验证与限制

验证记录见 [实现与验证](IMPLEMENTATION.md)。Windows 安装包、冻结服务和界面测试与真实 GitHub 写入、真实模型效果分别记录；不能将合成凭证与模拟平台测试当作真实评论发布成功。

本轮覆盖 836 项 Python 回归，分段完成 824 passed／12 skipped；首次旧夹具失败及切换临时目录后的复验分别保留。14 项浏览器用例通过；实际安装版在 Python／Node 不在 PATH 的环境下完成设置、重启恢复、目录切换、托盘、鉴权、差异与隔离本地批准测试。

当前桌面是执行详情预览版：模型 Key 可加密保存和格式校验，尚未通过桌面启动 Agent worker；既有 CLI 仍沿用原环境配置。自动接单调度、创建 Issue、运行中需求变更、正式答复后自动恢复、桌面发布 PR 与原生 Approve、容器实时输出仍未接通。已有候选的 Git 操作需要本机安装 Git，目标仓库执行依赖另行准备。

安装程序暂未进行代码签名，Windows 可能显示未知发布者。本版仅交付 Windows x64；其他平台和独立干净 Windows 虚拟机尚未验收。卸载应用保留用户运行数据和加密设置，更新不会清零历史预算。

本机开发目录曾出现启动／安装未生效的情况，同一校验产物移到 C 盘后安装与原生验证通过，底层原因尚未确定。当前建议使用默认的用户目录安装位置，不将任意盘符或目录视为已验收。

## 从源码构建安装程序

在 Windows、Python 3.11+、Node.js 22.12+、pnpm 环境下：

```powershell
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[desktop,desktop-build]"
cd desktop
pnpm install --frozen-lockfile
pnpm build
cd ..
.\.venv\Scripts\python.exe scripts/build_desktop_service.py
cd desktop
pnpm dist:win
```

生成文件位于 `desktop/release/`，不是 wheel。发布前核对安装、冷启动、退出／重开、设置加密与运行目录切换；安装产物不应包含 `.env`、私钥、`.local/`、真实模型会话或业务运行数据。构建工具版本固定在锁文件与约束文件中，第三方许可随安装包提供。图标需要重新生成时，在 desktop 执行 `node scripts/build-icon.cjs`，可通过 `REPOPILOT_BROWSER` 指定本机 Chromium。
