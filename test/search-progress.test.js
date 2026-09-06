const test = require('node:test');
const assert = require('node:assert/strict');
const {describe} = require('../web/search-progress');

test('distinguishes scoring and quality pauses from finished collections', () => {
  const state = describe({stream_running: true, source_policy: {
    met: {stage: 'scoring', continue: false},
    commons: {stage: 'paused', continue: false},
    nga: {stage: 'finished', continue: false},
    hidden: {stage: 'hidden', selected: false},
  }});
  assert.equal(state.text, '1 finished · 1 scoring · 1 paused');
  assert.equal(state.running, true);
});

test('a heartbeat proves contact but not useful progress', () => {
  const state = describe({stream_running: true, progress_age_seconds: 50}, 1000, 2000);
  assert.match(state.connection, /Still connected.*51s/);
});

test('missing heartbeats show uncertainty rather than declaring a deadlock', () => {
  const state = describe({stream_running: true}, 1000, 17000);
  assert.match(state.connection, /Connection uncertain/);
});

test('completed searches do not acquire stale connection warnings', () => {
  const state = describe({lifecycle: 'complete', stream_running: false}, 1000, 999999);
  assert.equal(state.connection, '');
  assert.equal(state.running, false);
});
