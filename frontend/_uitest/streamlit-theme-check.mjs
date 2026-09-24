// Browser-verify the Streamlit theme matches the React UI palette.
// Usage: node _uitest/streamlit-theme-check.mjs
import puppeteer from 'puppeteer-core';

const CHROME = process.env.IG_CHROME_PATH || 'C:/Users/DELL/AppData/Local/Google/Chrome/Application/chrome.exe';
const BASE = process.env.UI_URL || 'http://127.0.0.1:8501';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--window-size=1440,2200'],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 1100 });

await page.goto(BASE, { waitUntil: 'networkidle2', timeout: 90000 });
await sleep(4000); // let Streamlit finish its first render

const theme = await page.evaluate(() => {
  const cs = getComputedStyle(document.body);
  const root = getComputedStyle(document.documentElement);
  const val = (el, p) => (el ? el.getPropertyValue(p).trim() : null);
  return {
    bodyBg: getComputedStyle(document.body).backgroundColor,
    stAppBg: val(root, '--background-color') || getComputedStyle(document.querySelector('[data-testid="stApp"]') || document.body).backgroundColor,
    primary: val(root, '--primary-color'),
    text: val(root, '--text-color') || getComputedStyle(document.body).color,
    secondary: val(root, '--secondary-background-color'),
    sidebarBg: (document.querySelector('[data-testid="stSidebar"]'))
      ? getComputedStyle(document.querySelector('[data-testid="stSidebar"]')).backgroundColor
      : null,
  };
});
console.log('theme:', JSON.stringify(theme, null, 2));

const expect = {
  bodyBg: 'rgb(21, 23, 26)',      // #15171A
  primary: '#C6FF4E'.toLowerCase(),
  secondary: 'rgb(29, 32, 35)',   // #1D2023
};
const pass =
  theme.bodyBg === expect.bodyBg &&
  (!theme.primary || theme.primary.toLowerCase() === expect.primary) &&
  (!theme.secondary || theme.secondary === expect.secondary);

await page.screenshot({ path: '_uitest/st-theme-check.png' });
await browser.close();
console.log(pass ? '✅ THEME PASS — dark intelligence palette applied' : '❌ THEME MISMATCH');
process.exit(pass ? 0 : 1);
