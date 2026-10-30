// Source-checkout launcher. A local override can point to an equivalent verified
// Electron installation when Windows cannot initialize it in node_modules.
const { spawn } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')
const directory = path.resolve(__dirname, '..')
const configPath = path.resolve(directory, '../.local/desktop-launch.json')
if (!process.env.ELECTRON_OVERRIDE_DIST_PATH && fs.existsSync(configPath)) {
  const config = JSON.parse(fs.readFileSync(configPath, 'utf8'))
  if (typeof config.electron_dist === 'string' && path.isAbsolute(config.electron_dist) && fs.existsSync(path.join(config.electron_dist, 'electron.exe'))) {
    process.env.ELECTRON_OVERRIDE_DIST_PATH = config.electron_dist
  }
}
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
// Electron is the interactive window; hiding STARTUPINFO can suppress its first
// ShowWindow call. Only the Node launcher and background Python service are hidden.
const child = spawn(require('electron'), [directory], { cwd: directory, env, stdio: 'inherit', windowsHide: false })
child.on('error', error => { console.error(error.message); process.exitCode = 1 })
child.on('exit', code => { process.exitCode = code || 0 })
