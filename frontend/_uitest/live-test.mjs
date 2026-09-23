// Browser-test the LIVE deployment: https://instaiq-ui.vercel.app
// Captures: home, chat drawer + real LLM answer, analysis attempt outcome.
import puppeteer from 'puppeteer-core';

const CHROME = process.env.IG_CHROME_PATH || 'C:/Users/DELL/AppData/Local/Google/Chrome/Application/chrome.exe';
const BASE = 'https://instaiq-ui.vercel.app';
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
await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 90000 });
await sleep(2500);
await shot('live-01-home');

// ---------- 2. Chat drawer on the front page ----------
const fab = await page.$('.chat-fab');
console.log('chat FAB on front page:', fab ? 'YES' : 'NO');
await page.click('.chat-fab');
await sleep(900);
await shot('live-02-chat-drawer');

// ---------- 3. Real message through the live backend ----------
await page.type('.chat-drawer-input textarea', 'Give me one concrete tip to improve engagement', { delay: 10 });
await page.click('.chat-send');
const t0 = Date.now();
try {
  await page.waitForFunction(
    () => document.querySelectorAll('.chat-msg.bot').length >= 1 && !document.querySelector('.chat-bubble.typing'),
    { timeout: 90000 },
  );
  console.log(`chat answered in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  await sleep(600);
  await shot('live-03-chat-answer');
  const ans = await page.$eval('.chat-msg.bot .chat-bubble', (el) => el.textContent.slice(0, 120));
  console.log('answer:', ans.replace(/\s+/g, ' '));
} catch {
  console.log('chat reply timed out');
  await shot('live-03-chat-timeout');
}

// ---------- 4. Analysis attempt (honest behavior on datacenter IPs) ----------
await page.keyboard.press('Escape');
await page.evaluate(() => document.querySelector('.chat-drawer-close')?.click());
await sleep(600);
await page.type('.scanner-row input', 'nike', { delay: 12 });
await page.click('.scanner-submit');
let outcome = 'pending';
try {
  await page.waitForFunction(
    () => document.querySelector('.dash-hero') || document.querySelector('.error-line'),
    { timeout: 180000 },
  );
  outcome = (await page.$('.dash-hero')) ? 'dashboard' : 'error-shown';
} catch {
  outcome = 'still-progressing';
}
await sleep(1500);
await shot('live-04-analysis-' + outcome);
console.log('analysis outcome:', outcome);

await browser.close();
console.log('DONE');
