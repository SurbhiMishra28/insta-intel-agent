// Full browser UI test: dashboard + OpenRouter AI summaries visible.
// Flow: home -> analyze @<handle> -> dashboard -> AI report (LLM verdict,
// recommendations, strengths/weaknesses) -> chat drawer (LLM answer bubble
// WITHOUT the "rule-based" fallback note).
import puppeteer from 'puppeteer-core';

const CHROME = process.env.IG_CHROME_PATH || 'C:/Users/DELL/AppData/Local/Google/Chrome/Application/chrome.exe';
const BASE = 'http://127.0.0.1:5173';
const HANDLE = process.argv[2] || 'adidas';
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

let failures = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? '✅' : '❌'} ${name}${detail ? ` — ${detail}` : ''}`);
  if (!ok) failures += 1;
};

// ---------- 1. Home ----------
await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 60000 });
check('home renders', !!(await page.$('.scanner input')));

// ---------- 2. Analyze ----------
await page.click('.scanner input', { clickCount: 3 });
await page.type('.scanner input', HANDLE, { delay: 30 });
await page.click('.scanner-submit');
console.log(`analyzing @${HANDLE} (full pipeline + LLM chains)...`);
await page.waitForSelector('.dash-hero-top h2', { timeout: 420000 });
const heroHandle = await page.$eval('.dash-hero-top h2', (el) => el.textContent.trim());
check('dashboard renders', heroHandle.toLowerCase().includes(HANDLE.toLowerCase()), heroHandle);
const stats = await page.$$eval('.dash-hero .stat-box', (els) =>
  els.slice(0, 4).map((el) => el.textContent.replace(/\s+/g, ' ').trim()),
);
check('real metrics visible', stats.length >= 3, stats.join(' | '));
await shot('ai-01-dashboard');

// ---------- 3. AI report (OpenRouter LLM output) ----------
await page.evaluate(() => document.querySelector('#report')?.scrollIntoView({ behavior: 'instant', block: 'start' }));
await sleep(800);
const verdict = await page.$eval('#report .report-summary', (el) => el.textContent.trim()).catch(() => '');
check('AI verdict rendered', verdict.length > 40, verdict.slice(0, 140) + '…');
const recs = await page.$$eval('#report .rec-list li', (els) =>
  els.map((el) => el.textContent.replace(/\s+/g, ' ').trim().slice(0, 80)),
);
check('recommendations rendered', recs.length >= 2, `${recs.length} numbered items`);
const strengths = await page.$$eval('#report .finding-col.strengths .finding-list li', (els) => els.length).catch(() => 0);
const weaknesses = await page.$$eval('#report .finding-col.weaknesses .finding-list li', (els) => els.length).catch(() => 0);
check('strengths & weaknesses rendered', strengths >= 1 && weaknesses >= 1, `${strengths}▲ / ${weaknesses}▼`);
await shot('ai-02-report');

// ---------- 4. Chat drawer: LLM answer without the fallback note ----------
await page.click('.chat-fab');
await sleep(900);
await page.type('.chat-drawer-input input, .chat-drawer-input textarea', 'give me one quick growth tip', { delay: 20 });
await page.click('.chat-send');
console.log('chat question sent — waiting for the LLM answer...');
try {
  await page.waitForFunction(
    () => {
      const bubbles = document.querySelectorAll('.chat-msg.bot .chat-bubble');
      const last = bubbles[bubbles.length - 1];
      return last && !last.classList.contains('typing') && last.textContent.trim().length > 10;
    },
    { timeout: 120000 },
  );
} catch {
  /* fall through to inspection below */
}
await sleep(1200);
const chatBubbles = await page.$$eval('.chat-msg.bot .chat-bubble', (els) =>
  els.map((el) => ({ text: el.textContent.trim(), fallback: !!el.querySelector('.chat-llm-note') })),
).catch(() => []);
const lastBot = chatBubbles.filter((b) => b.text && !b.text.includes('Ask anything')).pop() || {};
check('chat answered', (lastBot.text || '').length > 15, (lastBot.text || '').slice(0, 120) + '…');
check('answer is LLM (no rule-based note)', lastBot.fallback === false, lastBot.fallback ? 'fallback note present' : 'clean LLM bubble');
await shot('ai-03-chat');

console.log(failures === 0 ? `\n✅ ALL CHECKS PASSED — OpenRouter AI summaries visible for @${HANDLE}` : `\n❌ ${failures} check(s) failed`);
await browser.close();
process.exit(failures === 0 ? 0 : 1);
