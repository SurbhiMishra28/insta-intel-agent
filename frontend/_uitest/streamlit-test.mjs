// Browser-test the Streamlit UI: type a handle, run the analysis, wait for
// the dashboard to render with real data.
import puppeteer from 'puppeteer-core';

const CHROME = process.env.IG_CHROME_PATH || 'C:/Users/DELL/AppData/Local/Google/Chrome/Application/chrome.exe';
const BASE = process.env.UI_URL || 'http://127.0.0.1:8501';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const shot = (page, name) => page.screenshot({ path: `_uitest/${name}.png` }).then(() => console.log('shot:', name));

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--window-size=1440,2200'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1100 });
page.on('pageerror', (e) => console.log('PAGE ERROR:', String(e).slice(0, 120)));

// ---------- 1. App loads ----------
await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 90000 });
await sleep(3000);
await shot(page, 'st-01-home');
console.log('title:', await page.title());

// ---------- 2. Type the handle in the sidebar input ----------
const inputSel = 'input[aria-label="Instagram handle"]';
try {
  await page.waitForSelector(inputSel, { timeout: 60000 });
} catch {
  const diag = await page.evaluate(() => ({
    inputs: Array.from(document.querySelectorAll('input')).map((i) => i.getAttribute('aria-label')),
    body: document.body.innerText.slice(0, 120),
  }));
  console.log('DIAG:', JSON.stringify(diag));
  throw new Error('handle input never appeared');
}
await page.click(inputSel, { clickCount: 3 });
await page.type(inputSel, 'fcbarcelona', { delay: 15 });
await sleep(500);

// ---------- 3. Click "Run analysis" ----------
const clicked = await page.evaluate(() => {
  const btns = Array.from(document.querySelectorAll('[data-testid="stSidebar"] button'));
  const target = btns.find((b) => (b.textContent || '').includes('Run analysis'));
  if (target) { target.click(); return true; }
  return false;
});
console.log('clicked Run analysis:', clicked);
if (!clicked) throw new Error('Run analysis button not found');

// ---------- 4. Wait for the dashboard (title becomes @fcbarcelona) ----------
let outcome = 'timeout';
try {
  await page.waitForFunction(
    () => document.body.innerText.includes('@fcbarcelona')
      && document.body.innerText.includes('Followers'),
    { timeout: 240000 },
  );
  outcome = 'dashboard';
} catch { /* keep timeout */ }
await sleep(2500);
await shot(page, 'st-02-' + outcome);
console.log('outcome:', outcome);

if (outcome === 'dashboard') {
  const text = await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' '));
  const followers = text.match(/Followers\s*([0-9.,]+[KMB]?)/);
  const score = text.match(/Account score\s*([0-9]+)/);
  console.log('followers shown:', followers ? followers[1] : '?');
  console.log('account score  :', score ? score[1] : '?');
}

await browser.close();
console.log('DONE', outcome === 'dashboard' ? 'PASS' : 'FAIL');
process.exit(outcome === 'dashboard' ? 0 : 1);
