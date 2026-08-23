const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('gnosisGoogle', {
  searchStage: options => ipcRenderer.invoke('google-images:search-stage', options),
});

contextBridge.exposeInMainWorld('gnosisDesktop', {
  downloadFullSize: options => ipcRenderer.invoke('image:download-full-size', options),
});
