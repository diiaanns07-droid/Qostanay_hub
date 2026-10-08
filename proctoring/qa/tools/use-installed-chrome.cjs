// QA-only browser selection. Existing local Chrome; no download or product patch.
const { chromium } = require('playwright');
const originalLaunch = chromium.launch.bind(chromium);
if (!process.env.QORGAU_CHROMIUM) throw new Error('QORGAU_CHROMIUM is required for this QA adapter');
chromium.launch = (options = {}) => originalLaunch({
  ...options,
  executablePath: options.executablePath || process.env.QORGAU_CHROMIUM,
});
