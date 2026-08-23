const STAGES = [4, 9, 16, 25];
const STORAGE_KEY = 'gnosis-google-ranked-v1';
const form = document.querySelector('#search-form');
const queryInput = document.querySelector('#search-query');
const searchButton = document.querySelector('#search-button');
const sourceOptions = document.querySelector('#source-options');
const processSummary = document.querySelector('#process-summary');
const resultsSection = document.querySelector('#results-section');
const resultCount = document.querySelector('#result-count');
const resultTitle = document.querySelector('#result-title');
const formulaNote = document.querySelector('#formula-note');
const resultNotice = document.querySelector('#result-notice');
const gallery = document.querySelector('#gallery');
const resultTemplate = document.querySelector('#result-template');
let sourceConfig = [];
let compiled = [];
let currentSort = 'final';
let searchSequence = 0;

function loadState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch (_) { return {}; }
}
const saved = loadState();
queryInput.value = typeof saved.query === 'string' ? saved.query : 'angel gabriel';

function selectedSources() {
  return [...sourceOptions.querySelectorAll('input:checked')].map(input => input.value);
}
function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ query: queryInput.value, sources: selectedSources() }));
}
function stageElement(mp) { return document.querySelector(`.stage[data-stage="${mp}"]`); }
function resetStages() {
  for (const mp of STAGES) {
    const stage = stageElement(mp);
    stage.className = 'stage';
    stage.querySelector('strong').textContent = 'Waiting';
  }
}
function updateStage(mp, state, text) {
  const stage = stageElement(mp);
  stage.className = `stage ${state}`;
  stage.querySelector('strong').textContent = text;
}
async function getJson(url) {
  const response = await fetch(url);
  let body = {};
  try { body = await response.json(); } catch (_) {}
  if (!response.ok) {
    const error = new Error(body.error || `Request failed (${response.status}).`);
    error.code = body.code || '';
    throw error;
  }
  return body;
}
async function loadSources() {
  const data = await getJson('/api/sources');
  // The normal museum search uses every configured API/catalog. Google Images
  // exposes only the providers whose collection-object pages it indexes well.
  sourceConfig = data.sources.filter(source => source.google_images);
  const savedSources = Array.isArray(saved.sources) ? new Set(saved.sources) : null;
  sourceOptions.replaceChildren();
  for (const source of sourceConfig) {
    const domains = (source.google_domains || []).join(' · ');
    const checked = savedSources ? savedSources.has(source.id) : source.default;
    const label = document.createElement('label');
    label.className = 'source-choice';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = source.id;
    input.checked = checked;
    const copy = document.createElement('span');
    const name = document.createElement('strong');
    const domain = document.createElement('small');
    name.textContent = source.label;
    domain.textContent = domains;
    copy.append(name, domain);
    label.append(input, copy);
    sourceOptions.append(label);
  }
}

async function collectStage(query, sources, mp) {
  if (!window.gnosisGoogle?.searchStage) {
    throw new Error('Rendered Google collection is available only inside the Gnosis Images desktop app.');
  }
  const selected = new Set(sources);
  const domains = sourceConfig
    .filter(source => selected.has(source.id))
    .flatMap(source => source.google_domains || []);
  const response = await window.gnosisGoogle.searchStage({ query, domains, mp });
  return {
    cached: response.cached,
    results: GoogleRanking.normalizeStageResults(response.results, sourceConfig, sources, mp, 200),
  };
}

