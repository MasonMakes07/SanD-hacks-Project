import { test, expect, chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const extensionPath = fileURLToPath(new URL('../', import.meta.url));
const contentScript = await readFile(new URL('../src/content.js', import.meta.url), 'utf8');
const fixture = JSON.parse(await readFile(new URL('../../source_tracing/fixtures/example_investigation_response.json', import.meta.url)));
const articleHtml = `<!doctype html><title>NASA DART demo article</title>
  <nav><p>Navigation should not be analyzed.</p></nav>
  <main><article><h1>DART changes an asteroid’s orbit</h1><p id="claim">NASA's DART impact altered Dimorphos's orbit around Didymos.</p>
  <p>Scientists measured the spacecraft’s impact using telescopes.</p>
  <div hidden><p>Hidden private text.</p></div><div style="display:none"><p>Invisible hidden text.</p></div>
  <form><p>Personal form data.</p><input value="a password"></form>
  <footer><p>Footer should not be sent.</p></footer></article></main>`;

let server, baseUrl, requests, behavior, context, panel, article;

test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-TruthTrace-Key');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
    if (req.url === '/article') { res.setHeader('Content-Type', 'text/html'); return res.end(articleHtml); }
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/api/health') return res.end(JSON.stringify({ status: 'ok', mock: false }));
    if (req.url !== '/api/analyze' || req.method !== 'POST') { res.writeHead(404); return res.end('{}'); }
    let body = ''; for await (const chunk of req) body += chunk;
    requests.push({ body: JSON.parse(body || '{}'), key: req.headers['x-truthtrace-key'] });
    if (behavior === 'unauthorized') { res.writeHead(401); return res.end('{"error":"unauthorized","message":"Denied"}'); }
    if (behavior === 'offline') { req.socket.destroy(); return; }
    if (behavior === 'slow') return;
    if (behavior === 'malformed') return res.end('{"status":"supported"}');
    const result = {
      ...structuredClone(fixture), claim: 'DART changed the orbit of Dimorphos.',
      status: 'supported', explanation: 'NASA measured a changed orbit [s1].',
    };
    if (behavior === 'empty') Object.assign(result, { sources: [], graph_nodes: [], graph_edges: [], earliest_observed_source: null, status: 'insufficient_evidence', explanation: 'No sources found.' });
    if (behavior === 'mock') result.investigation_id = 'mock-0000000000000000';
    if (behavior === 'xss') {
      result.claim = '<img src=x onerror="window.pwned=true">';
      result.explanation = '<script>window.pwned=true</script> [s1] [unknown]';
      result.sources.push({ id: 'bad', title: 'Unsafe source', url: 'javascript:alert(1)', excerpt: 'bad' });
      result.graph_edges.push({ source_id: 's1', target_id: 's2', relationship_type: 'explicit_citation', evidence: '' });
    }
    res.end(JSON.stringify(result));
  }).listen(0, '127.0.0.1');
  await once(server, 'listening');
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => { server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); });

