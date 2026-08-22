const form = document.querySelector('#search-form');
const queryInput = document.querySelector('#query');
const gallery = document.querySelector('#gallery');
const emptyState = document.querySelector('#empty-state');
const statusLine = document.querySelector('#search-status');
const sourcePanel = document.querySelector('#source-panel');
const sourceOptions = document.querySelector('#source-options');
const toggleSources = document.querySelector('#toggle-sources');
const tileTemplate = document.querySelector('#tile-template');
const detailPanel = document.querySelector('#detail-panel');
const detailImageLink = document.querySelector('#detail-image-link');
const detailImage = document.querySelector('#detail-image');
const detailImageSpinner = document.querySelector('#detail-image-spinner');
const detailDimensionsOverlay = document.querySelector('#detail-dimensions-overlay');
const detailTitle = document.querySelector('#detail-title');
const detailSource = document.querySelector('#detail-source');
const detailDescription = document.querySelector('#detail-description');
const detailLicense = document.querySelector('#detail-license');
const detailSize = document.querySelector('#detail-size');
const detailLink = document.querySelector('#detail-link');
const similarGrid = document.querySelector('#similar-grid');
const similarStatus = document.querySelector('#similar-status');
const alternateSection = document.querySelector('#alternate-section');
const alternateGrid = document.querySelector('#alternate-grid');
const alternateStatus = document.querySelector('#alternate-status');

if (new URLSearchParams(location.search).has('desktop')) {
  document.body.classList.add('desktop-app');
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
const MAX_PARALLEL_COLLECTIONS = 6;

function selectedSources() {
  return [...sourceOptions.querySelectorAll('input:checked')].map(input => input.value);
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

async function mapWithConcurrency(items, limit, mapper) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const index = next++;
      results[index] = await mapper(items[index]);
    }
  }
  await Promise.all(
    Array.from({length: Math.min(limit, items.length)}, () => worker()),
  );
  return results;
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
}

function imageRatio(item) {
  const ratio = item.width && item.height ? item.width / item.height : 1.38;
  return Math.max(.62, Math.min(ratio, 2.8));
}

function imageCandidates(item, { detail = false } = {}) {
  const cachedDetail = detail
    && item.preview_click_action === 'open_full_image'
    && currentSession
    ? `/api/image/detail?session=${encodeURIComponent(currentSession)}&id=${encodeURIComponent(item.id)}`
    : '';
  const normal = detail
    ? [cachedDetail, item.image_url, item.thumb_url, item.placeholder_url]
    : [item.thumb_url, item.image_url, item.placeholder_url];
  const aicProxy = item.source === 'aic'
    && ['huggingface', 'wayback'].includes(item.image_delivery)
    && currentSession
    ? `/api/image/aic?session=${encodeURIComponent(currentSession)}&id=${encodeURIComponent(item.id)}`
    : '';
  const harvardProxy = item.source === 'harvard' && currentSession
    ? `/api/image/harvard?session=${encodeURIComponent(currentSession)}&id=${encodeURIComponent(item.id)}${detail ? '&detail=1' : ''}`
    : '';
  // Safari receives mirror previews through our same-origin endpoint with an
  // explicit image MIME type. The signed source URL and tiny API LQIP remain
  // fallbacks if the bounded proxy cannot retrieve a preview.
  const aic = detail
    ? [cachedDetail, aicProxy, item.image_url, item.thumb_url, item.placeholder_url]
    : [aicProxy, item.thumb_url, item.placeholder_url];
  const harvard = detail
    ? [cachedDetail, harvardProxy, item.image_url, item.thumb_url, item.placeholder_url]
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
    else image.closest('.image-tile')?.classList.add('image-unavailable');
  };
  if (candidates.length) image.src = candidates[0];
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

function renderGallery(items) {
  currentResults = items;
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
  renderGallery(snapshot.results);
  const unavailable = Object.keys(snapshot.source_errors);
  const errors = unavailable.length;
  const policies = Object.values(snapshot.source_policy || {});
  const active = policies.filter(policy => policy.continue).length;
  const searched = policies.length - active;
  const progress = policies.length
    ? ` · ${searched} of ${policies.length} collections searched`
    : '';
  const errorText = errors
    ? ` · ${errors} unavailable (${unavailable.map(collectionLabel).join(', ')})`
    : '';
  const rankingText = snapshot.ranking_mode === 'pamela' ? ' · size × PAMELA' : '';
  statusLine.textContent = `${snapshot.results.length} ranked images${rankingText}${progress}${errorText}`;
}

async function searchSourceBatch(source, offset, sequence, sessionId) {
  if (sequence !== currentSearchSequence) return {source, ok: false, ignored: true};
  const controller = new AbortController();
  searchControllers.add(controller);
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, source === 'aic' ? 45000 : 25000);
  try {
    const snapshot = await getJson(
      `/api/search/source?session=${encodeURIComponent(sessionId)}&source=${encodeURIComponent(source)}&offset=${offset}`,
      {signal: controller.signal}
    );
    applySnapshot(snapshot, sequence);
    return {source, ok: true};
  } catch (error) {
    if (sequence !== currentSearchSequence || (!timedOut && error.name === 'AbortError')) {
      return {source, ok: false, ignored: true};
    }
    if (timedOut) {
      fetch(
        `/api/search/source/cancel?session=${encodeURIComponent(sessionId)}&source=${encodeURIComponent(source)}`,
        {keepalive: true},
      ).catch(() => {});
    }
    const failure = timedOut ? 'timeout'
      : error.status ? 'server'
      : 'network';
    return {source, ok: false, failure, message: error.message};
  } finally {
    clearTimeout(timer);
    searchControllers.delete(controller);
  }
}

