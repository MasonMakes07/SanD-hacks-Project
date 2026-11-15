import { test, expect, chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Ask the Detective, against a local stand-in backend. No Gemini or Bright
// Data calls are made; the fixture is the checked-in NASA investigation.
const extensionPath = fileURLToPath(new URL('../', import.meta.url));
const fixture = JSON.parse(await readFile(new URL('../../source_tracing/fixtures/example_investigation_response.json', import.meta.url)));
const NEW_URL = 'https://rebuttal.example/analysis';

let server, baseUrl, chats, searches, chatBehavior, searchBehavior, context, panel, article;

test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-TruthTrace-Key');
    if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
    if (req.url === '/article') { res.setHeader('Content-Type', 'text/html'); return res.end('<title>Demo</title><main><article><p id="claim">NASA\'s DART impact altered Dimorphos\'s orbit around Didymos.</p></article></main>'); }
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/api/health') return res.end('{"status":"ok","mock":false}');
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw || '{}');
    const key = req.headers['x-truthtrace-key'];
    if (req.url === '/api/analyze') return res.end(JSON.stringify({ ...structuredClone(fixture), claim: 'DART changed the orbit of Dimorphos.', status: 'supported', explanation: 'NASA measured a changed orbit [s1].' }));
    if (req.url === '/api/chat') {
      chats.push({ body, key });
      // Match the backend's strict legacy ChatSource schema. Rich evidence stays
      // on the board/receipt and must not leak extra fields into this request.
      const fields = new Set(['id', 'title', 'url', 'date', 'excerpt']);
      if (body.sources.some((s) => Object.keys(s).some((key) => !fields.has(key)))) {
        res.writeHead(422); return res.end('{"error":"invalid_request"}');
      }
      if (chatBehavior === 'down') { res.writeHead(502); return res.end('{"error":"chat_unavailable","message":"x"}'); }
      if (chatBehavior === 'uncited') return res.end(JSON.stringify({ answer: 'Nothing on the board settles this.', cited_source_ids: [], stances: [], grounded: false, basis: 'evidence' }));
      if (chatBehavior === 'background') return res.end(JSON.stringify({ answer: 'He is a member of Congress.', cited_source_ids: [], stances: [], grounded: false, basis: 'background' }));
      const cited = body.sources.some((s) => s.id === 'c1') ? 'c1' : 's1';
      return res.end(JSON.stringify({
        answer: `<b>NASA</b> measured the change [s1]. A second page repeats it [s2]. One more point [${cited}] and an invented one [z9].`,
        cited_source_ids: ['s1', 's2', cited],
        stances: [{ source_id: 's1', stance: 'supports' }, { source_id: cited, stance: cited === 'c1' ? 'contradicts' : 'supports' }, { source_id: 'z9', stance: 'contradicts' }],
        grounded: true, basis: 'evidence',
      }));
    }
    if (req.url === '/api/evidence/search') {
      searches.push({ body, key });
      if (searchBehavior === 'failed') { res.writeHead(502); return res.end('{"error":"search_failed","message":"x"}'); }
      if (searchBehavior === 'nothing') return res.end(JSON.stringify({ mode: body.mode, retrieved_at: '2026-11-14T20:00:00+00:00', from_cache: true, sources: [], graph_nodes: [], graph_edges: [], already_known: 2 }));
      return res.end(JSON.stringify({
        mode: body.mode, retrieved_at: '2026-11-14T20:00:00+00:00', from_cache: false, already_known: 1,
        sources: [
          { id: 'c1', title: 'A rebuttal of the orbit claim', url: NEW_URL, date: '2022-11-01', excerpt: 'The measured change is disputed.' },
          { id: 'c2', title: 'Unsafe', url: 'javascript:alert(1)', date: null, excerpt: '' },
        ],
        graph_nodes: [{ id: 'c1', title: 'A rebuttal of the orbit claim', url: NEW_URL, kind: 'article' }],
        graph_edges: [{ source_id: 'c1', target_id: 's1', relationship_type: 'explicit_citation', evidence: 'The rebuttal links to the NASA release.' }],
      }));
    }
    res.writeHead(404); res.end('{}');
  }).listen(0, '127.0.0.1');
  await once(server, 'listening');
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => { server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); });

