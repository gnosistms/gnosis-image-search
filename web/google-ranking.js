(function (root) {
  function canonicalUrl(value) {
    try {
      const url = new URL(value);
      url.hash = '';
      return url.href;
    } catch (_) {
      return value || '';
    }
  }

  function mergeStage(existing, incoming, mp) {
    const byId = new Map();
    const byUrl = new Map();
    for (const item of existing) {
      byId.set(item.id, item);
      byUrl.set(canonicalUrl(item.image_url), item);
    }
    for (const incomingItem of incoming) {
      let item = byId.get(incomingItem.id) || byUrl.get(canonicalUrl(incomingItem.image_url));
      if (item) {
        if (!item.stages.includes(mp)) item.stages.push(mp);
        if (incomingItem.pixel_count > item.pixel_count) {
          const order = item.sizeless_order;
          const stages = item.stages;
          Object.assign(item, incomingItem, { sizeless_order: order, stages });
        }
        continue;
      }
      item = { ...incomingItem, sizeless_order: existing.length, discovered_stage: mp, stages: [mp] };
      existing.push(item);
      byId.set(item.id, item);
      byUrl.set(canonicalUrl(item.image_url), item);
    }
    const total = existing.length;
    for (const item of existing) {
      item.sizeless_rank = item.sizeless_order + 1;
      item.rank_points = total - item.sizeless_order;
      item.final_score = item.rank_points * item.size_score;
    }
    return existing;
  }

  function hostMatches(host, domain) {
    host = String(host || '').toLowerCase().replace(/^\.+|\.+$/g, '');
    domain = String(domain || '').toLowerCase().replace(/^\.+|\.+$/g, '');
    return host === domain || host.endsWith(`.${domain}`);
  }

  function normalizeStageResults(raw, sourceConfig, selectedIds, mp, limit = 200) {
    const selected = new Set(selectedIds);
    const minimumPixels = mp * 1000000;
    const results = [];
    const seen = new Set();
    for (let googleRank = 0; googleRank < raw.length && results.length < limit; googleRank += 1) {
      const item = raw[googleRank];
      const width = Math.max(Number(item.width) || 0, 0);
      const height = Math.max(Number(item.height) || 0, 0);
      const pixelCount = width * height;
      if (pixelCount < minimumPixels) continue;
      let source = null;
      for (const candidate of sourceConfig) {
        if (!selected.has(candidate.id)) continue;
        const hosts = [item.page_url, item.image_url].map(value => {
          try { return new URL(value).hostname; } catch (_) { return ''; }
        });
        if (hosts.some(host => (candidate.google_domains || []).some(domain => hostMatches(host, domain)))) {
          source = candidate;
          break;
        }
      }
      if (!source) continue;
      const id = String(item.google_id || canonicalUrl(item.image_url));
      if (seen.has(id)) continue;
      seen.add(id);
      results.push({
        ...item,
        id,
        source: source.id,
        source_label: source.label,
        width,
        height,
        google_rank: googleRank,
        stage_mp: mp,
        pixel_count: pixelCount,
        megapixels: Math.round(pixelCount / 10000) / 100,
        size_score: Math.log2(pixelCount),
      });
    }
    return results;
  }

  function rankResults(compiled, sort = 'final') {
    const results = [...compiled];
    if (sort === 'sizeless') results.sort((a, b) => a.sizeless_order - b.sizeless_order);
    else results.sort((a, b) => b.final_score - a.final_score || a.sizeless_order - b.sizeless_order);
    return results;
  }

  const api = { canonicalUrl, mergeStage, normalizeStageResults, rankResults };
  root.GoogleRanking = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
}(typeof globalThis === 'undefined' ? this : globalThis));