function failureStatus(failures) {
  const timedOut = [...failures.values()].filter(item => item.failure === 'timeout');
  const other = [...failures.values()].filter(item => item.failure !== 'timeout');
  const parts = [`${currentResults.length} ranked images`];
  if (timedOut.length) {
    parts.push(`${timedOut.length} timed out (${timedOut.map(item => collectionLabel(item.source)).join(', ')})`);
  }
  if (other.length) {
    parts.push(`${other.length} request${other.length === 1 ? '' : 's'} failed (${other.map(item => collectionLabel(item.source)).join(', ')})`);
  }
  return parts.join(' · ');
}

async function runSearch(query) {
  const selected = selectedSources();
  if (!selected.length) {
    sourcePanel.hidden = false;
    toggleSources.setAttribute('aria-expanded', 'true');
    statusLine.textContent = 'Select at least one collection.';
    return;
  }
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
  emptyState.hidden = true;
  statusLine.textContent = `Searching ${selected.length} collections…`;

  try {
    const start = await getJson(
      `/api/search/start?q=${encodeURIComponent(query)}&sources=${encodeURIComponent(selected.join(','))}`
    );
    if (sequence !== currentSearchSequence) return;
    currentSession = start.session_id;
    const sessionId = start.session_id;
    applySnapshot(start, sequence);
    const offsets = Object.fromEntries(selected.map(source => [source, 0]));
    const failures = new Map();
    let active = [...selected];
    while (active.length && sequence === currentSearchSequence) {
      const outcomes = await mapWithConcurrency(
        active,
        MAX_PARALLEL_COLLECTIONS,
        source => searchSourceBatch(source, offsets[source], sequence, sessionId),
      );
      if (sequence !== currentSearchSequence) return;
      for (const outcome of outcomes) {
        if (!outcome.ok && !outcome.ignored) failures.set(outcome.source, outcome);
      }
      const policy = await getJson(`/api/search/policy?session=${encodeURIComponent(sessionId)}`);
      applySnapshot(policy, sequence);
      const succeeded = new Set(outcomes.filter(item => item.ok).map(item => item.source));
      active = active.filter(source => succeeded.has(source) && policy.source_policy[source]?.continue);
      for (const source of active) offsets[source] += 10;
    }
    if (sequence === currentSearchSequence && failures.size) {
      statusLine.textContent = failureStatus(failures);
    }
    if (sequence === currentSearchSequence && currentResults.length === 0) {
      gallery.innerHTML = '<p class="notice">No matching images found.</p>';
    }
  } catch (error) {
    if (sequence === currentSearchSequence) statusLine.textContent = error.message;
  }
}

function findItem(id) {
  return currentResults.find(item => item.id === id) || panelItems.get(id);
}

async function openDetails(id, previewImage) {
  const item = findItem(id);
  if (!item) return;
  selectedItemId = id;
  gallery.querySelectorAll('.image-tile').forEach(tile =>
    tile.classList.toggle('selected', tile.dataset.id === id));
  const galleryPreview = galleryTiles.get(id)?.querySelector('img');
  showDetailImage(item, previewImage || galleryPreview);
  detailTitle.textContent = item.title;
  detailSource.textContent = item.source_label;
  detailDescription.textContent = item.description || 'No additional description was supplied by this collection.';
  detailLicense.textContent = item.license;
  detailSize.textContent = item.width && item.height
    ? (item.preview_width && item.preview_height
      ? `Original ${item.width} × ${item.height} px · Preview ${item.preview_width} × ${item.preview_height} px`
      : `${item.width} × ${item.height} px`)
    : '';
  detailLink.href = item.page_url || item.image_url;
  const previewAction = item.preview_click_action === 'visit_website'
    ? 'visit website'
    : 'open full sized image';
  detailImageLink.href = item.preview_click_url || item.page_url || item.image_url;
  detailImageLink.title = previewAction;
  detailImageLink.setAttribute('aria-label', previewAction);
  detailDimensionsOverlay.hidden = !(item.width && item.height);
  detailDimensionsOverlay.textContent = item.width && item.height
    ? `${item.width} × ${item.height}`
    : '';
  detailPanel.hidden = false;
  document.body.classList.add('panel-open');
  similarGrid.replaceChildren();
  alternateGrid.replaceChildren();
  alternateSection.hidden = true;
  similarStatus.textContent = 'Comparing images in this search…';
  await loadSimilar(id);
}

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

form.addEventListener('submit', event => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (query) runSearch(query);
});

toggleSources.addEventListener('click', () => {
  const open = sourcePanel.hidden;
  sourcePanel.hidden = !open;
  toggleSources.setAttribute('aria-expanded', String(open));
});
document.addEventListener('pointerdown', event => {
  if (!sourcePanel.hidden && !sourcePanel.contains(event.target)
      && !toggleSources.contains(event.target)) {
    sourcePanel.hidden = true;
    toggleSources.setAttribute('aria-expanded', 'false');
  }
});
document.querySelector('#select-all').addEventListener('click', () =>
  sourceOptions.querySelectorAll('input').forEach(input => { input.checked = true; }));
document.querySelector('#select-none').addEventListener('click', () =>
  sourceOptions.querySelectorAll('input').forEach(input => { input.checked = false; }));
document.querySelector('#close-panel').addEventListener('click', closeDetails);
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeDetails(); });
document.querySelectorAll('[data-query]').forEach(button => {
  button.addEventListener('click', () => {
    queryInput.value = button.dataset.query;
    runSearch(button.dataset.query);
  });
});

loadSources().catch(error => { statusLine.textContent = error.message; });
