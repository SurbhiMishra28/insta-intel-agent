/* Browser UI test: analyze + AI chat flows at localhost:5173 using real Chrome.
   Run from frontend/:  node _uitest/test.mjs
   Artifacts (screenshots + captured JSON) land in _uitest/. */
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';

const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const BASE = 'http://localhost:5173';
const HANDLE = process.env.UI_HANDLE || '@spotify';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ' — ' + detail : ''}`);
};

fs.mkdirSync(new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'), { recursive: true });
const shot = (n) => `${new URL('.', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')}${n}.png`;
const save = (n, obj) =>
  fs.writeFileSync(
    new URL(`./${n}`, import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'),
    typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2),
  );

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
  defaultViewport: { width: 1400, height: 950 },
});
const page = await browser.newPage();
const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 300)); });
page.on('pageerror', (e) => consoleErrors.push('PAGEERROR: ' + String(e.message).slice(0, 300)));
page.on('requestfailed', (r) => {
  const u = r.url();
  if (!u.includes('favicon')) consoleErrors.push('REQFAIL: ' + u.slice(0, 120) + ' ' + (r.failure()?.errorText || ''));
});

// Capture the main API response for verification
let apiPayload = null;
let apiMs = 0;
page.on('response', async (res) => {
  if (res.url().includes('/api/growth-plan')) {
    try { apiPayload = await res.json(); } catch { /* ignore */ }
  }
});
page.on('request', (req) => { if (req.url().includes('/api/growth-plan')) apiMs = Date.now(); });

