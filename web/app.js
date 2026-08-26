const form = document.querySelector('#search-form');
const queryInput = document.querySelector('#query');
const searchButton = form.querySelector('.search-button');
const heroForm = document.querySelector('#hero-search-form');
const heroQueryInput = document.querySelector('#hero-query');
const heroSearchButton = heroForm.querySelector('button');
const gallery = document.querySelector('#gallery');
const emptyState = document.querySelector('#empty-state');
const statusLine = document.querySelector('#search-status');
const sourcePanel = document.querySelector('#source-panel');
const sourcePanelDragExclusion = document.querySelector('#source-panel-drag-exclusion');
const sourceOptions = document.querySelector('#source-options');
const toggleSources = document.querySelector('#toggle-sources');
const heroToggleSources = document.querySelector('#hero-toggle-sources');
const heroSourceCount = document.querySelector('#hero-source-count');
const tileTemplate = document.querySelector('#tile-template');
const detailPanel = document.querySelector('#detail-panel');
const detailImageLink = document.querySelector('#detail-image-link');
const detailImage = document.querySelector('#detail-image');
const detailImageSpinner = document.querySelector('#detail-image-spinner');
const detailDimensionsOverlay = document.querySelector('#detail-dimensions-overlay');
const detailDownloadOverlay = document.querySelector('#detail-download-overlay');
const downloadFullImage = document.querySelector('#download-full-image');
const copyFullSizeImageUrl = document.querySelector('#copy-full-size-image-url');
const copyImageUrlStatus = document.querySelector('#copy-image-url-status');
const detailTitle = document.querySelector('#detail-title');
const detailSource = document.querySelector('#detail-source');
const detailArtistRow = document.querySelector('#detail-artist-row');
const detailArtist = document.querySelector('#detail-artist');
const detailDateRow = document.querySelector('#detail-date-row');
const detailDate = document.querySelector('#detail-date');
const detailDescription = document.querySelector('#detail-description');
const detailLicense = document.querySelector('#detail-license');
const detailSize = document.querySelector('#detail-size');
const similarGrid = document.querySelector('#similar-grid');
const similarStatus = document.querySelector('#similar-status');
const alternateSection = document.querySelector('#alternate-section');
const alternateGrid = document.querySelector('#alternate-grid');
const alternateStatus = document.querySelector('#alternate-status');
const developerWarning = document.querySelector('#developer-warning');
const closeDeveloperWarning = document.querySelector('#close-developer-warning');

const launchParameters = new URLSearchParams(location.search);
if (launchParameters.has('desktop')) {
  document.body.classList.add('desktop-app');
  const desktopPlatform = launchParameters.get('platform');
  if (desktopPlatform) document.body.classList.add(`desktop-${desktopPlatform}`);
}

let sourceConfig = [];
let currentResults = [];
let currentSession = '';
let currentRevision = -1;
let currentSearchSequence = 0;
let selectedItemId = '';
let detailImageRequest = 0;
const galleryTiles = new Map();
const panelItems = new Map();
const searchControllers = new Set();
const shownSourceAlerts = new Set();
let copyFeedbackTimer;

function clearCopyFeedback() {
  clearTimeout(copyFeedbackTimer);
  copyFeedbackTimer = undefined;
  copyFullSizeImageUrl.classList.remove('is-copied', 'is-copy-error');
  copyImageUrlStatus.textContent = '';
}

function showCopyFeedback({ message, tooltip, className, duration = 1500 }) {
  clearCopyFeedback();
  copyFullSizeImageUrl.classList.add(className);
  copyFullSizeImageUrl.dataset.tooltip = tooltip;
  copyImageUrlStatus.textContent = message;
  copyFeedbackTimer = setTimeout(() => {
    clearCopyFeedback();
    const item = findItem(copyFullSizeImageUrl.dataset.itemId);
    const state = GnosisFullSizeImageUrl.controlState(item);
    copyFullSizeImageUrl.dataset.tooltip = state.tooltip;
  }, duration);
}

function selectedSources() {
  return [...sourceOptions.querySelectorAll('input:checked')].map(input => input.value);
}

