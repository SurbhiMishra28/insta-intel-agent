// Browser AI check: home render -> analyze flow -> dashboard -> AI chat round-trip.
// Usage: node _uitest/browser-ai-check.mjs [handle]
import puppeteer from 'puppeteer-core';

const HANDLE = process.argv[2] || 'nasa';
const MESSAGE = process.argv[3] || 'Reply with exactly: Browser AI check OK';
const EXPECT = process.argv[4] || 'Browser AI check OK';

const CHROME = process.env.IG_CHROME_PATH || 'C:/Users/DELL/AppData/Local/Google/Chrome/Application/chrome.exe';
const BASE = 'http://127.0.0.1:5173';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const consoleErrors = [];
const failedRequests = [];

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--window-size=1440,2200'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1100, deviceScaleFactor: 1 });
page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text().slice(0, 200)); });
page.on('requestfailed', (req) => failedRequests.push(`${req.url().slice(0, 120)} -> ${req.failure()?.errorText}`));
const shot = async (name) => {
  await page.screenshot({ path: `_uitest/${name}.png` });
  console.log('shot:', name);
};

let failed = false;
try {
  // ---------- 1. Home ----------
  await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 60000 });
  await sleep(1500);
  const hero = await page
    .$eval('header.hero h1, .hero .title, .hero', (el) => el.textContent.replace(/\s+/g, ' ').trim().slice(0, 80))
    .catch(() => null);
  console.log('[1] hero text:', hero || 'NOT FOUND');
  if (!hero) throw new Error('home page did not render hero');
  await shot('browser-01-home');

  // ---------- 2. Analyze flow (mounts the dashboard + ChatBox) ----------
  await page.click('.scanner input', { clickCount: 3 });
  await page.type('.scanner input', HANDLE, { delay: 30 });
  console.log('[2] typed handle:', HANDLE, '— clicking submit, waiting for pipeline (real fetch)...');
  await page.click('.scanner-submit');
  await page.waitForSelector('.dash-hero-top h2', { timeout: 560000 });
  const dashHandle = await page.$eval('.dash-hero-top h2', (el) => el.textContent.trim());
  const stats = await page.$$eval('.dash-hero .stat-box', (els) => els.length);
  console.log(`[2] dashboard rendered: ${dashHandle} (${stats} stat boxes)`);
  await shot('browser-02-dashboard');

  // ---------- 3. Open AI chat ----------
  await page.click('button[aria-label="Open chat"]');
  await page.waitForSelector('button[aria-label="Send message"]', { timeout: 10000 });
  console.log('[3] chat panel open');

  // ---------- 4. Send message, wait for reply ----------
  await page.type('textarea:has(+ button[aria-label="Send message"])', MESSAGE, { delay: 10 });
  await page.click('button[aria-label="Send message"]');
  console.log('[4] message sent, waiting for AI reply...');
  await page.waitForFunction(
    () => {
      const boxes = document.querySelectorAll('div[style*="max-width: 88%"]');
      return boxes.length >= 2; // user msg + bot msg
    },
    { timeout: 180000 },
  );
  await sleep(800); // let state settle
  const botText = await page.evaluate(() => {
    const boxes = [...document.querySelectorAll('div[style*="max-width: 88%"]')];
    return boxes.length ? boxes[boxes.length - 1].textContent.trim() : '';
  });
  const ruleBased = botText.includes('rule-based answer');
  console.log('[4] bot reply:', botText.slice(0, 200));
  await shot('browser-03-chat-reply');

  // ---------- 5. Verdict ----------
  const llmOk = !ruleBased && botText.length > 0;
  console.log('--- VERDICT ---');
  console.log('reply received:', botText ? 'YES' : 'NO');
  console.log('llm_used (no rule-based fallback marker):', llmOk ? 'YES' : 'NO');
  console.log('console errors:', consoleErrors.length ? JSON.stringify(consoleErrors.slice(0, 5)) : 'none');
  console.log('failed requests:', failedRequests.length ? JSON.stringify(failedRequests.slice(0, 5)) : 'none');
  failed = !(llmOk && botText.toLowerCase().includes(EXPECT.toLowerCase()));
  console.log(failed ? `❌ FAIL — expected "${EXPECT}" in reply` : '✅ UI + AI CHAT PASSED in browser');
} catch (err) {
  failed = true;
  const bodyText = await page.evaluate(() => document.body.innerText.slice(0, 400)).catch(() => '(page unreadable)');
  console.log('❌ ERROR:', err.message);
  console.log('page shows:', bodyText.replace(/\s+/g, ' '));
  await shot('browser-99-failure').catch(() => {});
  if (consoleErrors.length) console.log('console errors:', JSON.stringify(consoleErrors.slice(0, 5)));
  if (failedRequests.length) console.log('failed requests:', JSON.stringify(failedRequests.slice(0, 5)));
} finally {
  await browser.close();
}
process.exit(failed ? 1 : 0);