try {
  // ---------- 1. Homepage loads ----------
  await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 45000 });
  check('homepage loads', true);
  const input = await page.$('.scanner input');
  const submit = await page.$('.scanner-submit');
  check('scanner form present', !!input && !!submit);
  await page.screenshot({ path: shot('01-home') });

  // ---------- 2. Analyze flow ----------
  await page.type('.scanner input', HANDLE);
  const t0 = Date.now();
  await page.click('.scanner-submit');

  // progress indicator should appear
  try {
    await page.waitForFunction(() => document.body.innerText.match(/Fetching|Running|pipeline/i), { timeout: 8000 });
    check('progress indicator appears', true);
  } catch {
    check('progress indicator appears', false, 'no loading state seen within 8s');
  }

  // dashboard renders (IG fetch + full AI chains; NIM free tier can be slow)
  await page.waitForSelector('.dash-hero', { timeout: 180000 });
  const analyzeSecs = ((Date.now() - t0) / 1000).toFixed(1);
  check('dashboard renders after analyze', true, `${analyzeSecs}s (handle ${HANDLE})`);

  const stats = await page.$$eval('.dash-stats .stat', (els) =>
    els.map((e) => e.innerText.replace(/\n/g, ' ').trim()),
  );
  check('hero stats populated', stats.length >= 4 && !stats.join(' ').includes('— —'), stats.join(' | '));
  await page.screenshot({ path: shot('02-dashboard') });

  const simulated = await page.evaluate(() => document.body.innerText.includes('SIMULATED'));
  check('no SIMULATED badge (real data)', !simulated);

  // ---------- 3. AI report section ----------
  await page.evaluate(() => document.getElementById('report')?.scrollIntoView({ block: 'start' }));
  await sleep(400);
  await page.screenshot({ path: shot('03-ai-report') });
  const reportText = await page.$eval('#report', (e) => e.innerText.replace(/\s+/g, ' ').trim());
  save('report-text.txt', reportText.slice(0, 1200));
  check('AI report section has content', reportText.length > 200, `${reportText.length} chars`);
  // LLM narrative is grounded with concrete numbers; template text is generic.
  const grounded = /\d/.test(reportText) && reportText.length > 300;
  check('report text looks LLM-written (grounded, detailed)', grounded);

  // ---------- 4. AI chat flow ----------
  // Known UX bug: the PWA banner (z-index 1000) overlaps the chat bubble
  // (z-index 999, bottom-right) — a real user must dismiss it first.
  const bannerDismissed = await page.evaluate(() => {
    const later = [...document.querySelectorAll('button')].find(
      (b) => b.innerText.trim() === 'Maybe later',
    );
    if (later) {
      later.click();
      return true;
    }
    return false;
  });
  if (bannerDismissed) {
    check('PWA banner present and dismissible (it covers the chat button — UX bug)', true);
    await sleep(400);
  }
  await page.click('button[aria-label="Open chat"]');
  await page.waitForSelector('textarea', { timeout: 5000 });
  const ctxBadge = await page.evaluate(() =>
    [...document.querySelectorAll('span')].some(
      (s) => s.innerText.trim().toLowerCase() === 'context',
    ),
  );
  check('chat panel opens with context badge', ctxBadge);

  // A slow analyze chain can open the 8s SOFT breaker right before the test
  // clicks chat (real users read the dashboard first) — wait for it to clear.
  await page.waitForFunction(
    () => fetch('http://localhost:8000/api/ai-status').then((r) => r.json()).then((s) => s.available),
    { timeout: 60000, polling: 2000 },
  );

  await page.type('textarea', 'Give me one specific tactic to grow this account this week');
  const chatT0 = Date.now();
  await page.keyboard.press('Enter');
  try {
    await page.waitForFunction(
      () => document.body.innerText.includes('thinking…') === false &&
            document.querySelectorAll('textarea').length === 1 &&
            (document.body.innerText.match(/Ask anything about the account/) === null),
      { timeout: 120000 },
    );
  } catch { /* fall through to bubble check */ }
  await sleep(500);
  const bubbles = await page.evaluate(() =>
    [...document.querySelectorAll('div')]
      .filter((d) => d.style && d.style.maxWidth === '88%')
      .map((d) => d.innerText),
  );
  const chatSecs = ((Date.now() - chatT0) / 1000).toFixed(1);
  const lastBot = bubbles.filter((b) => !b.startsWith('Give me one')).pop() || '';
  save('chat-answer.txt', lastBot);
  check('chat bot answered', lastBot.length > 50, `${chatSecs}s, ${lastBot.length} chars`);
  const ruleBadge = lastBot.includes('rule-based answer');
  check('answer is real AI (no rule-based badge)', !ruleBadge, ruleBadge ? 'RULE-BASED FALLBACK SHOWN' : 'llm answer');
  const hasMarkdown = /\*\*|^#{1,3} /m.test(lastBot);
  check('answer is plain text (no raw markdown asterisks)', !hasMarkdown);

  await page.screenshot({ path: shot('04-chat') });

  // Second question: context-grounded (should reference the account's real data)
  await page.type('textarea', 'How often does this account actually post, and is that good?');
  const chat2T0 = Date.now();
  await page.keyboard.press('Enter');
  await sleep(3000);
  await page.waitForFunction(
    (n) => [...document.querySelectorAll('div')].filter((d) => d.style && d.style.maxWidth === '88%').length >= n,
    { timeout: 120000 },
    bubbles.length + 2,
  );
  await sleep(500);
  const bubbles2 = await page.evaluate(() =>
    [...document.querySelectorAll('div')]
      .filter((d) => d.style && d.style.maxWidth === '88%')
      .map((d) => d.innerText),
  );
  const lastBot2 = bubbles2.filter((b) => !b.startsWith('Give me one') && !b.startsWith('How often')).pop() || '';
  const chat2Secs = ((Date.now() - chat2T0) / 1000).toFixed(1);
  save('chat-answer-2.txt', lastBot2);
  check('second chat answered (grounded question)', lastBot2.length > 50, `${chat2Secs}s`);
  check('second answer is real AI (no rule-based badge)', !lastBot2.includes('rule-based answer'));
  await page.screenshot({ path: shot('05-chat-2') });

  // ---------- 5. Console health ----------
  const realErrors = consoleErrors.filter(
    (e) => !e.includes('favicon') && !e.includes('net:: ERR_ABORTED') && !e.includes('X-Frame'),
  );
  check('no console/page errors', realErrors.length === 0, realErrors.slice(0, 3).join(' || '));

  if (apiPayload) {
    const main = apiPayload.main || {};
    save('growth-plan-response.json', {
      username: main?.profile?.username,
      followers: main?.profile?.followers,
      account_score: main?.account_score,
      ai_summary_preview: (main?.ai_summary || '').slice(0, 300),
      keys: Object.keys(apiPayload),
    });
    check('growth-plan API payload captured', !!main?.profile?.username, `followers=${main?.profile?.followers}`);
  }
} catch (err) {
  check('TEST RUN ABORTED', false, String(err.message).slice(0, 300));
  try { await page.screenshot({ path: shot('99-error') }); } catch { /* noop */ }
}

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n==== ${results.length - failed.length}/${results.length} checks passed ====`);
process.exit(failed.length ? 1 : 0);
