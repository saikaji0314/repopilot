const { app, BrowserWindow, ipcMain, Menu, Tray, nativeImage, shell, dialog } = require('electron')
const { spawn } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')
const fs = require('node:fs')

const project = path.resolve(__dirname, '../..')
if (process.env.REPOPILOT_DESKTOP_USER_DATA) app.setPath('userData', path.resolve(process.env.REPOPILOT_DESKTOP_USER_DATA))
const packaged = app.isPackaged
const userDirectory = app.getPath('userData')
const token = randomBytes(32).toString('hex')
let window, tray, backend, origin, quitting = false
const hostConfigPath = process.env.REPOPILOT_DESKTOP_HOST_CONFIG || (packaged ? path.join(userDirectory, 'desktop-host.json') : path.join(project, '.local/desktop-host.json'))
function hostConfig() { try { return JSON.parse(fs.readFileSync(hostConfigPath, 'utf8')) } catch { return {} } }
function saveHost(update) { fs.mkdirSync(path.dirname(hostConfigPath), { recursive: true }); const temporary = hostConfigPath + '.writing'; fs.writeFileSync(temporary, JSON.stringify({ ...hostConfig(), ...update }, null, 2)); fs.renameSync(temporary, hostConfigPath) }
let runtime = process.env.REPOPILOT_RUNTIME_DIR || hostConfig().runtime || (packaged ? path.join(process.env.LOCALAPPDATA || path.resolve(app.getPath('appData'), '../Local'), 'RepoPilot/runtime') : path.join(project, '.local/runtime'))
const python = process.env.REPOPILOT_PYTHON || path.join(project, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
if (!app.requestSingleInstanceLock()) app.quit()
else {
  app.on('second-instance', () => show())
  app.on('window-all-closed', () => {})
  app.on('before-quit', () => { quitting = true; if (backend) backend.kill() })
  app.whenReady().then(start).catch(error => { dialog.showErrorBox('RepoPilot 无法启动', error.message); app.quit() })
}
function show() { if (window) { window.show(); if (window.isMinimized()) window.restore(); window.focus() } }
function authorized(event) { return window && event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame && event.senderFrame.url.startsWith(origin + '/') }
async function start() {
  const ui = packaged ? path.join(process.resourcesPath, 'ui') : path.resolve(__dirname, '../out')
  if (!fs.existsSync(path.join(ui, 'index.html'))) throw new Error('请先在 desktop 目录运行 pnpm build。')
  origin = await startBackend(ui)
  window = new BrowserWindow({ width: 1440, height: 960, minWidth: 1060, minHeight: 680,
    frame: false, show: false, backgroundColor: '#ffffff',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true } })
  window.setMenu(null)
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event, url) => { if (!url.startsWith(origin + '/')) event.preventDefault() })
  window.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  ipcMain.handle('desktop:bootstrap', event => { if (!authorized(event)) throw new Error('unauthorized_sender'); return { token, runtime } })
  ipcMain.handle('desktop:preferences', event => { if (!authorized(event)) throw new Error('unauthorized_sender'); return hostConfig().panels || {} })
  ipcMain.handle('desktop:save-preferences', (event, preferences) => {
    if (!authorized(event)) throw new Error('unauthorized_sender')
    if (!preferences || !Number.isFinite(preferences.sidebarWidth) || !Number.isFinite(preferences.agentWidth) || typeof preferences.sidebarHidden !== 'boolean' || typeof preferences.agentHidden !== 'boolean') throw new Error('invalid_preferences')
    saveHost({ panels: { sidebarWidth: Math.min(420, Math.max(180, preferences.sidebarWidth)), agentWidth: Math.min(400, Math.max(180, preferences.agentWidth)), sidebarHidden: preferences.sidebarHidden, agentHidden: preferences.agentHidden } })
  })
  let changingRuntime = false
  ipcMain.handle('desktop:choose-runtime', async event => {
    if (!authorized(event) || changingRuntime) throw new Error('runtime_change_unavailable')
    const selected = await dialog.showOpenDialog(window, { title: '选择 RepoPilot 运行目录', defaultPath: runtime, properties: ['openDirectory', 'createDirectory'] })
    if (selected.canceled || !selected.filePaths[0]) return
    changingRuntime = true; const previous = runtime; const previousOrigin = origin; const oldBackend = backend
    try {
      runtime = path.resolve(selected.filePaths[0]); origin = await startBackend(ui)
      await window.loadURL(origin + '/'); saveHost({ runtime }); oldBackend.kill()
    } catch {
      if (backend !== oldBackend) backend.kill()
      backend = oldBackend; runtime = previous; origin = previousOrigin
      await window.loadURL(origin + '/').catch(() => {})
      throw new Error('runtime_change_failed')
    } finally { changingRuntime = false }
  })
  ipcMain.on('desktop:window', (event, action) => { if (!authorized(event)) return; if (action === 'minimize') window.minimize(); if (action === 'maximize') window.isMaximized() ? window.unmaximize() : window.maximize(); if (action === 'close') window.hide() })
  ipcMain.handle('desktop:external', async (event, url) => {
    if (!authorized(event) || typeof url !== 'string') throw new Error('external_url_denied')
    const parsed = new URL(url)
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error('external_url_denied')
    await shell.openExternal(parsed.href)
  })
  const icon = nativeImage.createFromPath(path.join(__dirname, '../assets/tray.png'))
  tray = new Tray(icon); tray.setToolTip('RepoPilot · 关闭窗口后仍可从托盘打开')
  tray.setContextMenu(Menu.buildFromTemplate([{ label: '打开 RepoPilot', click: show }, { type: 'separator' }, { label: '退出桌面端', click: async () => {
    const answer = await dialog.showMessageBox(window, { type: 'question', title: '退出桌面端', message: '退出桌面端和本机读取服务？', detail: '这不会强杀由 CLI 启动的任务。再次打开后会重新读取持久化记录。', buttons: ['取消', '退出'], defaultId: 0, cancelId: 0 })
    if (answer.response === 1) app.quit()
  } }]))
  tray.on('double-click', show); tray.on('click', show)
  window.on('close', event => { if (!quitting) { event.preventDefault(); window.hide() } })
  window.once('ready-to-show', show)
  await window.loadURL(origin + '/')
}
function startBackend(ui) {
  return new Promise((resolve, reject) => {
    let buffer = '', settled = false
    const executable = packaged ? path.join(process.resourcesPath, 'backend/repopilot-service.exe') : python
    const args = [...(packaged ? [] : ['-m', 'repopilot.desktop']), '--output', runtime, '--ui', ui]
    if (packaged) fs.mkdirSync(userDirectory, { recursive: true })
    backend = spawn(executable, args, {
      cwd: packaged ? userDirectory : project, windowsHide: true, env: { ...process.env, REPOPILOT_DESKTOP_TOKEN: token, PYTHONIOENCODING: 'utf-8' }, stdio: ['ignore', 'pipe', 'pipe'],
    })
    const timer = setTimeout(() => { if (!settled) { settled = true; backend.kill(); reject(new Error('本机服务启动超时，请检查安装完整性或本机 Python 环境。')) } }, packaged ? 60000 : 20000)
    backend.on('error', () => { if (!settled) { settled = true; clearTimeout(timer); reject(new Error('无法启动本机服务，请检查安装完整性或本机 Python 环境。')) } })
    backend.stdout.on('data', chunk => { buffer += chunk.toString('utf8'); const lines = buffer.split('\n'); buffer = lines.pop(); for (const line of lines) {
      let ready; try { ready = JSON.parse(line) } catch { continue }
      if (!settled && ready.desktop_ready && Number.isInteger(ready.port)) {
        settled = true; clearTimeout(timer); const endpoint = 'http://127.0.0.1:' + ready.port
        // The socket is reserved before ready is emitted; wait for ASGI startup too.
        const check = async () => { for (let attempt = 0; attempt < 50; attempt++) { try { if ((await fetch(endpoint + '/health')).ok) return resolve(endpoint) } catch {} await new Promise(r => setTimeout(r, 100)) } reject(new Error('本机服务启动失败。')) }; void check()
      }
    } })
    backend.stderr.on('data', () => {}) // No credentials or runtime output in renderer console.
    backend.on('exit', () => { if (!settled) { settled = true; clearTimeout(timer); reject(new Error('本机服务提前退出，请检查安装完整性或 desktop 可选依赖。')) } })
  })
}
