const GnosisFullSizeImageUrl = (() => {
  const AVAILABLE_TOOLTIP = 'copy image url';
  const UNAVAILABLE_TOOLTIP = 'full sized image url unavailable - visit the image website';

  function fullSizeImageUrl(item) {
    const value = String(item?.download_url || '');
    try {
      const url = new URL(value);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_error) {
      return '';
    }
  }

  function controlState(item) {
    const url = fullSizeImageUrl(item);
    return {
      disabled: !url,
      tooltip: url ? AVAILABLE_TOOLTIP : UNAVAILABLE_TOOLTIP,
      url,
    };
  }

  return { AVAILABLE_TOOLTIP, UNAVAILABLE_TOOLTIP, controlState, fullSizeImageUrl };
})();

if (typeof module === 'object' && module.exports) module.exports = GnosisFullSizeImageUrl;
