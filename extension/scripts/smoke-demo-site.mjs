// Checks the content extractor on the two public demo articles. No backend,
// Bright Data, Gemini, or Fact Check API calls. Does not grant activeTab.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const script = await readFile(new URL('../src/content.js', import.meta.url), 'utf8');
const urls = [
  'https://www.nasa.gov/news-release/nasa-confirms-dart-mission-impact-changed-asteroids-motion-in-space/',
  'https://www.nasa.gov/solar-system/nasa-dart-imagery-shows-changed-orbit-of-target-asteroid/',
];
const browser = await chromium.launch();
try {
  for (const url of urls) {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    const result = await page.evaluate(script);
    assert.ok(result.article.length > 200, 'Readable article text expected');
    assert.match(result.article, /DART/);
    assert.match(result.article, /Dimorphos/);
    await page.evaluate(() => {
      const paragraph = [...document.querySelectorAll('p')].find((p) => p.innerText.includes('Dimorphos') && p.getClientRects().length);
      const range = document.createRange(); range.selectNodeContents(paragraph);
      getSelection().removeAllRanges(); getSelection().addRange(range);
    });
    const selected = await page.evaluate(script);
    assert.match(selected.selection, /Dimorphos/);
    console.log(`PASS: ${new URL(url).pathname} — ${result.article.length} article characters; selection extracted.`);
    await page.close();
  }
} finally { await browser.close(); }
