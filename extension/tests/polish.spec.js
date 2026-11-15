import { test, expect, chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const extensionPath = fileURLToPath(new URL('../', import.meta.url));
const fixture = JSON.parse(await readFile(new URL('../../source_tracing/fixtures/example_investigation_response.json', import.meta.url)));
let server, base, context, panel, article, status, fail, hold, replies, calls;
const claim = 'SYNTHETIC: A fictional report describes an orbit change.';

function response() {
  const result = { ...structuredClone(fixture), investigation_id: 'synthetic-polish', analysis_id: `synthetic-${calls.length}`,
    claim, status, explanation: 'This synthetic finding preserves uncertainty and source identifiers [s1].', cited_source_ids: ['s1'] };
  result.sources[0].title = '[SYNTHETIC] A long source title about the measurements and their unresolved limitations '.repeat(4);
  result.sources[0].url = `https://example.org/report/${'observed-material-'.repeat(25)}`;
  result.graph_nodes[0] = { ...result.graph_nodes[0], title: result.sources[0].title, url: result.sources[0].url };
  result.earliest_observed_source = result.sources[0];
  return result;
}

test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-TruthTrace-Key');
    if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
    if (req.url === '/article') { res.setHeader('Content-Type', 'text/html'); return res.end(`<title>Synthetic polish article</title><article><p id="claim">${claim}</p></article>`); }
    res.setHeader('Content-Type', 'application/json');
    calls.push({ path: req.url, method: req.method });
    if (req.url === '/api/health') return res.end('{"status":"ok","mock":false}');
    if (req.url.startsWith('/api/analyses')) return res.end('{"items":[]}');
    if (req.url === '/api/evidence/search') return res.end(JSON.stringify({
      mode: 'more', evidence_set_id: 'a'.repeat(32), retrieved_at: '2026-11-15T08:00:00Z', from_cache: false,
      sources: [{ id: 'm1', title: '[SYNTHETIC] Added evidence', url: 'https://example.org/added', excerpt: 'A synthetic snippet.', evidence_kind: 'search_snippet' }],
      graph_nodes: [{ id: 'm1', title: '[SYNTHETIC] Added evidence', url: 'https://example.org/added' }], graph_edges: [], already_known: 0,
    }));
    if (req.url === '/api/chat') return res.end(JSON.stringify({ answer: 'Synthetic additional material remains inconclusive [m1].',
      cited_source_ids: ['m1'], stances: [], grounded: true, basis: 'evidence' }));
    if (req.url === '/api/extract') return res.end(JSON.stringify({ checkable: true, claim, confirmation_token: 'synthetic-token' }));
    if (req.url === '/api/analyze') {
      if (fail) { res.writeHead(503); return res.end('{"error":"unavailable"}'); }
      const result = response();
      const finish = () => res.end(JSON.stringify(result));
      if (hold) replies.push(finish); else finish();
      return;
    }
    res.writeHead(404); res.end('{}');
  }).listen(0, '127.0.0.1');
  await once(server, 'listening'); base = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => { server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); });