function orderedResults() {
  return GoogleRanking.rankResults(compiled, currentSort);
}
function renderResults() {
  if (!compiled.length) return;
  resultsSection.hidden = false;
  const ordered = orderedResults();
  resultCount.textContent = `${compiled.length} unique images collected`;
  resultTitle.textContent = currentSort === 'final' ? 'Size-adjusted ranking' : 'Sizeless Google ranking';
  formulaNote.textContent = currentSort === 'final'
    ? 'Final score = sizeless rank points × log₂(width × height). Rank points descend from the total result count to 1.'
    : 'The 4 MP Google order comes first. Images discovered only at 9, 16, or 25 MP are appended after the previously known set.';
  gallery.replaceChildren();
  ordered.forEach((item, index) => {
    const card = resultTemplate.content.firstElementChild.cloneNode(true);
    const link = card.querySelector('.image-link');
    const image = card.querySelector('img');
    link.href = item.page_url || item.image_url;
    image.src = item.thumb_url || item.image_url;
    image.dataset.imageUrl = item.image_url || item.thumb_url || '';
    image.alt = item.title || 'Museum image';
    image.addEventListener('error', () => {
      if (image.src !== item.image_url) image.src = item.image_url;
      else card.classList.add('image-unavailable');
    }, { once: true });
    card.querySelector('.rank-badge').textContent = `#${index + 1}`;
    card.querySelector('.dimensions').textContent = `${item.width} × ${item.height} · ${item.megapixels} MP`;
    card.querySelector('.result-source').textContent = `${item.source_label} · found at ${item.discovered_stage} MP`;
    card.querySelector('h3').textContent = item.title || 'Untitled';
    card.querySelector('.result-score').textContent = currentSort === 'final'
      ? `${item.rank_points} rank points × ${item.size_score.toFixed(2)} size = ${item.final_score.toFixed(2)}`
      : `Sizeless rank #${item.sizeless_rank} · stages ${item.stages.join(', ')} MP`;
    gallery.append(card);
  });
}

async function runSearch(event) {
  event.preventDefault();
  const query = queryInput.value.trim();
  const sources = selectedSources();
  if (!query) { queryInput.focus(); return; }
  if (!sources.length) {
    resultNotice.textContent = 'Choose at least one collection.';
    resultNotice.hidden = false;
    resultsSection.hidden = false;
    return;
  }
  saveState();
  const sequence = ++searchSequence;
  compiled = [];
  currentSort = 'final';
  document.querySelectorAll('.sort-toggle button').forEach(button => button.classList.toggle('selected', button.dataset.sort === 'final'));
  resetStages();
  gallery.replaceChildren();
  resultsSection.hidden = false;
  resultNotice.hidden = true;
  resultCount.textContent = 'Collecting Google metadata';
  resultTitle.textContent = 'Building the ranking…';
  formulaNote.textContent = 'The 4 MP pass runs first so larger-image passes cannot rewrite Google’s broad ranking.';
  searchButton.disabled = true;
  searchButton.textContent = 'Searching…';
  const errors = [];
  for (const mp of STAGES) {
    if (sequence !== searchSequence) return;
    updateStage(mp, 'running', 'Searching…');
    processSummary.textContent = `Collecting ${mp} MP metadata`;
    try {
      const stage = await collectStage(query, sources, mp);
      if (sequence !== searchSequence) return;
      GoogleRanking.mergeStage(compiled, stage.results, mp);
      updateStage(mp, 'complete', `${stage.results.length} results${stage.cached ? ' · cached' : ''}`);
      renderResults();
    } catch (error) {
      errors.push(`${mp} MP: ${error.message}`);
      updateStage(mp, 'error', 'Could not collect');
      if (mp === 4) break;
    }
  }
  if (sequence !== searchSequence) return;
  searchButton.disabled = false;
  searchButton.textContent = 'Search Google Images';
  processSummary.textContent = errors.length ? 'Finished with warnings' : 'Complete';
  if (errors.length) {
    resultNotice.textContent = errors.join(' ');
    resultNotice.hidden = false;
  }
  if (!compiled.length) {
    resultCount.textContent = 'No image metadata collected';
    resultTitle.textContent = 'Google did not return usable results';
  }
}

form.addEventListener('submit', runSearch);
sourceOptions.addEventListener('change', saveState);
queryInput.addEventListener('input', saveState);
document.querySelector('#select-all').addEventListener('click', () => { sourceOptions.querySelectorAll('input').forEach(input => { input.checked = true; }); saveState(); });
document.querySelector('#select-none').addEventListener('click', () => { sourceOptions.querySelectorAll('input').forEach(input => { input.checked = false; }); saveState(); });
document.querySelector('.sort-toggle').addEventListener('click', event => {
  const button = event.target.closest('button[data-sort]');
  if (!button) return;
  currentSort = button.dataset.sort;
  document.querySelectorAll('.sort-toggle button').forEach(candidate => candidate.classList.toggle('selected', candidate === button));
  renderResults();
});
loadSources().catch(error => { sourceOptions.textContent = `Collections could not be loaded: ${error.message}`; });
