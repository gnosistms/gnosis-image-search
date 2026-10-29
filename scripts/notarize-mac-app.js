const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

// @electron/notarize retries a failed staple only seconds apart, which a
// dropped connection to Apple's ticket server outlasts. By then the app is
// already notarized, so only the staple needs repeating, with longer waits.
const STAPLE_RETRY_DELAYS_SECONDS = [30, 60, 120, 240];

async function stapleApp(appPath) {
  await promisify(execFile)('xcrun', ['stapler', 'staple', '-v', appPath]);
}

function wait(seconds) {
  return new Promise(resolve => setTimeout(resolve, seconds * 1000));
}

async function notarizeMacApp(appPath, credentials, {
  notarize = require('@electron/notarize').notarize,
  staple = stapleApp,
  pause = wait,
  log = console.warn
} = {}) {
  try {
    await notarize({ appPath, ...credentials });
    return;
  } catch (error) {
    if (!/^Failed to staple/.test(error.message)) throw error;
    log(`Notarized ${appPath}, but stapling failed; retrying the staple.`);
  }
  for (const [index, seconds] of STAPLE_RETRY_DELAYS_SECONDS.entries()) {
    await pause(seconds);
    try {
      await staple(appPath);
      return;
    } catch (error) {
      if (index === STAPLE_RETRY_DELAYS_SECONDS.length - 1) throw error;
      log(`Staple retry ${index + 1} failed: ${error.message}`);
    }
  }
}

module.exports = { notarizeMacApp, STAPLE_RETRY_DELAYS_SECONDS };