test.beforeEach(async () => {
  status = 'supported'; fail = false; hold = false; replies = []; calls = [];
  context = await chromium.launchPersistentContext('', { channel: 'chromium', headless: !process.env.POLISH_HEADED,
    viewport: { width: 380, height: 800 }, args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`] });
  let [worker] = context.serviceWorkers(); if (!worker) worker = await context.waitForEvent('serviceworker');
  await worker.evaluate(async (backendUrl) => chrome.storage.local.set({ connection: { backendUrl, apiKey: 'synthetic-only-key', requestTimeoutMs: 100000 } }), base);
  article = await context.newPage(); await article.goto(`${base}/article`);
  panel = await context.newPage(); await panel.goto(`chrome-extension://${new URL(worker.url()).host}/sidepanel.html`);
  await expect(panel.locator('#connection-label')).toHaveText('Backend connected');
});
test.afterEach(async () => { for (const reply of replies) reply(); await context.close(); });

async function propose() {
  await article.bringToFront();
  await article.evaluate(() => { const range = document.createRange(); range.selectNodeContents(document.querySelector('#claim')); getSelection().removeAllRanges(); getSelection().addRange(range); });
  if (await panel.locator('#start-controls').isHidden()) await panel.getByRole('button', { name: 'New check', exact: true }).click();
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click();
  await expect(panel.locator('#confirmation')).toBeVisible();
}
async function complete() {
  const previous = calls.filter((call) => call.path === '/api/analyze').length;
  await propose(); await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  await expect.poll(() => calls.filter((call) => call.path === '/api/analyze').length).toBe(previous + 1);
  await expect(panel.locator('#loading')).toBeHidden();
  await expect(panel.locator('#results')).toBeVisible();
}

for (const width of [280, 380]) for (const finding of ['supported', 'contradicted', 'misleading', 'insufficient_evidence']) {
  test(`reading hierarchy wraps long evidence at ${width}px: ${finding}`, async () => {
    status = finding; await panel.setViewportSize({ width, height: 800 }); await complete();
    await expect(panel.locator('#mock-warning')).toBeVisible();
    await expect(panel.locator('#status-badge')).toHaveText(finding.replaceAll('_', ' '), { ignoreCase: true });
    const type = await panel.evaluate(() => {
      const selectors = ['.source-meta', '.source-host', '.network-caveat', '.legend', '.investigation-id', 'footer', '.reading-context summary'];
      return selectors.map((selector) => ({ selector, size: parseFloat(getComputedStyle(document.querySelector(selector)).fontSize) }));
    });
    expect(type.every((item) => item.size >= 12)).toBe(true);
    const contrast = await panel.evaluate(() => {
      const rgb = (color) => color.match(/[\d.]+/g).slice(0, 3).map(Number);
      const luminance = (color) => rgb(color).map((v) => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
        .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
      return ['.privacy-note', '.source-meta', '.source-host', '.network-caveat', '.legend em', '.reading-context summary', '#status-badge', 'footer'].map((selector) => {
        const element = document.querySelector(selector), color = getComputedStyle(element).color;
        let background = element;
        while (background.parentElement && getComputedStyle(background).backgroundColor === 'rgba(0, 0, 0, 0)') background = background.parentElement;
        const values = [luminance(color), luminance(getComputedStyle(background).backgroundColor)].sort((a, b) => b - a);
        return { selector, ratio: (values[0] + .05) / (values[1] + .05) };
      });
    });
    expect(contrast.every((item) => item.ratio >= 4.5), JSON.stringify(contrast)).toBe(true);
    expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await panel.locator('.result-card').screenshot({ path: `test-results/polish-finding-${width}-${finding}.png` });
    await panel.locator('#sources .text-button').first().click();
    await expect(panel.locator('#edge-detail')).toContainText('observed-material-');
    expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(panel.locator('.network-caveat')).toContainText('does not prove copying');
    await panel.screenshot({ path: `test-results/polish-reading-${width}-${finding}.png`, fullPage: false });
  });
}

test('empty, pending and error surfaces remain legible and preserve a completed finding', async () => {
  await panel.screenshot({ path: 'test-results/polish-empty-380.png' });
  await complete();
  hold = true; await propose(); await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  await expect(panel.locator('#loading')).toBeVisible();
  await panel.screenshot({ path: 'test-results/polish-loading-380.png' });
  await panel.getByRole('button', { name: 'Cancel check', exact: true }).click();
  await expect(panel.locator('#results')).toBeVisible();
  hold = false; fail = true;
  await panel.getByRole('button', { name: 'Retry original capture', exact: true }).click();
  await expect(panel.locator('#error')).toBeVisible();
  await expect(panel.locator('#claim')).toHaveText(claim);
  await panel.screenshot({ path: 'test-results/polish-error-380.png' });
  expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('pointer, press, disabled and keyboard states keep handlers immediate and layout stable', async () => {
  const start = panel.locator('#check-selection'), settings = panel.locator('#settings-toggle');
  const siblingTop = await panel.locator('#analyze-page').evaluate((el) => el.offsetTop);
  await start.hover();
  expect(await start.evaluate((el) => getComputedStyle(el).transitionDelay)).toBe('0.08s');
  await expect.poll(() => start.evaluate((el) => getComputedStyle(el).transform)).not.toBe('none');
  await panel.mouse.down();
  expect(await start.evaluate((el) => getComputedStyle(el).transitionDelay)).toBe('0s');
  await expect.poll(() => start.evaluate((el) => getComputedStyle(el).boxShadow)).toContain('inset');
  await panel.mouse.move(1, 1); await panel.mouse.up();
  await expect.poll(() => start.evaluate((el) => getComputedStyle(el).transform)).toBe('none');
  expect(await panel.locator('#analyze-page').evaluate((el) => el.offsetTop)).toBe(siblingTop);
  await settings.focus(); await panel.keyboard.press('Enter');
  await expect(panel.locator('#settings')).toBeVisible();
  await panel.locator('#backend-url').focus(); await panel.keyboard.press('Tab'); await panel.keyboard.press('Tab');
  const save = panel.getByRole('button', { name: 'Save connection', exact: true });
  await expect(save).toBeFocused();
  expect(await save.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe('solid');
  await panel.keyboard.press('Enter'); await expect(panel.locator('#settings-message')).toHaveText('Connection saved.');
  await settings.click();
  hold = true; await propose(); await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  await expect.poll(() => calls.filter((c) => c.path === '/api/analyze').length).toBe(1);
  await expect(start).toBeDisabled();
  // Inspect a disabled visible action without modifying any handler.
  await panel.getByRole('button', { name: 'Cancel check', exact: true }).focus();
  await panel.keyboard.press('Enter');
  await expect(panel.locator('#error')).toBeVisible();
  expect(calls.filter((c) => c.path === '/api/analyze')).toHaveLength(1);
  const summary = panel.locator('details.history-section > summary');
  await summary.focus(); await panel.keyboard.press('Enter');
  await expect(panel.locator('details.history-section')).toHaveAttribute('open', '');
  hold = false; await complete();
  const reset = panel.locator('#reset-selection');
  await expect(reset).toBeDisabled(); await reset.hover();
  expect(await reset.evaluate((el) => getComputedStyle(el).transform)).toBe('none');
  expect(await reset.evaluate((el) => getComputedStyle(el).boxShadow)).toBe('none');
});

test('reduced motion removes control transforms while focus, pressed and disabled cues remain', async () => {
  await panel.emulateMedia({ reducedMotion: 'reduce' });
  const button = panel.locator('#settings-toggle');
  await button.hover(); await panel.mouse.down();
  expect(await button.evaluate((el) => getComputedStyle(el).transform)).toBe('none');
  expect(await button.evaluate((el) => getComputedStyle(el).transitionDuration)).toBe('0s');
  expect(await button.evaluate((el) => getComputedStyle(el).boxShadow)).toContain('inset');
  await panel.mouse.move(1, 1); await panel.mouse.up();
  await button.focus(); await panel.keyboard.press('Tab'); await panel.keyboard.press('Shift+Tab');
  expect(await button.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe('solid');
  // Native disabled semantics remain authoritative during an observed request.
  hold = true; await propose(); await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  const disabled = panel.locator('#check-selection');
  expect(await disabled.evaluate((el) => getComputedStyle(el).transform)).toBe('none');
  expect(await disabled.evaluate((el) => getComputedStyle(el).boxShadow)).toBe('none');
  await panel.getByRole('button', { name: 'Cancel check', exact: true }).click();
  await expect(panel.locator('#loading')).toBeHidden();
});

async function trackArrival() {
  await panel.evaluate(() => {
    window.arrivals = [];
    const original = Element.prototype.animate;
    Element.prototype.animate = function (frames, options) {
      if (options?.id?.startsWith('truthtrace-arrival-')) window.arrivals.push({
        id: options.id, duration: options.duration, delay: options.delay || 0, frames,
      });
      return original.call(this, frames, options);
    };
  });
}
async function arrivalCount() { return panel.evaluate(() => window.arrivals.filter((a) => a.id === 'truthtrace-arrival-emphasis').length); }
async function activeArrivals() { return panel.evaluate(() => document.getAnimations().filter((a) => a.id.startsWith('truthtrace-arrival-')).length); }

for (const finding of ['supported', 'contradicted', 'misleading', 'insufficient_evidence']) {
  test(`one bounded arrival on ${finding}, with warnings and evidence immediately usable`, async () => {
    status = finding; await trackArrival(); await complete();
    expect(await arrivalCount()).toBe(1);
    const effects = await panel.evaluate(() => window.arrivals);
    expect(Math.max(...effects.map((a) => a.duration + a.delay))).toBeGreaterThanOrEqual(250);
    expect(Math.max(...effects.map((a) => a.duration + a.delay))).toBeLessThanOrEqual(400);
    expect(effects.filter((a) => a.frames.some((f) => f.opacity !== undefined)).every((a) => a.frames.every((f) => f.opacity >= .9))).toBe(true);
    expect(await panel.locator('#mock-warning').evaluate((el) => getComputedStyle(el).opacity)).toBe('1');
    await panel.locator('details.evidence-inspection > summary').click();
    await expect(panel.locator('#evidence-inspector')).toBeVisible();
    await panel.locator('#sources .text-button').first().click();
    await panel.setViewportSize({ width: 280, height: 800 });
    expect(await arrivalCount()).toBe(1);
    await expect.poll(activeArrivals).toBe(0);
    await expect(panel.locator('.is-arrival-emphasis')).toHaveCount(0);
    const geometry = await panel.locator('#network').evaluate((el) => ({ width: el.clientWidth, height: el.clientHeight }));
    expect(geometry.width).toBeGreaterThan(150); expect(geometry.height).toBeGreaterThan(250);
    expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('arrival cancels cleanly across new checks, late cancelled replies, failure and retry', async () => {
  await trackArrival(); await complete();
  const firstCount = await arrivalCount();
  hold = true; await propose(); await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  await expect.poll(() => replies.length).toBe(1);
  expect(await activeArrivals()).toBe(0);
  await panel.getByRole('button', { name: 'Cancel check', exact: true }).click();
  replies.shift()();
  expect(await arrivalCount()).toBe(firstCount);
  await expect(panel.locator('.is-arrival-emphasis')).toHaveCount(0);
  hold = false; fail = true;
  await panel.getByRole('button', { name: 'Retry original capture', exact: true }).click();
  await expect(panel.locator('#error')).toBeVisible();
  expect(await arrivalCount()).toBe(firstCount);
  fail = false;
  await panel.getByRole('button', { name: 'Retry original capture', exact: true }).click();
  await expect.poll(arrivalCount).toBe(firstCount + 1);
  await expect.poll(activeArrivals).toBe(0);
  await expect(panel.locator('#claim')).toHaveText(claim);
  await expect(panel.locator('.is-arrival-emphasis')).toHaveCount(0);
});

test('reduced-motion arrival is static; preference changes cancel running motion', async () => {
  await trackArrival(); await panel.emulateMedia({ reducedMotion: 'reduce' }); await complete();
  expect(await arrivalCount()).toBe(0); expect(await activeArrivals()).toBe(0);
  await expect(panel.locator('.result-card')).toHaveClass(/is-arrival-static/);
  expect(await panel.locator('.result-card').evaluate((el) => getComputedStyle(el).outlineStyle)).toBe('solid');
  await panel.emulateMedia({ reducedMotion: 'no-preference' });
  await complete(); await expect.poll(arrivalCount).toBe(1);
  await panel.evaluate(() => document.getAnimations().filter((a) => a.id.startsWith('truthtrace-arrival-')).forEach((a) => a.pause()));
  await panel.emulateMedia({ reducedMotion: 'reduce' });
  await expect.poll(activeArrivals).toBe(0);
  await expect(panel.locator('.result-card')).toHaveClass(/is-arrival-static/);
  await expect(panel.locator('.is-arrival-emphasis')).toHaveCount(0);
});


test('pinning extra evidence preserves the existing finding and does not replay arrival', async () => {
  await trackArrival(); await complete();
  expect(await arrivalCount()).toBe(1);
  await panel.getByRole('button', { name: 'Search for more evidence' }).click();
  const pin = panel.getByRole('button', { name: /Pin m1/ });
  await expect(pin).toBeEnabled(); await pin.click();
  await expect(panel.locator('#sources .source-card')).toHaveCount(3);
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  expect(await arrivalCount()).toBe(1);
  expect(calls.filter((call) => call.path === '/api/analyze')).toHaveLength(1);
});
