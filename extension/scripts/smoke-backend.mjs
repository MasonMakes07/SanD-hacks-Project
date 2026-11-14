// Exercises the real FastAPI middleware + mock route with the unpacked extension.
// Only localhost is contacted; external service keys are explicitly cleared.
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';

const python = process.env.TRUTHTRACE_TEST_PYTHON || 'python3';
const key = randomUUID();
const fixtureServer = createServer((req, res) => {
  res.setHeader('Content-Type', 'text/html');
  res.end('<!doctype html><title>Local smoke article</title><article><p id="claim">The city cut its library budget by 40% this year.</p></article>');
}).listen(0, '127.0.0.1');
await once(fixtureServer, 'listening');
// Ask the OS for a free port without interfering with the user's demo backend.
const reserve = createServer().listen(0, '127.0.0.1');
await once(reserve, 'listening');
const backendPort = reserve.address().port;
await new Promise((resolve) => reserve.close(resolve));
const backendUrl = `http://127.0.0.1:${backendPort}`;
const backend = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(backendPort)], {
  cwd: fileURLToPath(new URL('../../backend/', import.meta.url)),
  env: { ...process.env, USE_MOCK: 'true', TRUTHTRACE_API_KEY: key, GEMINI_API_KEY: '', FACTCHECK_API_KEY: '', BRIGHTDATA_API_KEY: '', DATABASE_URL: '' },
  stdio: ['ignore', 'ignore', 'pipe'],
});
let backendErrors = '';
backend.stderr.on('data', (chunk) => { backendErrors += chunk; });
backend.on('error', (error) => { backendErrors += error.message; });
let context;
try {
  await expect.poll(async () => {
    try { return (await fetch(`${backendUrl}/api/health`)).status; } catch { return 0; }
  }, { timeout: 10000 }).toBe(200);
  const extension = fileURLToPath(new URL('../', import.meta.url));
  context = await chromium.launchPersistentContext('', {
    channel: 'chromium', headless: true,
    args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
  });
  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent('serviceworker');
  await worker.evaluate(async (connection) => { await chrome.storage.local.set({ connection }); }, { backendUrl, apiKey: key, requestTimeoutMs: 45000 });
  const article = await context.newPage();
  await article.goto(`http://127.0.0.1:${fixtureServer.address().port}/article`);
  await article.evaluate(() => {
    const range = document.createRange(); range.selectNodeContents(document.getElementById('claim'));
    getSelection().removeAllRanges(); getSelection().addRange(range);
  });
  const panel = await context.newPage();
  await panel.goto(`chrome-extension://${new URL(worker.url()).host}/sidepanel.html`);
  await expect(panel.locator('#connection-label')).toHaveText('Mock backend · synthetic data');
  await article.bringToFront();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#status-badge')).toHaveText('Contradicted');
  await expect(panel.locator('#mock-warning')).toBeVisible();
  await expect(panel.locator('#sources .source-card')).toHaveCount(3);
  await expect(panel.locator('#explanation a')).toHaveCount(2);
  await expect(panel.locator('#network canvas').first()).toBeVisible();
  console.log('PASS: unpacked extension → actual authenticated FastAPI mock route → citations, sources, and network.');
  backend.kill('SIGTERM');
  await once(backend, 'exit');
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#error')).toContainText('Cannot reach the backend');
  console.log('PASS: stopping the real backend produces the actionable offline error.');
} catch (error) {
  console.error(backendErrors.replaceAll(key, '[redacted]'));
  throw error;
} finally {
  await context?.close();
  if (backend.exitCode === null) backend.kill('SIGTERM');
  fixtureServer.closeAllConnections();
  await new Promise((resolve) => fixtureServer.close(resolve));
}