test.beforeEach(async () => {
  chats = []; searches = []; chatBehavior = 'ok'; searchBehavior = 'ok';
  context = await chromium.launchPersistentContext('', {
    channel: 'chromium', headless: true, viewport: { width: 380, height: 900 },
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`],
  });
  let [worker] = context.serviceWorkers();
  if (!worker) worker = await context.waitForEvent('serviceworker');
  const extensionId = new URL(worker.url()).host;
  await worker.evaluate((backendUrl) => chrome.storage.local.set({ connection: { backendUrl, apiKey: 'test-only-key', requestTimeoutMs: 4000 } }), baseUrl);
  article = await context.newPage();
  await article.goto(`${baseUrl}/article`);
  panel = await context.newPage();
  await panel.goto(`chrome-extension://${extensionId}/sidepanel.html`);
  await expect(panel.locator('#connection-label')).toHaveText('Backend connected');
  await article.evaluate(() => {
    const range = document.createRange(); range.selectNodeContents(document.getElementById('claim'));
    getSelection().removeAllRanges(); getSelection().addRange(range);
  });
  await article.bringToFront();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
});

test.afterEach(async () => { await context?.close(); });

test('a suggested question is answered from the investigation with real, clickable citations', async () => {
  await expect(panel.locator('.chat-chip')).toHaveCount(4);
  await panel.getByRole('button', { name: 'Are these sources independent?' }).click();
  const answer = panel.locator('.chat-detective');
  await expect(answer).toContainText('measured the change');
  // The request carries the claim, verdict, explanation, sources and relationships, and only the local key.
  expect(chats[0].key).toBe('test-only-key');
  expect(chats[0].body).toMatchObject({ question: 'Are these sources independent?', claim: 'DART changed the orbit of Dimorphos.', status: 'supported', explanation: 'NASA measured a changed orbit [s1].', history: [] });
  expect(chats[0].body.sources.map((s) => s.id)).toEqual(['s1', 's2']);
  expect(chats[0].body.edges.length).toBe(2);
  expect(chats[0].body.page_text).toBe("NASA's DART impact altered Dimorphos's orbit around Didymos.");
  expect(JSON.stringify(chats[0].body)).not.toMatch(/gemini|brightdata/i);
  // Citations link to retrieved sources; an id that was never retrieved stays plain text; markup is inert.
  await expect(answer.locator('.chat-text a')).toHaveCount(3);
  await expect(answer.locator('.chat-text a').first()).toHaveAttribute('href', fixture.sources[0].url);
  await expect(answer).toContainText('[z9]');
  await expect(answer.locator('b')).toHaveCount(0);
  await expect(answer.locator('.stance-chip')).toHaveCount(1);
  await expect(answer.locator('.chat-stances')).toContainText('Supports the claim');
  await context.route(fixture.sources[0].url, (route) => route.fulfill({ contentType: 'text/html', body: 'source' }));
  const popup = panel.waitForEvent('popup');
  await answer.locator('.chat-text a').first().click();
  expect((await popup).url()).toBe(fixture.sources[0].url);
  // A typed follow-up sends the earlier exchange as history.
  await panel.locator('#chat-input').fill('What evidence is missing?');
  await panel.locator('#chat-input').press('Enter');
  await expect(panel.locator('.chat-detective')).toHaveCount(2);
  expect(chats[1].body.history.map((turn) => turn.role)).toEqual(['user', 'detective']);
  expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('uncited answers are labelled, and a backend failure is a retryable message', async () => {
  chatBehavior = 'uncited';
  await panel.getByRole('button', { name: 'What evidence is missing?' }).click();
  await expect(panel.locator('.chat-detective .chat-caution')).toContainText('No retrieved source backs this answer');
  chatBehavior = 'background';
  await panel.locator('#chat-input').fill('who is the committee chair');
  await panel.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(panel.locator('.chat-detective').nth(1)).toContainText('member of Congress');
  await expect(panel.locator('.chat-detective').nth(1).locator('.chat-caution')).toContainText('General background from the model');
  chatBehavior = 'down';
  await panel.getByRole('button', { name: 'What evidence contradicts this?' }).click();
  await expect(panel.locator('.chat-error')).toContainText('could not be reached');
  expect(chats.at(-1).body.history).toHaveLength(4);
  await expect(panel.getByRole('button', { name: 'What evidence contradicts this?' })).toBeEnabled();
});

test('no search runs until asked; Challenge the verdict retrieves counterevidence and can pin it to the board', async () => {
  await panel.getByRole('button', { name: 'What evidence is missing?' }).click();
  await expect(panel.locator('.chat-detective')).toHaveCount(1);
  expect(searches).toHaveLength(0);
  await panel.getByRole('button', { name: 'Challenge the verdict' }).click();
  const evidence = panel.locator('.chat-evidence');
  await expect(evidence).toContainText('Search for counterevidence: 1 new source');
  await expect(evidence).toContainText('Retrieved Nov 14');
  await expect(evidence).toContainText('The verdict above has not changed');
  expect(searches).toHaveLength(1);
  expect(searches[0].body).toEqual({ claim: 'DART changed the orbit of Dimorphos.', mode: 'counter', known: fixture.sources.map((s) => ({ id: s.id, url: s.url })) });
  // The unsafe source from the response is dropped; the real one is a working link.
  await expect(evidence.locator('li')).toHaveCount(1);
  await expect(evidence.locator('.chat-evidence-link')).toHaveAttribute('href', NEW_URL);
  // The detective is then asked about the new evidence, which it can now cite.
  await expect(panel.locator('.chat-detective')).toHaveCount(2);
  expect(chats[1].body.sources.map((s) => s.id)).toEqual(['s1', 's2', 'c1']);
  await expect(panel.locator('.chat-detective').nth(1).locator('.stance-contradicts')).toHaveText('c1');
  await expect(panel.locator('.chat-detective').nth(1).locator('.chat-text a[href="' + NEW_URL + '"]')).toHaveCount(1);
  // Nothing is on the board until the user pins it; the verdict never changes.
  await expect(panel.locator('#network .ev-card')).toHaveCount(2);
  await evidence.getByRole('button', { name: /Pin c1/ }).click();
  await expect(panel.locator('#network .ev-card')).toHaveCount(3);
  await expect(panel.locator('#sources .source-card')).toHaveCount(3);
  await expect(panel.locator('#edge-count')).toHaveText('(3)');
  await expect(panel.locator('#edge-list')).toContainText('c1 cites s1');
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  await expect(evidence.locator('.pinned-note')).toHaveText('Pinned');
  await expect(panel.locator('.chat-detective')).toHaveCount(2);
  const stored = await panel.evaluate(async () => (await chrome.storage.session.get('investigation')).investigation.result);
  expect(stored.graph_nodes.map((n) => n.id)).toEqual(['s1', 's2', 'c1']);
  expect(stored.status).toBe('supported');
});

test('failed and empty searches say so and add nothing', async () => {
  searchBehavior = 'failed';
  await panel.getByRole('button', { name: 'Search for more evidence' }).click();
  await expect(panel.locator('.chat-error')).toContainText('search failed');
  expect(searches[0].body.mode).toBe('more');
  searchBehavior = 'nothing';
  await panel.getByRole('button', { name: 'Search for more evidence' }).click();
  await expect(panel.locator('.chat-evidence')).toContainText('0 new sources');
  await expect(panel.locator('.chat-evidence')).toContainText('No new search was spent');
  await expect(panel.locator('.chat-evidence')).toContainText('not evidence for or against');
  expect(chats).toHaveLength(0);
  await expect(panel.locator('#network .ev-card')).toHaveCount(2);
});
