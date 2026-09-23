// Browser-test the original React UI (vite :5173): home render -> analyze flow -> dashboard with real data.
import puppeteer from 'puppeteer-core';

const HANDLE = process.argv[2] || 'nasa';

const CHROME = process.env.IG_CHROME_PATH || 'C:/Users/DELL/AppData/Local/Google/Chrome/Application/chrome.exe';
const BASE = 'http://127.0.0.1:5173';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--window-size=1440,2200'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1100, deviceScaleFactor: 1 });
const shot = async (name) => {
  await page.screenshot({ path: `_uitest/${name}.png` });
  console.log('shot:', name);
};

// ---------- 1. Home ----------
await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 60000 });
await sleep(1500);
const hero = await page.$eval('header.hero h1, .hero .title, .hero', (el) => el.textContent.slice(0, 80)).catch(() => null);
console.log('hero text:', hero ? hero.replace(/\s+/g, ' ').trim() : 'NOT FOUND');
const hasScanner = await page.$('.scanner input');
console.log('scanner input:', hasScanner ? 'YES' : 'NO');
await shot('react-01-home');

// ---------- 2. Analyze flow ----------
await page.click('.scanner input', { clickCount: 3 });
await page.type('.scanner input', HANDLE, { delay: 30 });
console.log('typed handle:', HANDLE);
await page.click('.scanner-submit');
console.log('clicked: Do everything — waiting for pipeline (real Playwright fetch)...');

try {
  await page.waitForSelector('.dash-hero-top h2', { timeout: 420000 });
} catch {
  const bodyText = await page.evaluate(() => document.body.innerText.slice(0, 400));
  console.log('TIMEOUT — page shows:', bodyText.replace(/\s+/g, ' '));
  await shot('react-02-timeout');
  await browser.close();
  process.exit(1);
}

const handle = await page.$eval('.dash-hero-top h2', (el) => el.textContent.trim());
const badge = await page.$('.dash-hero-top .badge');
const bio = await page.$eval('.dash-hero-bio', (el) => el.textContent.trim().slice(0, 90)).catch(() => '(no bio el)');
const stats = await page.$$eval('.dash-hero .stat-box', (els) =>
  els.slice(0, 4).map((el) => el.textContent.replace(/\s+/g, ' ').trim().slice(0, 40)),
);
const score = await page.$eval('.ring-num', (el) => el.textContent.trim()).catch(() => '(no score ring)');
console.log('--- DASHBOARD ---');
console.log('handle:', handle, '| verified badge:', badge ? 'YES' : 'no');
console.log('bio:', bio);
console.log('stats:', JSON.stringify(stats));
console.log('score ring:', score);
await shot('react-02-dashboard');

const ok = handle.toLowerCase().includes(HANDLE.toLowerCase()) && stats.length >= 2;
console.log(ok ? `✅ UI FLOW PASSED — real data rendered for @${HANDLE}` : '❌ UI flow incomplete');
await browser.close();
process.exit(ok ? 0 : 1);
