const { contextBridge, ipcRenderer } = require('electron')
contextBridge.exposeInMainWorld('repopilot', Object.freeze({
  bootstrap: () => ipcRenderer.invoke('desktop:bootstrap'),
  window: action => ipcRenderer.send('desktop:window', action),
  openExternal: url => ipcRenderer.invoke('desktop:external', url),
  getPreferences: () => ipcRenderer.invoke('desktop:preferences'),
  savePreferences: preferences => ipcRenderer.invoke('desktop:save-preferences', preferences),
  chooseRuntime: () => ipcRenderer.invoke('desktop:choose-runtime'),
}))