function updateSourceCount() {
  const count = selectedSources().length;
  toggleSources.textContent = `Collections · ${count}`;
  heroSourceCount.textContent = `· ${count} selected`;
}

function updateSearchControl(input, button) {
  const hasValue = input.value.length > 0;
  button.type = hasValue ? 'button' : 'submit';
  button.classList.toggle('is-clear', hasValue);
  button.textContent = hasValue ? '×' : '⌕';
  button.setAttribute('aria-label', hasValue ? 'Clear search' : 'Search');
}

function setupSearchControl(formElement, input, button) {
  input.addEventListener('input', () => updateSearchControl(input, button));
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && input.value.trim()) {
      event.preventDefault();
      formElement.requestSubmit();
    }
  });
  button.addEventListener('click', event => {
    if (!button.classList.contains('is-clear')) return;
    event.preventDefault();
    input.value = '';
    updateSearchControl(input, button);
    input.focus();
  });
  updateSearchControl(input, button);
}

function syncSourcePanelDragExclusion() {
  if (sourcePanel.hidden || emptyState.hidden) return;
  const rect = sourcePanel.getBoundingClientRect();
  Object.assign(sourcePanelDragExclusion.style, {
    top: `${rect.top}px`,
    left: `${rect.left}px`,
    width: `${rect.width}px`,
    height: `${rect.height}px`,
  });
}

function setSourcePanelOpen(open) {
  sourcePanel.hidden = !open;
  sourcePanelDragExclusion.hidden = !open;
  if (open) syncSourcePanelDragExclusion();
  toggleSources.setAttribute('aria-expanded', String(open));
  heroToggleSources.setAttribute('aria-expanded', String(open));
}

function setPlainStatus(text) {
  statusLine.removeAttribute('aria-label');
  statusLine.textContent = text;
}

function setSearchBusy(busy) {
  statusLine.classList.toggle('is-searching', busy);
  statusLine.setAttribute('aria-busy', String(busy));
}

function showSourceAlerts(alerts = {}) {
  for (const alert of Object.values(alerts)) {
    if (alert.code !== 'europeana_key_access' || shownSourceAlerts.has(alert.code)) continue;
    shownSourceAlerts.add(alert.code);
    if (typeof developerWarning.showModal === 'function') developerWarning.showModal();
    else developerWarning.setAttribute('open', '');
  }
}

