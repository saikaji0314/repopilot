const path = require('node:path')
module.exports = {
  appId: 'io.github.saikaji0314.repopilot', productName: 'RepoPilot',
  directories: { output: 'release', buildResources: 'assets' },
  files: ['electron/main.cjs', 'electron/preload.cjs', 'assets/tray.png', 'package.json', '!node_modules/**'],
  extraResources: [
    { from: 'out', to: 'ui' },
    { from: 'release/backend/repopilot-service', to: 'backend' },
    { from: 'release/third-party-licenses', to: 'third-party-licenses' },
    { from: '../LICENSE', to: 'LICENSE' },
  ],
  asar: true, npmRebuild: false,
  ...(process.env.ELECTRON_OVERRIDE_DIST_PATH ? { electronDist: path.resolve(process.env.ELECTRON_OVERRIDE_DIST_PATH) } : {}),
  win: { target: 'nsis', artifactName: 'RepoPilot-Setup-${version}-${arch}.${ext}', icon: 'assets/icon.ico', signExecutable: false },
  nsis: { oneClick: false, perMachine: false, allowToChangeInstallationDirectory: true, createDesktopShortcut: false, createStartMenuShortcut: true, runAfterFinish: true, license: '../LICENSE', deleteAppDataOnUninstall: false },
}
