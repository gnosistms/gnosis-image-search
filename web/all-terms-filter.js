const GnosisAllTermsFilter = (() => {
  const WORDS = /[\p{L}\p{M}\p{N}_]+/gu;
  const STOPWORDS = new Set([
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'been', 'but', 'by', 'for',
    'from', 'had', 'has', 'have', 'he', 'her', 'hers', 'him', 'his', 'i',
    'in', 'is', 'it', 'its', 'me', 'my', 'of', 'on', 'or', 'our', 'ours',
    'she', 'that', 'the', 'their', 'theirs', 'them', 'these', 'they', 'this',
    'those', 'to', 'us', 'was', 'we', 'were', 'with', 'you', 'your', 'yours',
  ]);

  function foldToken(value) {
    return String(value || '')
      .normalize('NFKD')
      .replace(/\p{M}/gu, '')
      .toLocaleLowerCase();
  }

  function queryTerms(query) {
    const terms = new Set();
    for (const match of String(query || '').matchAll(WORDS)) {
      const term = foldToken(match[0]);
      if (!STOPWORDS.has(term)) terms.add(term);
    }
    return [...terms];
  }

  function matches(item, query) {
    const required = queryTerms(query);
    if (!required.length) return true;
    const pageTerms = new Set(item?.page_text_terms || []);
    return required.every(term => pageTerms.has(term));
  }

  return { matches, queryTerms };
})();

if (typeof module === 'object' && module.exports) {
  module.exports = GnosisAllTermsFilter;
}
