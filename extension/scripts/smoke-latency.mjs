// Controlled 46-second response through the actual unpacked panel, using the
// shipped deadline. Synthetic payload only; this does not verify live services.
import { chromium, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
const fixture = JSON.parse(await readFile(new URL('../../source_tracing/fixtures/example_investigation_response.json', import.meta.url)));
const config = JSON.parse(await readFile(new URL('../config.json', import.meta.url)));
let context;
const server = createServer(async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-TruthTrace-Key');
  if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
  if (req.url === '/article') { res.setHeader('Content-Type', 'text/html'); return res.end('<title>Controlled latency fixture</title><article><p id="claim">DART changed the orbit of Dimorphos.</p></article>'); }
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/api/health') return res.end('{"status":"ok","mock":true}');
  if (req.url === '/api/extract') return res.end(JSON.stringify({ checkable: true, claim: 'DART changed the orbit of Dimorphos.', confirmation_token: 'synthetic-only', input_context: { capture_kind: 'selection', extraction_method: 'text_model' } }));
  if (req.url === '/api/analyze') {
    await new Promise((resolve) => setTimeout(resolve, 46000));
    return res.end(JSON.stringify({ ...fixture, investigation_id: 'synthetic-latency-only', status: 'supported', claim: 'DART changed the orbit of Dimorphos.', explanation: 'Controlled latency fixture, not live analysis. NASA reports an orbital change [s1].' }));
  }
  res.writeHead(404); res.end('{}');
}).listen(0, '127.0.0.1');
await once(server, 'listening');
try {
  const extension = fileURLToPath(new URL('../', import.meta.url));
  context = await chromium.launchPersistentContext('', { channel: 'chromium', headless: true, args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`] });
  let [worker] = context.serviceWorkers(); if (!worker) worker = await context.waitForEvent('serviceworker');
  const backendUrl = `http://127.0.0.1:${server.address().port}`;
  await worker.evaluate((connection) => chrome.storage.local.set({ connection }), { ...config, backendUrl, apiKey: 'synthetic-only' });
  const article = await context.newPage(); await article.goto(`${backendUrl}/article`);
  await article.evaluate(() => { const range = document.createRange(); range.selectNodeContents(document.querySelector('#claim')); getSelection().removeAllRanges(); getSelection().addRange(range); });
  const panel = await context.newPage(); await panel.goto(`chrome-extension://${new URL(worker.url()).host}/sidepanel.html`);
  await expect(panel.locator('#connection-label')).toContainText('Mock backend');
  await article.bringToFront(); await panel.getByRole('button', { name: 'Check selection', exact: true }).click();
  const start = Date.now();
  await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  await expect(panel.locator('#status-badge')).toHaveText('Supported', { timeout: 55000 });
  await expect(panel.locator('#mock-warning')).toBeVisible();
  console.log(`PASS: synthetic result rendered after ${((Date.now() - start) / 1000).toFixed(1)} seconds with shipped ${config.requestTimeoutMs}ms deadline.`);
} finally {
  await context?.close(); server.closeAllConnections(); await new Promise((resolve) => server.close(resolve));
}