test.beforeEach(async () => {
  requests = []; behavior = 'result';
  context = await chromium.launchPersistentContext('', {
    channel: 'chromium', headless: true, viewport: { width: 380, height: 900 },
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`],
  });
  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent('serviceworker');
  const extensionId = new URL(worker.url()).host;
  // Real storage, worker, messaging, content-script injection and fetch. Only
  // upstream analysis is a fixture; no vendor calls or credentials are needed.
  await worker.evaluate(async (backendUrl) => {
    await chrome.storage.local.set({ connection: { backendUrl, apiKey: 'test-only-key', requestTimeoutMs: 1000 } });
  }, baseUrl);
  article = await context.newPage();
  await article.goto(`${baseUrl}/article`);
  panel = await context.newPage();
  await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
  await expect(panel.locator('#connection-label')).toHaveText('Backend connected');
});

test.afterEach(async () => { await context?.close(); });

async function selectClaim() {
  await article.evaluate(() => {
    const range = document.createRange(); range.selectNodeContents(document.getElementById('claim'));
    getSelection().removeAllRanges(); getSelection().addRange(range);
  });
  await article.bringToFront();
}

test('fresh profile loads MV3 and runs selection → worker → backend → safe result/network', async () => {
  const errors = []; panel.on('pageerror', (e) => errors.push(e.message));
  await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  expect(requests[0]).toEqual({ body: { text: "NASA's DART impact altered Dimorphos's orbit around Didymos.", url: `${baseUrl}/article`, mode: 'manual' }, key: 'test-only-key' });
  await expect(panel.locator('#sources .source-card')).toHaveCount(2);
  await expect(panel.locator('#explanation a')).toHaveAttribute('href', fixture.sources[0].url);
  await expect(panel.locator('#network canvas').first()).toBeVisible();
  await panel.locator('#connections summary').click();
  await expect(panel.locator('#edge-list li')).toHaveCount(2);
  await expect(panel.locator('#edge-list')).toContainText('Observed link:');
  await expect(panel.locator('.network-caveat')).toContainText('does not prove copying');
  await expect(panel.locator('#earliest')).toContainText('Oct 11, 2022');
  await expect(panel.locator('#mock-warning')).toBeHidden();
  expect(errors).toEqual([]);
  expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await panel.screenshot({ path: 'test-results/result-panel.png', fullPage: true });
  await panel.setViewportSize({ width: 280, height: 800 });
  expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await context.route(fixture.sources[0].url, (route) => route.fulfill({ contentType: 'text/html', body: '<p>Source link destination</p>' }));
  const popupPromise = panel.waitForEvent('popup');
  await panel.locator('#sources a').first().click();
  const popup = await popupPromise;
  await popup.waitForLoadState();
  expect(popup.url()).toBe(fixture.sources[0].url);
  await popup.close();
});

test('Analyze page reads visible article text and excludes forms, hidden text and navigation', async () => {
  await article.bringToFront();
  await panel.getByRole('button', { name: 'Analyze page', exact: true }).click();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  const text = requests[0].body.text;
  expect(text).toContain('DART changes');
  expect(text).toContain('Scientists measured');
  expect(text).not.toMatch(/Navigation|Hidden private|Invisible hidden|Personal form|Footer|password/);
});

test('empty selection shows an actionable error and sends no request', async () => {
  await article.bringToFront();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#error')).toContainText('Select a factual sentence');
  expect(requests).toHaveLength(0);
});

test('empty evidence stays inconclusive, with no edges or invented earliest publisher', async () => {
  behavior = 'empty'; await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#status-badge')).toHaveText('Insufficient evidence');
  await expect(panel.locator('#no-sources')).toBeVisible();
  await expect(panel.locator('#no-edges')).toBeVisible();
  await expect(panel.locator('#earliest')).toContainText('No source with a reliable publication date');
});

test('offline, authentication and malformed-response errors are distinct and retryable', async () => {
  await selectClaim();
  for (const [mode, message] of [['offline', 'Cannot reach the backend'], ['unauthorized', 'backend rejected the API key'], ['malformed', 'does not match the API contract']]) {
    behavior = mode;
    await panel.getByRole('button', { name: 'Check selection' }).click();
    await expect(panel.locator('#error')).toContainText(message);
    await expect(panel.locator('#loading')).toBeHidden();
    await expect(panel.locator('#results')).toBeHidden();
  }
  behavior = 'result';
  await panel.getByRole('button', { name: 'Try again' }).click();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
});

test('loading can be cancelled, and timeout restores the buttons', async () => {
  behavior = 'slow'; await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#loading')).toBeVisible();
  await panel.getByRole('button', { name: 'Cancel check' }).click();
  await expect(panel.locator('#loading')).toBeHidden();
  await expect(panel.locator('#empty')).toBeVisible();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#error')).toContainText('took too long');
  await expect(panel.getByRole('button', { name: 'Check selection' })).toBeEnabled();
});

test('renders model text literally, removes unsafe links and omits unevidenced edges', async () => {
  behavior = 'xss'; await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#claim')).toContainText('<img');
  await expect(panel.locator('#claim img, #explanation script')).toHaveCount(0);
  expect(await panel.evaluate(() => window.pwned)).toBeUndefined();
  await expect(panel.locator('#sources .source-card')).toHaveCount(2);
  await expect(panel.locator('#edge-list li')).toHaveCount(2);
  await expect(panel.locator('#explanation a')).toHaveCount(1);
});

test('mock results are explicitly labelled as synthetic', async () => {
  behavior = 'mock'; await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#mock-warning')).toBeVisible();
  await expect(panel.locator('#mock-warning')).toContainText('not an investigation of your selected text');
});

test('content extraction has no body fallback, ignores editor selections and reports truncation', async () => {
  await article.setContent('<body><p>Unrelated body text.</p><input value="private"><div contenteditable="true">Secret editor draft.</div></body>');
  expect((await article.evaluate(contentScript)).article).toBe('');
  await article.evaluate(() => {
    const range = document.createRange(); range.selectNodeContents(document.querySelector('[contenteditable]'));
    getSelection().removeAllRanges(); getSelection().addRange(range);
  });
  expect((await article.evaluate(contentScript)).selection).toBe('');
  await article.setContent('<article><p id="mixed">Public statement. <span contenteditable="true">Secret inline draft.</span><span style="display:none">Invisible statement.</span> More public text.</p></article>');
  await article.evaluate(() => {
    const range = document.createRange(); range.selectNodeContents(document.getElementById('mixed'));
    getSelection().removeAllRanges(); getSelection().addRange(range);
  });
  const mixed = await article.evaluate(contentScript);
  expect(mixed.article).toContain('Public statement.');
  expect(mixed.selection).toContain('More public text.');
  expect(mixed.article + mixed.selection).not.toMatch(/Secret inline|Invisible statement/);
  await article.setContent(`<article><p>${'A factual sentence. '.repeat(2000)}</p></article>`);
  expect((await article.evaluate(contentScript)).article.length).toBe(20001);
});
