const GnosisSearchTermHighlight = (() => {
  const WORDS = /[\p{L}\p{M}\p{N}_]+/gu;

  function foldToken(value) {
    return String(value || '')
      .normalize('NFKD')
      .replace(/\p{M}/gu, '')
      .toLocaleLowerCase();
  }

  function termSet(query) {
    const values = Array.isArray(query) ? query : [query];
    const terms = new Set();
    for (const value of values) {
      for (const match of String(value || '').matchAll(WORDS)) {
        terms.add(foldToken(match[0]));
      }
    }
    return terms;
  }

  function matchingLength(word, terms, mode) {
    const foldedWord = foldToken(word);
    if (terms.has(foldedWord)) return word.length;
    if (!['english_stem', 'prefix'].includes(mode)) return 0;

    if (mode === 'english_stem') {
      for (const term of terms) {
        const querySuffix = term.startsWith(foldedWord)
          ? term.slice(foldedWord.length)
          : '';
        if (foldedWord.length >= 3
            && ['s', 'es', 'ed', 'ing'].includes(querySuffix)) return word.length;
      }
    }

    let bestLength = 0;
    for (let end = 1; end < word.length; end += 1) {
      // Do not split a UTF-16 surrogate pair when walking the displayed word.
      if (end < word.length
          && /[\uD800-\uDBFF]/.test(word[end - 1])
          && /[\uDC00-\uDFFF]/.test(word[end])) continue;
      const prefix = foldToken(word.slice(0, end));
      // Keep short terms literal so searches such as "a rose" do not mark the
      // first letter of every word beginning with "a".
      const suffix = foldToken(word.slice(end));
      const providerAllowsMatch = mode === 'prefix'
        || ['s', 'es', 'ed', 'ing'].includes(suffix);
      if (terms.has(prefix) && prefix.length >= 3 && providerAllowsMatch) {
        bestLength = end;
      }
    }
    return bestLength;
  }

  function segments(text, query, mode = 'whole_word') {
    const value = String(text || '');
    const terms = termSet(query);
    if (!value || !terms.size) return [{ text: value, highlighted: false }];

    const output = [];
    let cursor = 0;
    for (const match of value.matchAll(WORDS)) {
      if (match.index > cursor) {
        output.push({ text: value.slice(cursor, match.index), highlighted: false });
      }
      const prefixLength = matchingLength(match[0], terms, mode);
      if (prefixLength) {
        output.push({
          text: match[0].slice(0, prefixLength),
          highlighted: true,
        });
        if (prefixLength < match[0].length) {
          output.push({
            text: match[0].slice(prefixLength),
            highlighted: false,
          });
        }
      } else {
        output.push({ text: match[0], highlighted: false });
      }
      cursor = match.index + match[0].length;
    }
    if (cursor < value.length) {
      output.push({ text: value.slice(cursor), highlighted: false });
    }
    return output;
  }

  return { segments };
})();

if (typeof module === 'object' && module.exports) {
  module.exports = GnosisSearchTermHighlight;
}
