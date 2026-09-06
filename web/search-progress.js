(function (root) {
  function describe(snapshot, lastContact = Date.now(), now = Date.now()) {
    const policies = Object.values(snapshot?.source_policy || {}).filter(p => p.selected !== false);
    const counts = {};
    for (const policy of policies) {
      const stage = policy.stage || (policy.continue ? 'fetching' : 'finished');
      counts[stage] = (counts[stage] || 0) + 1;
    }
    const labels = {finished: 'finished', fetching: 'fetching', scoring: 'scoring',
      evaluating: 'evaluating', queued: 'queued', paused: 'paused', incomplete: 'incomplete', failed: 'failed'};
    const text = Object.entries(labels).filter(([stage]) => counts[stage])
      .map(([stage, label]) => `${counts[stage]} ${label}`).join(' · ');
    const running = Boolean(snapshot?.stream_running || snapshot?.lifecycle === 'running');
    const age = (snapshot?.progress_age_seconds || 0) + Math.max(0, now - lastContact) / 1000;
    const connection = !running ? '' : now - lastContact > 15000
      ? 'Connection uncertain — reconnect to check this search.'
      : age >= 30 ? `Still connected · no new results or completed work for ${Math.floor(age)}s`
        : 'Connected · search in progress';
    return {text, counts, running, connection, age};
  }
  const api = {describe};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.GnosisSearchProgress = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
