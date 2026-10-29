const assert = require('node:assert/strict');
const test = require('node:test');

const { notarizeMacApp, STAPLE_RETRY_DELAYS_SECONDS } = require('../scripts/notarize-mac-app');

const credentials = { appleApiKey: '/tmp/key.p8', appleApiKeyId: 'KEY', appleApiIssuer: 'ISSUER' };

function harness({ notarizeError, stapleFailures = 0 }) {
  const calls = { notarize: [], staple: 0, pauses: [] };
  return {
    calls,
    deps: {
      notarize: async options => {
        calls.notarize.push(options);
        if (notarizeError) throw notarizeError;
      },
      staple: async () => {
        calls.staple += 1;
        if (calls.staple <= stapleFailures) throw new Error('The network connection was lost.');
      },
      pause: async seconds => { calls.pauses.push(seconds); },
      log: () => {}
    }
  };
}

test('a clean notarization does not staple again', async () => {
  const { calls, deps } = harness({});
  await notarizeMacApp('/out/Gnosis Images.app', credentials, deps);
  assert.deepEqual(calls.notarize, [{ appPath: '/out/Gnosis Images.app', ...credentials }]);
  assert.equal(calls.staple, 0);
});

test('a failed staple is retried after a wait without resubmitting', async () => {
  const { calls, deps } = harness({
    notarizeError: new Error('Failed to staple your application with code: 68'),
    stapleFailures: 1
  });
  await notarizeMacApp('/out/Gnosis Images.app', credentials, deps);
  assert.equal(calls.notarize.length, 1);
  assert.equal(calls.staple, 2);
  assert.deepEqual(calls.pauses, STAPLE_RETRY_DELAYS_SECONDS.slice(0, 2));
});

test('staple retries give up after the last wait', async () => {
  const { calls, deps } = harness({
    notarizeError: new Error('Failed to staple your application with code: 68'),
    stapleFailures: Infinity
  });
  await assert.rejects(notarizeMacApp('/out/Gnosis Images.app', credentials, deps), /connection was lost/);
  assert.equal(calls.staple, STAPLE_RETRY_DELAYS_SECONDS.length);
  assert.ok(STAPLE_RETRY_DELAYS_SECONDS.reduce((sum, seconds) => sum + seconds, 0) >= 300);
});

test('a rejected notarization is not retried', async () => {
  const { calls, deps } = harness({ notarizeError: new Error('Notarization failed: status Invalid') });
  await assert.rejects(notarizeMacApp('/out/Gnosis Images.app', credentials, deps), /status Invalid/);
  assert.equal(calls.staple, 0);
});
