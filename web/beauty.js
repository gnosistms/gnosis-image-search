const $ = selector => document.querySelector(selector);
const ui = {
  leftCard: $('#left-card'), rightCard: $('#right-card'),
  leftImage: $('#left-image'), rightImage: $('#right-image'),
  leftTitle: $('#left-title'), rightTitle: $('#right-title'),
  leftSource: $('#left-source'), rightSource: $('#right-source'),
  tie: $('#tie'), noOpinion: $('#no-opinion'), undo: $('#undo'),
  count: $('#decision-count'), ranking: $('#ranking'), status: $('#status'),
};
let current = null;
let busy = false;

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}

function render(data) {
  current = data;
  const [left, right] = data.pair;
  for (const [side, item] of [['left', left], ['right', right]]) {
    ui[`${side}Image`].src = item.image_url;
    ui[`${side}Image`].dataset.imageUrl = item.image_url;
    ui[`${side}Image`].alt = item.title;
    ui[`${side}Title`].textContent = item.title;
    ui[`${side}Source`].textContent = item.source;
  }
  ui.count.textContent = `${data.decisions} decision${data.decisions === 1 ? '' : 's'} · ${data.total_images} images`;
  ui.undo.disabled = data.votes === 0;
  ui.ranking.replaceChildren(...data.ranking.map((item, index) => {
    const li = document.createElement('li');
    li.textContent = item.title;
    const meta = document.createElement('span');
    meta.textContent = index < 10 ? item.source : '';
    li.append(meta);
    return li;
  }));
}

async function vote(choice) {
  if (!current || busy) return;
  busy = true; document.body.classList.add('loading'); ui.status.textContent = '';
  try {
    render(await request('/api/beauty/vote', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({left_id: current.pair[0].id, right_id: current.pair[1].id, choice}),
    }));
  } catch (error) { ui.status.textContent = error.message; }
  finally { busy = false; document.body.classList.remove('loading'); }
}

ui.leftCard.addEventListener('click', () => vote('left'));
ui.rightCard.addEventListener('click', () => vote('right'));
ui.tie.addEventListener('click', () => vote('tie'));
ui.noOpinion.addEventListener('click', () => vote('no_opinion'));
ui.undo.addEventListener('click', async () => {
  if (busy) return; busy = true;
  try { render(await request('/api/beauty/undo', {method:'POST'})); }
  catch (error) { ui.status.textContent = error.message; }
  finally { busy = false; }
});
document.addEventListener('keydown', event => {
  if (event.repeat) return;
  if (event.key.toLowerCase() === 'a') vote('left');
  else if (event.key.toLowerCase() === 'd') vote('right');
  else if (event.key.toLowerCase() === 's') vote('tie');
  else if (event.code === 'Space') { event.preventDefault(); vote('no_opinion'); }
});
request('/api/beauty/state').then(render).catch(error => { ui.status.textContent = error.message; });
