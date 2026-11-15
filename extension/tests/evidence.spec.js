import { test, expect } from '@playwright/test';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
let server, base;
test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); return res.end('<main></main>'); }
    const name = req.url.match(/^\/src\/(evidence|result)\.js$/)?.[1];
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
