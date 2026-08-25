const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('Electron keeps controls and resize edges out of the hero drag region', () => {
  const markup = fs.readFileSync(path.join(__dirname, '..', 'web', 'index.html'), 'utf8');
  const script = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'web', 'styles.css'), 'utf8');

  assert.match(markup, /<div class="topbar-drag-handle" aria-hidden="true"><\/div>/);
  assert.match(markup, /window-resize-edge-top/);
  assert.match(markup, /id="source-panel-drag-exclusion"/);
  assert.match(script, /sourcePanel\.getBoundingClientRect\(\)/);
  assert.match(script, /sourcePanelDragExclusion\.hidden = !open/);
  assert.match(styles, /body\.desktop-app \.empty-state \{ -webkit-app-region: drag; \}/);
  assert.match(styles, /body\.desktop-app \.source-panel-drag-exclusion .*z-index: 39; -webkit-app-region: no-drag;/);
  assert.match(styles, /body\.desktop-app \.topbar \{ padding-left: 86px; -webkit-app-region: drag; \}/);
  assert.match(styles, /body\.desktop-app \.topbar-drag-handle .*left: 70px; width: 14px;.*-webkit-app-region: drag;/);
  assert.match(styles, /body\.desktop-win32 \.topbar \{ padding-right: calc\(18px \+ env\(titlebar-area-width, 138px\)\); padding-left: 18px; \}/);
  assert.match(styles, /body\.desktop-app \.window-resize-edge-top .*height: 6px;/);
  assert.match(
    styles,
    /body\.desktop-app \.hero-search-form, body\.desktop-app \.hero-filter-button, body\.desktop-app \.source-panel \{ -webkit-app-region: no-drag; \}/,
  );
});
