# RepoPilot 桌面端

按已确认的执行详情设计实现的开发版本。当前包含执行记录、Agent 动作与输出、改动、检查与审核、本地人工决定和托盘外壳；完整桌面产品的实现范围见 [实现与验证](../docs/desktop/IMPLEMENTATION.md)。

普通 Windows 用户从 [v0.2.0rc1](https://github.com/saikaji0314/repopilot/releases/tag/v0.2.0rc1) 下载 `.exe` 安装程序，见[安装与发行说明](../docs/desktop/RELEASE-0.2.0rc1.md)。下方是开发者源码启动流程。

## 准备

需要 Node.js 22.12+、pnpm、项目 Python 3.11+ 虚拟环境。首次安装需要下载依赖与 Electron；不包含模型或 GitHub 凭证。

在仓库根目录安装 Python 可选依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[desktop]"
```

在本目录安装并构建界面：

```powershell
pnpm install --frozen-lockfile
pnpm build
pnpm start
```

默认读取仓库的 `.local/runtime`。可以在启动前通过 `REPOPILOT_RUNTIME_DIR` 指定其他已有运行目录，通过 `REPOPILOT_PYTHON` 指定 Python 可执行文件。空目录显示空状态，不会自动填入示例或开始模型调用。当前启动方式面向源码工作区，尚无安装包。

已安装依赖后，也可从仓库根目录使用 `scripts/Start-Desktop.ps1 -Build`，无需全局 pnpm 来构建和启动。

## 设计预览

```powershell
pnpm dev
```

打开终端显示的本机地址，添加 `?demo=running`、`?demo=waiting` 或 `?demo=change`。预览只使用标明的示例数据；点击暂停或批准不会触碰真实运行。Vite 页面不持有本机服务会话，真实记录通过 Electron 打开。

## 验证

导航与 Agent 列表支持拖动／键盘调宽、折叠及重启记忆。设置现已接上本机加密、GitHub App 引导／连接检查、模型配置格式校验和运行目录选择；Issue 讨论支持普通评论读取与显式发送。详细步骤及当前执行边界见 [桌面配置](../docs/desktop/SETUP.md)。

```powershell
pnpm build
pnpm test
pnpm test:native-settings
```

浏览器测试默认使用 Windows 已安装的 Edge；其他环境通过 `REPOPILOT_BROWSER` 指定 Chromium 可执行文件。真实服务测试使用独立的临时运行目录，不访问原运行或平台。原生测试 `pnpm test:native` 另需 `REPOPILOT_RUNTIME_DIR` 指向已生成的确定性测试候选；该测试会批准此隔离候选，不应指向真实业务运行。

`pnpm test:native-settings` 自动创建独立的运行目录、主进程配置及用户 profile，使用合成凭证验证真实加密、重启恢复和目录切换，不发 GitHub 评论或模型请求。测试时可通过 `REPOPILOT_DESKTOP_HOST_CONFIG`、`REPOPILOT_DESKTOP_USER_DATA` 分别隔离主进程配置与 Electron 用户数据；正常启动无需设置它们。

本机 `node_modules` 目录中的 Electron 45.1.0 曾在 `--version` 阶段以 `0x80000003` 退出。同一份已校验的运行时复制到另一处已授权目录后可以启动，原生关闭隐藏、重开、API 鉴权、实际 diff 和隔离候选批准已通过。没有关闭 Chromium sandbox。

若其他开发机有同类问题，可通过 Electron 的 `ELECTRON_OVERRIDE_DIST_PATH` 指向同版本的正常运行时目录。此源码启动器也支持被忽略的 `.local/desktop-launch.json`，格式为 `{ "electron_dist": "绝对运行时目录" }`；该目录必须包含 `electron.exe`。本机配置已保存，路径不进入公共代码。尚未确认导致原目录失败的完整根因，不自动修改系统或父目录权限。

## 构建 Windows 安装程序

在 Windows 仓库根目录安装构建依赖，然后构建界面、冻结服务和安装程序：

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

生成文件位于 `desktop/release/`。图标重新生成入口为 `node scripts/build-icon.cjs`，截图入口为 `node scripts/capture-preview.cjs`；均可通过 `REPOPILOT_BROWSER` 指定本机 Chromium。

## 代码入口

- `src/App.tsx`、`src/panels.tsx`：执行页、可调分栏、证据与人工操作。
- `src/Settings.tsx`、`src/Discussion.tsx`：连接向导、凭证管理、普通评论与投递核对。
- `src/events.ts`：动作关联，拒绝猜测无法唯一匹配的旧并行结果。
- `src/api.ts`：服务会话、事件补读、重连与失联提示。
- `electron/main.cjs`、`electron/preload.cjs`：窗口、托盘、受限 IPC 与 Python 读取服务。
- `../repopilot/desktop/`：状态投影、回环接口与安全检查点暂停请求。

图标、字体与组件来源见 [第三方说明](THIRD_PARTY.md)。
