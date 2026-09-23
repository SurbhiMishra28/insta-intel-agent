// Browser-test competitor research through the real React UI:
// home -> analyze @natgeo -> click "Competitor research" deep dive -> ranking renders.
import puppeteer from 'puppeteer-core';

const CHROME = process.env.IG_CHROME_PATH || 'C:/Users/DELL/AppData/Local/Google/Chrome/Application/chrome.exe';
const BASE = 'http://127.0.0.1:5173';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--window-size=1440,2400'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1100, deviceScaleFactor: 1 });
const shot = async (name) => {
  await page.screenshot({ path: `_uitest/${name}.png` });
  console.log('shot:', name);
};

// ---------- 1. Analyze @natgeo through the UI ----------
await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 60000 });
await page.click('.scanner input', { clickCount: 3 });
await page.type('.scanner input', 'natgeo', { delay: 30 });
await page.click('.scanner-submit');
console.log('analyzing @natgeo (cached → fast)...');
await page.waitForSelector('.dash-hero-top h2', { timeout: 420000 });
const handle = await page.$eval('.dash-hero-top h2', (el) => el.textContent.trim());
console.log('dashboard rendered:', handle);
await shot('research-01-dashboard');

// ---------- 2. Run the Competitor research deep dive ----------
await page.evaluate(() => document.querySelector('#research')?.scrollIntoView({ behavior: 'instant', block: 'start' }));
await sleep(600);
const deepCards = await page.$$('.deep-card');
console.log('deep-dive cards found:', deepCards.length);
if (!deepCards.length) { await browser.close(); process.exit(1); }
await deepCards[0].click(); // first card = Competitor research
console.log('clicked: Competitor research — waiting for rival ranking (real fetches, may take a few minutes)...');

// ---------- 3. Wait for the ranking bars ----------
let ok = false;
try {
  await page.waitForSelector('.rank-row', { timeout: 540000 });
  ok = true;
} catch {
  const bodyText = await page.evaluate(() => document.body.innerText.slice(0, 600));
  console.log('TIMEOUT — page shows:', bodyText.replace(/\s+/g, ' '));
  await shot('research-02-timeout');
}

if (ok) {
  await sleep(1500);
  const ranking = await page.$$eval('.rank-row', (els) =>
    els.map((el) => el.textContent.replace(/\s+/g, ' ').trim().slice(0, 70)),
  );
  const summary = await page.$eval('.rank-summary, #research .report-summary', (el) => el.textContent.trim().slice(0, 140)).catch(() => '(no summary el)');
  const readouts = await page.$$eval('.readout .handle', (els) => els.map((el) => el.textContent.trim().slice(0, 40)));
  console.log('--- COMPETITOR RESEARCH RESULT ---');
  console.log('ranking rows:');
  for (const r of ranking) console.log('  ', r);
  console.log('rival readout cards:', JSON.stringify(readouts));
  console.log('market summary:', summary);
  await shot('research-02-result');
  const okFinal = ranking.length >= 2 && /natgeo/i.test(ranking.join(' '));
  console.log(okFinal ? '✅ COMPETITOR RESEARCH UI FLOW PASSED' : '❌ ranking incomplete');
  await browser.close();
  process.exit(okFinal ? 0 : 1);
} else {
  await browser.close();
  process.exit(1);
}
