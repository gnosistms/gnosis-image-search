const { contextBridge, ipcRenderer } = require('electron');

window.addEventListener('contextmenu', event => {
  const image = event.target instanceof HTMLImageElement ? event.target : null;
  if (!image) return;
  const source = image.dataset.imageUrl || image.currentSrc || image.src;
  if (!source) return;
  let imageUrl;
  try {
    imageUrl = new URL(source, document.baseURI).href;
  } catch (_error) {
    return;
  }
  event.preventDefault();
  ipcRenderer.send('image:show-context-menu', {
    imageUrl,
    x: Math.round(event.clientX),
    y: Math.round(event.clientY),
  });
}, true);

contextBridge.exposeInMainWorld('gnosisGoogle', {
  searchStage: options => ipcRenderer.invoke('google-images:search-stage', options),
});

contextBridge.exposeInMainWorld('gnosisDesktop', {
  downloadFullSize: options => ipcRenderer.invoke('image:download-full-size', options),
});
