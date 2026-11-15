import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
let server, base;
test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); return res.end('<main></main>'); }
    const name = req.url.match(/^\/src\/(evidence|result|history|api|reassessment)\.js$/)?.[1];
    if (!name) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', 'text/javascript');
    res.end(await readFile(new URL(`../src/${name}.js`, import.meta.url)));
  }).listen(0, '127.0.0.1');
  await once(server, 'listening'); base = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => { server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); });
for (const status of ['supported', 'contradicted', 'misleading', 'insufficient_evidence']) {
  test(`inspector ${status} literal text and fallback labels`, async ({ page }) => {
    await page.goto(base);
    await page.evaluate(async (status) => {
      const { normalizeResult } = await import('/src/result.js');
      const { renderEvidenceInspector } = await import('/src/evidence.js');
      const sources = [
        { id: 's1', title: '<img src=x onerror=alert(1)>', url: 'https://example.org/a', evidence_kind: 'fetched_article', passages: [{ id: 's1-p1', kind: 'fetched_article', text: 'The orbit changed.' }] },
        ...['search_snippet', 'social_snippet', 'fact_check_summary', 'unknown'].map((kind, i) => ({ id: `s${i+2}`, title: kind, url: `https://example.org/${i}`, evidence_kind: kind, excerpt: 'A snippet' })),
      ];
      renderEvidenceInspector(document.querySelector('main'), normalizeResult({
        claim: 'The orbit changed.', explanation: 'Measurements show a change [s1].', status, investigation_id: 'i',
        sources, graph_nodes: [], graph_edges: [], claim_source: { quote: 'Detected speech', timestamp_url: 'https://youtube.com/watch?v=test' },
        evidence_quotes: [
          { source_id: 's1', passage_id: 's1-p1', quote: 'orbit changed.', wording_verified: true, explanation: 'The size remains unclear [s1].' },
          { source_id: 's9', quote: 'invented', wording_verified: true },
          { source_id: 's1', passage_id: 's1-p1', quote: 'never happened', wording_verified: true },
        ],
      }));
    }, status);
    await expect(page.locator('main')).toContainText('Wording matched to supplied material; interpretation is not verified.');
    for (const text of ['Search snippet', 'Social search snippet', 'Published fact-check summary', 'Material provenance unknown', 'Model-detected video statement']) await expect(page.locator('main')).toContainText(text);
    await expect(page.locator('main')).not.toContainText('invented');
    await expect(page.locator('main')).not.toContainText('never happened');
    await expect(page.locator('img')).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Open source' }).first()).toHaveAttribute('href', 'https://example.org/a');
  });
}


test('history opens stored video context with authenticated GETs only and clear error states', async ({ page }) => {
  const requests = [];
  let state = 'ok';
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    requests.push({ url: new URL(request.url()).pathname, method: request.method(), key: request.headers()['x-truthtrace-key'] });
    const path = new URL(request.url()).pathname;
    if (state === 'outage') return route.fulfill({ status: 503, json: { error: 'storage_unavailable' } });
    if (path === '/api/analyses') return route.fulfill({ json: { items: state === 'empty' ? [] : [{ analysis_id: 'saved', claim: 'Saved claim', status: 'insufficient_evidence', checked_at: null, storage_backend: 'neon_postgres' }] } });
    if (state === 'missing') return route.fulfill({ status: 404, json: { error: 'analysis_not_found' } });
    return route.fulfill({ json: { investigation_id: 'trace', analysis_id: 'saved', reopened: true,
      claim: 'Saved claim', status: 'insufficient_evidence', explanation: 'Unclear', sources: [], graph_nodes: [], graph_edges: [],
      page_context: { url: 'https://youtube.com/watch?v=abcdefghijk', title: 'Original title', text: 'Original input', video_time_seconds: 90 },
      claim_source: { quote: 'Model detected words' },
    } });
  });
  await page.goto(base);
  await page.evaluate(async () => {
    const { createHistory } = await import('/src/history.js');
    window.historyView = createHistory({ container: document.querySelector('main'),
      getConfig: () => ({ backendUrl: location.origin, apiKey: 'test-key', requestTimeoutMs: 1000 }),
      onOpen: (result, capture) => { window.opened = { result, capture }; },
    });
    await window.historyView.load();
  });
  await expect(page.getByRole('button', { name: /Saved claim/ })).toBeVisible();
  await page.getByRole('button', { name: /Saved claim/ }).click();
  await expect(page.getByRole('status')).toContainText('not a fresh check');
  const opened = await page.evaluate(() => window.opened);
  expect(opened.capture.video_time_seconds).toBe(90);
  expect(opened.result.claim_source.quote).toBe('Model detected words');
  expect(requests.every((r) => r.method === 'GET' && r.key === 'test-key' && r.url.startsWith('/api/analyses'))).toBe(true);
  state = 'missing';
  await page.getByRole('button', { name: /Saved claim/ }).click();
  await expect(page.getByRole('status')).toContainText('was not found');
  expect(await page.evaluate(() => window.opened.result.analysis_id)).toBe('saved');
  state = 'empty'; await page.getByRole('button', { name: 'Refresh history' }).click();
  await expect(page.getByRole('status')).toContainText('No saved investigations yet');
  state = 'outage'; await page.getByRole('button', { name: 'Refresh history' }).click();
  await expect(page.getByRole('status')).toContainText('New checks still work');
});
