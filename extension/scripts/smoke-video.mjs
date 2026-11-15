// Live YouTube player/content capture only, separately from model API smoke.
// Does not grant activeTab or claim to exercise the toolbar permission gesture.
import { chromium } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const script = await readFile(new URL('../src/content.js', import.meta.url), 'utf8');
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto('https://www.youtube.com/watch?v=N-q2z4N_Rsk&t=141s', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.locator('video').first().waitFor({ state: 'visible', timeout: 20000 });
  await page.waitForFunction(() => { const video = document.querySelector('video'); return video?.readyState > 0 && Number.isFinite(video.currentTime) && !video.closest('.ad-showing'); }, { timeout: 20000 });
  await page.locator('video').first().evaluate((video) => { video.pause(); video.currentTime = 141; });
  await page.waitForFunction(() => Math.abs(document.querySelector('video').currentTime - 141) < 1, { timeout: 10000 });
  const capture = await page.evaluate(script);
  assert(capture.video, capture.videoError);
  assert(Math.abs(capture.video.video_time_seconds - 141) < 1);
  console.log(JSON.stringify({ synthetic: false, scope: 'live YouTube player/content capture; toolbar grant unverified', title: capture.title, ...capture.video }));
} finally { await browser.close(); }
