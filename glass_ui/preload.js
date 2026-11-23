const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('glassAPI', {
  onHotkey: (cb) => ipcRenderer.on('hotkey', (_, data) => cb(data)),
  setCollapsed: (collapsed) => ipcRenderer.send('set-collapsed', collapsed),
  setMiniPill: (mini) => ipcRenderer.send('set-mini-pill', mini),
  fitHeight: (h) => ipcRenderer.send('fit-height', h),
  setOpacity: (alpha) => ipcRenderer.send('set-opacity', alpha),
  hideWindow: () => ipcRenderer.send('hide-window'),
  closeApp: () => ipcRenderer.send('close-app'),
  pickResume: () => ipcRenderer.invoke('pick-resume'),
  pickDocs: () => ipcRenderer.invoke('pick-docs'),
});
