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
  await propose(); await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
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