async function getJson(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.text();
  let data = {};
  try {
    data = body ? JSON.parse(body) : {};
  } catch (error) {
    if (response.ok) throw error;
  }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function collectionLabel(source) {
  return sourceConfig.find(item => item.id === source)?.label || source;
}

function cancelPreviousSearch() {
  for (const controller of searchControllers) controller.abort();
  searchControllers.clear();
  if (currentSession) {
    fetch(`/api/search/cancel?session=${encodeURIComponent(currentSession)}`, {
      keepalive: true,
    }).catch(() => {});
  }
}

async function loadSources() {
  const data = await getJson('/api/sources');
  sourceConfig = data.sources;
  sourceOptions.replaceChildren();
  for (const source of sourceConfig) {
    const label = document.createElement('label');
    label.className = 'source-choice';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = source.id;
    input.checked = source.default;
    const name = document.createElement('span');
    name.textContent = source.label;
    label.append(input, name);
    sourceOptions.append(label);
  }
  updateSourceCount();
}

function imageRatio(item) {
  const ratio = item.width && item.height ? item.width / item.height : 1.38;
  return Math.max(.62, Math.min(ratio, 2.8));
}

function imageFileType(item) {
  const supported = new Map([
    ['jpeg', 'JPG'], ['jpg', 'JPG'], ['png', 'PNG'], ['webp', 'WEBP'],
    ['gif', 'GIF'], ['tif', 'TIFF'], ['tiff', 'TIFF'], ['jp2', 'JP2'],
  ]);
  const values = [
    item.file_type, item.mime_type, item.mime, item.format,
    item.full_resolution_url, item.image_url, item.thumb_url,
  ].filter(Boolean);
  for (const value of values) {
    const text = String(value).toLowerCase();
    for (const [token, label] of supported) {
      if (text.includes(`image/${token}`)) return label;
    }
    try {
      const url = new URL(value, document.baseURI);
      const queryFormat = url.searchParams.get('fm')
        || url.searchParams.get('format')
        || url.searchParams.get('ext');
      if (queryFormat && supported.has(queryFormat.toLowerCase())) {
        return supported.get(queryFormat.toLowerCase());
      }
      const extension = url.pathname.match(/\.([a-z0-9]{2,5})$/i)?.[1]?.toLowerCase();
      if (extension && supported.has(extension)) return supported.get(extension);
    } catch (_error) {
      const extension = text.match(/\.([a-z0-9]{2,5})(?:$|[?#])/i)?.[1]?.toLowerCase();
      if (extension && supported.has(extension)) return supported.get(extension);
    }
  }
  return 'IMAGE';
}

function imageCandidates(item, { detail = false } = {}) {
  const cachedDetail = detail
    && item.download_url
    && currentSession
    ? `/api/image/detail?session=${encodeURIComponent(currentSession)}&id=${encodeURIComponent(item.id)}`
    : '';
  const normal = detail
    ? [cachedDetail, item.image_url, item.thumb_url]
    : [item.thumb_url, item.image_url];
  const aicProxy = item.source === 'aic'
    && ['huggingface', 'wayback'].includes(item.image_delivery)
    && currentSession
    ? `/api/image/aic?session=${encodeURIComponent(currentSession)}&id=${encodeURIComponent(item.id)}`
    : '';
  const harvardProxy = item.source === 'harvard' && currentSession
    ? `/api/image/harvard?session=${encodeURIComponent(currentSession)}&id=${encodeURIComponent(item.id)}${detail ? '&detail=1' : ''}`
    : '';
  // Safari receives mirror previews through our same-origin endpoint with an
  // explicit image MIME type. The signed source URL remains a fallback if the
  // bounded proxy cannot retrieve a preview; tiny API LQIPs are intentionally
  // excluded because they otherwise look like permanently blurred results.
  const aic = detail
    ? [cachedDetail, aicProxy, item.image_url, item.thumb_url]
    : [aicProxy, item.thumb_url, item.image_url];
  const harvard = detail
    ? [cachedDetail, harvardProxy, item.image_url, item.thumb_url]
    : [harvardProxy, ...normal];
  const selected = item.source === 'aic' ? aic
    : item.source === 'harvard' ? harvard
    : normal;
  return [...new Set(selected.filter(Boolean))];
}

function applyImageSources(image, item, { detail = false } = {}) {
  const candidates = imageCandidates(item, { detail });
  let index = 0;
  image.loading = detail ? 'eager' : 'lazy';
  image.decoding = 'async';
  if (item.source === 'aic') image.referrerPolicy = 'no-referrer';
  image.onerror = () => {
    index += 1;
    if (index < candidates.length) image.src = candidates[index];
    else {
      image.removeAttribute('src');
      image.closest('.image-tile')?.classList.add('image-unavailable');
    }
  };
  if (candidates.length) image.src = candidates[0];
  else image.closest('.image-tile')?.classList.add('image-unavailable');
}

function setDetailImageLoading(loading) {
  detailImageLink.classList.toggle('is-loading', loading);
  detailImageLink.setAttribute('aria-busy', String(loading));
  detailImageSpinner.hidden = !loading;
}

function showDetailImage(item, previewImage) {
  const request = ++detailImageRequest;
  const previewUrl = previewImage?.currentSrc || previewImage?.src || '';
  const absoluteUrl = url => new URL(url, document.baseURI).href;
  const ratio = item.width && item.height
    ? item.width / item.height
    : previewImage?.naturalWidth && previewImage?.naturalHeight
      ? previewImage.naturalWidth / previewImage.naturalHeight
      : 1.38;

  detailImageLink.style.setProperty('--detail-ratio', String(ratio));

  detailImage.alt = item.title;
  detailImage.onerror = null;
  detailImage.loading = 'eager';
  detailImage.decoding = 'async';
  if (item.source === 'aic') detailImage.referrerPolicy = 'no-referrer';
  else detailImage.removeAttribute('referrerpolicy');

  // Keep the already-rendered search result visible while the larger image is
  // fetched offscreen. Assigning the larger URL directly makes the panel look
  // empty until that request completes.
  if (previewUrl) detailImage.src = previewUrl;
  else detailImage.removeAttribute('src');

  const candidates = imageCandidates(item, { detail: true })
    .filter(url => !previewUrl || absoluteUrl(url) !== absoluteUrl(previewUrl));
  setDetailImageLoading(candidates.length > 0);
  if (!candidates.length) {
    return;
  }

  let index = 0;
  const loadNext = () => {
    if (request !== detailImageRequest || index >= candidates.length) return;
    const loader = new Image();
    loader.decoding = 'async';
    if (item.source === 'aic') loader.referrerPolicy = 'no-referrer';
    loader.onload = () => {
      if (request === detailImageRequest && selectedItemId === item.id) {
        detailImage.src = loader.currentSrc || loader.src;
        setDetailImageLoading(false);
      }
    };
    loader.onerror = () => {
      index += 1;
      if (index < candidates.length) loadNext();
      else if (request === detailImageRequest && selectedItemId === item.id) {
        setDetailImageLoading(false);
      }
    };
    loader.src = candidates[index];
  };
  loadNext();
}

function updateTile(tile, item) {
  tile.dataset.id = item.id;
  tile.style.setProperty('--ratio', imageRatio(item));
  tile.classList.toggle('selected', item.id === selectedItemId);
  tile.querySelector('strong').textContent = item.title;
  tile.querySelector('small').textContent = typeof item.pamela_score === 'number'
    ? `${item.source_label} · P ${item.pamela_score.toFixed(2)}`
    : item.source_label;
  tile.title = `${item.title} — ${item.source_label}`;
}

function createTile(item) {
  const tile = tileTemplate.content.firstElementChild.cloneNode(true);
  const image = tile.querySelector('img');
  image.alt = item.title;
  applyImageSources(image, item);
  tile.addEventListener('click', () => openDetails(tile.dataset.id, image));
  updateTile(tile, item);
  return tile;
}

function setGallerySearching(searching) {
  const existing = gallery.querySelector('.gallery-searching');
  if (!searching) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const indicator = document.createElement('div');
  indicator.className = 'gallery-searching';
  indicator.setAttribute('role', 'status');
  const spinner = document.createElement('span');
  spinner.className = 'gallery-searching-spinner';
  spinner.setAttribute('aria-hidden', 'true');
  const label = document.createElement('span');
  label.textContent = 'Searching…';
  indicator.append(spinner, label);
  gallery.append(indicator);
}

function renderGallery(items) {
  currentResults = items;
  if (items.length) setGallerySearching(false);
  const retained = new Set(items.map(item => item.id));
  for (const [id, tile] of galleryTiles) {
    if (!retained.has(id)) {
      tile.remove();
      galleryTiles.delete(id);
    }
  }
  for (const item of items) {
    let tile = galleryTiles.get(item.id);
    if (!tile) {
      tile = createTile(item);
      galleryTiles.set(item.id, tile);
    } else {
      updateTile(tile, item);
    }
    // append() moves an existing node without reconstructing or reloading it.
    gallery.append(tile);
  }
}

function applySnapshot(snapshot, sequence) {
  if (sequence !== currentSearchSequence || snapshot.revision < currentRevision) return;
  currentRevision = snapshot.revision;
  showSourceAlerts(snapshot.source_alerts);
  renderGallery(snapshot.results);
  const unavailable = Object.keys(snapshot.source_errors);
  const errors = unavailable.length;
  const policies = Object.values(snapshot.source_policy || {});
  const active = policies.filter(policy => policy.continue).length;
  const searched = policies.length - active;
  const resultText = `${snapshot.results.length} ranked images`;
  const progressText = policies.length
    ? `${searched} of ${policies.length} collections searched`
    : '';
  const errorText = errors
    ? `${errors} unavailable (${unavailable.map(collectionLabel).join(', ')})`
    : '';
  const resultStatus = document.createElement('span');
  resultStatus.className = 'status-results';
  resultStatus.textContent = resultText;
  statusLine.replaceChildren(resultStatus);
  if (progressText) {
    const progressStatus = document.createElement('span');
    progressStatus.className = 'status-progress';
    progressStatus.textContent = progressText;
    statusLine.append(progressStatus);
  }
  if (errorText) {
    const errorStatus = document.createElement('span');
    errorStatus.className = 'status-errors';
    errorStatus.textContent = errorText;
    statusLine.append(errorStatus);
  }
  statusLine.setAttribute('aria-label', [resultText, progressText, errorText].filter(Boolean).join(' · '));
}

async function streamSearchRound(sequence, sessionId) {
  if (sequence !== currentSearchSequence) return null;
  const controller = new AbortController();
  searchControllers.add(controller);
  let latestSnapshot = null;
  try {
    const response = await fetch(
      `/api/search/stream?session=${encodeURIComponent(sessionId)}`,
      {signal: controller.signal},
    );
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(body.error || `Request failed (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffered = '';
    while (true) {
      const {value, done} = await reader.read();
      buffered += decoder.decode(value || new Uint8Array(), {stream: !done});
      const lines = buffered.split('\n');
      buffered = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.snapshot) {
          latestSnapshot = event.snapshot;
          applySnapshot(event.snapshot, sequence);
        }
      }
      if (done) break;
    }
    if (buffered.trim()) {
      const event = JSON.parse(buffered);
      if (event.snapshot) {
        latestSnapshot = event.snapshot;
        applySnapshot(event.snapshot, sequence);
      }
    }
    return latestSnapshot;
  } finally {
    searchControllers.delete(controller);
  }
}

async function runSearch(query) {
  const selected = selectedSources();
  if (!selected.length) {
    setSourcePanelOpen(true);
    heroSourceCount.textContent = '· select at least one';
    setPlainStatus('Select at least one collection.');
    return;
  }
  queryInput.value = query;
  heroQueryInput.value = query;
  updateSearchControl(queryInput, searchButton);
  updateSearchControl(heroQueryInput, heroSearchButton);
  document.body.classList.add('search-active');
  window.gnosisDesktop?.setTitleBarTheme?.('search');
  setSourcePanelOpen(false);
  const sequence = ++currentSearchSequence;
  cancelPreviousSearch();
  currentResults = [];
  currentSession = '';
  currentRevision = -1;
  selectedItemId = '';
  panelItems.clear();
  closeDetails();
  gallery.replaceChildren();
  galleryTiles.clear();
  setGallerySearching(true);
  emptyState.hidden = true;
  setSearchBusy(true);
  setPlainStatus(`Searching ${selected.length} collections…`);

  try {
    const start = await getJson(
      `/api/search/start?q=${encodeURIComponent(query)}&sources=${encodeURIComponent(selected.join(','))}`
    );
    if (sequence !== currentSearchSequence) return;
    currentSession = start.session_id;
    const sessionId = start.session_id;
    applySnapshot(start, sequence);
    let hasActiveSources = true;
    while (hasActiveSources && sequence === currentSearchSequence) {
      const snapshot = await streamSearchRound(sequence, sessionId);
      if (!snapshot || sequence !== currentSearchSequence) return;
      hasActiveSources = Object.values(snapshot.source_policy || {})
        .some(policy => policy.continue);
    }
    if (sequence === currentSearchSequence && currentResults.length === 0) {
      setGallerySearching(false);
      gallery.innerHTML = '<p class="notice">No matching images found.</p>';
    }
  } catch (error) {
    if (sequence === currentSearchSequence) {
      setGallerySearching(false);
      setPlainStatus(error.message);
    }
  } finally {
    if (sequence === currentSearchSequence) setSearchBusy(false);
  }
}

function findItem(id) {
  return currentResults.find(item => item.id === id) || panelItems.get(id);
}

async function openDetails(id, previewImage) {
  const item = findItem(id);
  if (!item) return;
  clearCopyFeedback();
  selectedItemId = id;
  gallery.querySelectorAll('.image-tile').forEach(tile =>
    tile.classList.toggle('selected', tile.dataset.id === id));
  const galleryPreview = galleryTiles.get(id)?.querySelector('img');
  showDetailImage(item, previewImage || galleryPreview);
  detailTitle.textContent = item.title;
  detailSource.textContent = item.source_label;
  detailArtist.textContent = item.artist || '';
  detailArtistRow.hidden = !item.artist;
  detailDate.textContent = item.date || '';
  detailDateRow.hidden = !item.date;
  detailDescription.textContent = item.description || 'No additional description was supplied by this collection.';
  detailLicense.textContent = item.license;
  detailSize.textContent = '';
  detailImageLink.href = item.preview_click_url || item.page_url || item.image_url;
  detailImageLink.setAttribute('aria-label', 'Open image website');
  detailDownloadOverlay.hidden = true;
  downloadFullImage.disabled = false;
  downloadFullImage.hidden = !item.download_url;
  downloadFullImage.dataset.itemId = item.download_url ? item.id : '';
  const copyUrlState = GnosisFullSizeImageUrl.controlState(item);
  copyFullSizeImageUrl.disabled = copyUrlState.disabled;
  copyFullSizeImageUrl.dataset.itemId = copyUrlState.disabled ? '' : item.id;
  copyFullSizeImageUrl.dataset.tooltip = copyUrlState.tooltip;
  copyFullSizeImageUrl.setAttribute('aria-label', copyUrlState.tooltip);
  const imageType = imageFileType(item);
  detailDimensionsOverlay.hidden = !(item.width && item.height) && imageType === 'IMAGE';
  detailDimensionsOverlay.textContent = [
    item.width && item.height ? `${item.width} × ${item.height}` : '',
    imageType,
  ].filter(Boolean).join(' · ');
  detailPanel.hidden = false;
  document.body.classList.add('panel-open');
  similarGrid.replaceChildren();
  alternateGrid.replaceChildren();
  alternateSection.hidden = true;
  similarStatus.textContent = 'Comparing images in this search…';
  await loadSimilar(id);
}

function suggestedImageFilename(item) {
  let extension = '';
  try {
    extension = new URL(item.download_url).pathname.match(/\.([a-z0-9]{2,5})$/i)?.[1] || '';
  } catch (_error) {}
  if (!extension) {
    const type = imageFileType(item).toLowerCase();
    extension = type === 'image' ? 'jpg' : type === 'tiff' ? 'tif' : type;
  }
  const stem = (item.title || 'image')
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 120) || 'image';
  return `${stem}.${extension}`;
}

downloadFullImage.addEventListener('click', async () => {
  const item = findItem(downloadFullImage.dataset.itemId);
  if (!item?.download_url) return;
  const filename = suggestedImageFilename(item);
  if (window.gnosisDesktop?.downloadFullSize) {
    detailDownloadOverlay.hidden = false;
    downloadFullImage.disabled = true;
    try {
      const url = item.source === 'harvard' && currentSession
        ? new URL(`/api/image/detail?session=${encodeURIComponent(currentSession)}&id=${encodeURIComponent(item.id)}`, location.href).href
        : item.download_url;
      await window.gnosisDesktop.downloadFullSize({ url, filename });
    } catch (error) {
      window.alert(error.message || 'The full-sized image could not be downloaded.');
    } finally {
      if (selectedItemId === item.id) {
        detailDownloadOverlay.hidden = true;
        downloadFullImage.disabled = false;
      }
    }
    return;
  }
  const link = document.createElement('a');
  link.href = item.download_url;
  link.download = filename;
  link.rel = 'noopener noreferrer';
  link.click();
});

copyFullSizeImageUrl.addEventListener('click', async () => {
  const item = findItem(copyFullSizeImageUrl.dataset.itemId);
  const url = GnosisFullSizeImageUrl.fullSizeImageUrl(item);
  if (!url) return;
  try {
    if (window.gnosisDesktop?.copyFullSizeImageUrl) {
      await window.gnosisDesktop.copyFullSizeImageUrl({ url });
    } else {
      await navigator.clipboard.writeText(url);
    }
    if (copyFullSizeImageUrl.dataset.itemId === item.id) {
      showCopyFeedback({
        message: 'Image URL copied to clipboard.',
        tooltip: 'Copied!',
        className: 'is-copied',
      });
    }
  } catch (_error) {
    if (copyFullSizeImageUrl.dataset.itemId === item.id) {
      showCopyFeedback({
        message: 'The image URL could not be copied.',
        tooltip: "Couldn't copy URL",
        className: 'is-copy-error',
        duration: 2500,
      });
    }
  }
});

async function loadSimilar(id) {
  try {
    const data = await getJson(
      `/api/similar?session=${encodeURIComponent(currentSession)}&id=${encodeURIComponent(id)}&limit=12`
    );
    if (id !== selectedItemId) return;
    for (const item of [...(data.alternates || []), ...data.results]) {
      panelItems.set(item.id, item);
    }
    alternateGrid.replaceChildren();
    for (const item of data.alternates || []) {
      const button = document.createElement('button');
      button.className = 'similar-tile version-tile';
      button.type = 'button';
      button.title = `${item.title} — ${item.source_label}`;
      const image = document.createElement('img');
      image.alt = item.title;
      applyImageSources(image, item);
      const label = document.createElement('span');
      const dimensions = item.width && item.height ? `${item.width} × ${item.height}` : 'Size unknown';
      label.textContent = `${dimensions} · ${item.source_label}`;
      button.append(image, label);
      button.addEventListener('click', () => openDetails(item.id, image));
      alternateGrid.append(button);
    }
    alternateSection.hidden = !(data.alternates || []).length;
    alternateStatus.textContent = (data.alternates || []).length === 1
      ? '1 alternate scan or resolution'
      : `${(data.alternates || []).length} alternate scans or resolutions`;
    similarGrid.replaceChildren();
    for (const item of data.results) {
      const button = document.createElement('button');
      button.className = 'similar-tile';
      button.type = 'button';
      button.title = item.title;
      const image = document.createElement('img');
      image.alt = item.title;
      applyImageSources(image, item);
      const score = document.createElement('span');
      score.textContent = `${Math.round(item.similarity * 100)}%`;
      button.append(image, score);
      button.addEventListener('click', () => openDetails(item.id, image));
      similarGrid.append(button);
    }
    similarStatus.textContent = data.results.length
      ? 'Compared with distinct images in the current search'
      : 'No other images are available to compare.';
  } catch (error) {
    if (id === selectedItemId) similarStatus.textContent = `Similarity unavailable: ${error.message}`;
  }
}

function closeDetails() {
  selectedItemId = '';
  detailImageRequest += 1;
  setDetailImageLoading(false);
  detailPanel.hidden = true;
  document.body.classList.remove('panel-open');
  gallery.querySelectorAll('.selected').forEach(tile => tile.classList.remove('selected'));
}

setupSearchControl(form, queryInput, searchButton);
setupSearchControl(heroForm, heroQueryInput, heroSearchButton);

form.addEventListener('submit', event => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (query) runSearch(query);
});

heroForm.addEventListener('submit', event => {
  event.preventDefault();
  const query = heroQueryInput.value.trim();
  if (query) runSearch(query);
});

toggleSources.addEventListener('click', () => {
  const open = sourcePanel.hidden;
  setSourcePanelOpen(open);
});
heroToggleSources.addEventListener('click', () => setSourcePanelOpen(sourcePanel.hidden));
document.addEventListener('pointerdown', event => {
  if (!sourcePanel.hidden && !sourcePanel.contains(event.target)
      && !toggleSources.contains(event.target)
      && !heroToggleSources.contains(event.target)) {
    setSourcePanelOpen(false);
  }
});
document.querySelector('#select-all').addEventListener('click', () => {
  sourceOptions.querySelectorAll('input').forEach(input => { input.checked = true; });
  updateSourceCount();
});
document.querySelector('#select-none').addEventListener('click', () => {
  sourceOptions.querySelectorAll('input').forEach(input => { input.checked = false; });
  updateSourceCount();
});
sourceOptions.addEventListener('change', updateSourceCount);
window.addEventListener('resize', syncSourcePanelDragExclusion);
closeDeveloperWarning.addEventListener('click', () => developerWarning.close());
document.querySelector('#close-panel').addEventListener('click', closeDetails);
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeDetails(); });
document.querySelectorAll('[data-query]').forEach(button => {
  button.addEventListener('click', () => {
    queryInput.value = button.dataset.query;
    runSearch(button.dataset.query);
  });
});

loadSources().catch(error => { setPlainStatus(error.message); });
