import { test, expect, chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { readFile, mkdtemp, mkdir, copyFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
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

// Social layouts, reduced to the structure content.js relies on.
const longReply = 'This much longer reply would have been picked by the longest-article rule, even though it is not the post the user opened on this page.';
const socialPages = {
  '/newsguy/status/111': `<!doctype html><title>Post on X</title><main role="main">
    <article data-testid="tweet"><a href="/newsguy"><span>@newsguy</span></a><a href="/newsguy/status/111"><time>2h</time></a>
      <div data-testid="tweetText"><span id="main-post">Michigan poll worker found a FOREIGN wifi signal inside a vote tabulator!!! #StopTheSteal</span></div></article>
    <article data-testid="tweet"><a href="/replier/status/222"><time>1h</time></a><div data-testid="tweetText">${longReply}</div></article></main>`,
  '/home': `<!doctype html><title>Home / X</title><main role="main">
    <article data-testid="tweet" style="display:block;min-height:700px"><a href="/first/status/301"><time>5m</time></a>
      <div data-testid="tweetText">Taylor Swift and Travis Kelce pledged $250 MILLION to the Giuffre case 😱</div></article>
    <article data-testid="tweet" style="display:block;min-height:700px"><a href="/second/status/302"><time>9m</time></a>
      <div data-testid="tweetText"><span id="second-post">${longReply}</span></div></article>
    <article data-testid="tweet"><a href="https://evil.example/third/status/303"><time>1d</time></a><div data-testid="tweetText">Unsafe permalink post.</div></article></main>`,
  '/r/news/comments/abc123/library_cut/': `<!doctype html><title>Reddit post</title><main>
    <shreddit-post permalink="/r/news/comments/abc123/library_cut/" post-title="The city cut its library budget by 40% this year" style="display:block">
      <div slot="text-body"><p>Council documents show the cut.</p></div></shreddit-post>
    <shreddit-comment><p>${longReply}</p></shreddit-comment></main>`,
  '/r/old/': `<!doctype html><title>old reddit</title><div class="thing link" data-permalink="/r/old/comments/zz9/obama/" style="display:block">
    <p class="title"><a class="title" href="/r/old/comments/zz9/obama/">Obama was born in Kenya #Birther</a></p>
    <div class="usertext-body"><div class="md"><p>Mainstream media lies &gt;&gt;&gt;</p></div></div></div>`,
};

let server, baseUrl, requests, extractions, behavior, context, panel, article;

test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-TruthTrace-Key');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    if (req.method === 'OPTIONS') { res.writeHead(204); return res.end(); }
    if (req.url === '/article') { res.setHeader('Content-Type', 'text/html'); return res.end(articleHtml); }
    if (Object.hasOwn(socialPages, req.url)) { res.setHeader('Content-Type', 'text/html'); return res.end(socialPages[req.url]); }
    res.setHeader('Content-Type', 'application/json');
    if (req.url === '/api/health') return res.end(JSON.stringify({ status: 'ok', mock: false }));
    if (!['/api/analyze', '/api/extract'].includes(req.url) || req.method !== 'POST') { res.writeHead(404); return res.end('{}'); }
    let body = ''; for await (const chunk of req) body += chunk;
    body = JSON.parse(body || '{}');
    (req.url === '/api/extract' ? extractions : requests).push({ body, key: req.headers['x-truthtrace-key'] });
    if (behavior === 'unauthorized') { res.writeHead(401); return res.end('{"error":"unauthorized","message":"Denied"}'); }
    if (behavior === 'offline') { req.socket.destroy(); return; }
    if (behavior === 'slow' || (behavior === 'analysis_slow' && req.url === '/api/analyze')) return;
    if (req.url === '/api/extract') return res.end(JSON.stringify({
      checkable: behavior !== 'no_claim', claim: behavior === 'no_claim' ? '' : 'DART changed the orbit of Dimorphos.',
      confirmation_token: behavior === 'no_claim' ? null : 'fixture-token', input_context: { capture_kind: body.capture_kind, extraction_method: 'text_model' },
    }));
    if (behavior === 'server_error') { res.writeHead(500); return res.end('{"error":"internal_error"}'); }
    if (behavior === 'malformed') return res.end('{"status":"supported"}');
    const result = {
      ...structuredClone(fixture), claim: body.approved_claim || 'DART changed the orbit of Dimorphos.',
      status: 'supported', explanation: 'NASA measured a changed orbit [s1].',
    };
    if (behavior === 'empty') Object.assign(result, { sources: [], graph_nodes: [], graph_edges: [], earliest_observed_source: null, status: 'insufficient_evidence', explanation: 'No sources found.' });
    if (behavior === 'mock') result.investigation_id = 'mock-0000000000000000';
    if (behavior === 'social') {
      const post = { id: 's3', title: 'Post on X', url: 'https://x.com/u/status/9', date: null, excerpt: 'NASA changed the orbit of Dimorphos.' };
      result.sources.push(post);
      result.graph_nodes.push({ id: post.id, title: post.title, url: post.url, kind: 'social_post' });
    }
    if (behavior === 'one') {
      result.sources = result.sources.slice(0, 1); result.graph_nodes = result.graph_nodes.slice(0, 1); result.graph_edges = [];
    }
    if (behavior === 'uncited') result.explanation = 'The returned material is inconclusive; no source is cited.';
    if (behavior === 'legacy') result.graph_edges = [{ ...result.graph_edges[0], evidence: 'Legacy observed body link.' }];
    if (behavior === 'snippet') result.graph_edges = [{ ...result.graph_edges[1], evidence: { ...result.graph_edges[1].evidence, method: 'search_snippet_wording_overlap', note: 'Snippet wording only.' } }];
    if (behavior === 'factcheck') result.graph_edges = [{ ...result.graph_edges[0], relationship_type: 'same_fact_check', evidence: 'Both pages identify the same published fact-check.' }];
    if (behavior === 'shared') {
      const third = { id: 's3', title: 'Third citing page with a long descriptive title', url: 'https://example.com/third', date: null, excerpt: 'An observed third page.' };
      result.sources.push(third); result.graph_nodes.push({ ...third, kind: 'article' });
      result.graph_edges.push(result.graph_edges[0], { ...result.graph_edges[0], source_id: 's3' });
    }
    if (behavior === 'xss') {
      result.claim = '<img src=x onerror="window.pwned=true">';
      result.explanation = '<script>window.pwned=true</script> [s1] [unknown]';
      result.sources.push({ id: 'bad', title: 'Unsafe source', url: 'javascript:alert(1)', excerpt: 'bad' });
      result.graph_edges.push({ source_id: 's1', target_id: 's2', relationship_type: 'explicit_citation', evidence: '' });
    }
    if (behavior === 'delayed') setTimeout(() => res.end(JSON.stringify(result)), 5500);
    else res.end(JSON.stringify(result));
  }).listen(0, '127.0.0.1');
  await once(server, 'listening');
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => { server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); });

test.beforeEach(async () => {
  requests = []; extractions = []; behavior = 'result';
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

async function approveClaim() {
  await expect(panel.locator('#confirmation')).toBeVisible();
  await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  await expect(panel.locator('#loading')).toBeHidden();
}

async function openCaptureControls() {
  if (await panel.locator('#start-controls').isHidden()) await panel.getByRole('button', { name: 'New check', exact: true }).click();
}

test('fresh profile loads MV3 and runs selection → worker → backend → safe result/network', async () => {
  const errors = []; panel.on('pageerror', (e) => errors.push(e.message));
  await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await approveClaim();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  expect(requests[0]).toEqual({ body: { text: "NASA's DART impact altered Dimorphos's orbit around Didymos.", url: `${baseUrl}/article`, mode: 'manual', page_title: 'NASA DART demo article', capture_kind: 'selection', approved_claim: 'DART changed the orbit of Dimorphos.', confirmation_token: 'fixture-token' }, key: 'test-only-key' });
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
  await openCaptureControls();
  await panel.getByRole('button', { name: 'Analyze page', exact: true }).click();
  await approveClaim();
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
  await approveClaim();
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
    if (mode === 'malformed') await approveClaim();
    await expect(panel.locator('#error')).toContainText(message);
    await expect(panel.locator('#loading')).toBeHidden();
    await expect(panel.locator('#results')).toBeHidden();
  }
  behavior = 'result';
  await panel.getByRole('button', { name: 'Retry original capture' }).click();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
});

test('loading can be cancelled, and timeout restores the buttons', async () => {
  await panel.clock.install();
  behavior = 'slow'; await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect(panel.locator('#loading')).toBeVisible();
  await panel.getByRole('button', { name: 'Cancel check' }).click();
  await expect(panel.locator('#loading')).toBeHidden();
  await expect(panel.locator('#empty')).toBeVisible();
  await article.bringToFront();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await expect.poll(() => extractions.length).toBe(2);
  await panel.clock.fastForward(100001);
  await expect(panel.locator('#error')).toContainText('took too long');
  await expect(panel.getByRole('button', { name: 'Check selection' })).toBeEnabled();
});

test('renders model text literally, removes unsafe links and omits unevidenced edges', async () => {
  behavior = 'xss'; await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await approveClaim();
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
  await approveClaim();
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

async function analyzeSocialPage(path) {
  const previousRequests = requests.length;
  await article.goto(`${baseUrl}${path}`);
  await article.bringToFront();
  await openCaptureControls();
  await panel.getByRole('button', { name: 'Analyze page', exact: true }).click();
  await approveClaim();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  await expect.poll(() => requests.length).toBe(previousRequests + 1);
  await expect(panel.locator('#loading')).toBeHidden();
  return requests.at(-1).body;
}

test('Analyze page on an X post page checks the opened post, not a longer reply', async () => {
  const body = await analyzeSocialPage('/newsguy/status/111');
  expect(body.text).toBe('Michigan poll worker found a FOREIGN wifi signal inside a vote tabulator!!! #StopTheSteal');
  expect(body.url).toBe(`${baseUrl}/newsguy/status/111`);
});

test('Analyze page on an X feed checks the post filling the screen and sends its permalink', async () => {
  const body = await analyzeSocialPage('/home');
  expect(body.text).toContain('Taylor Swift and Travis Kelce pledged');
  expect(body.text).not.toContain('longest-article rule');
  expect(body.url).toBe(`${baseUrl}/first/status/301`);
});

test('a selection inside a feed post is sent with that post’s permalink', async () => {
  await article.goto(`${baseUrl}/home`);
  await article.evaluate(() => {
    const range = document.createRange(); range.selectNodeContents(document.getElementById('second-post'));
    getSelection().removeAllRanges(); getSelection().addRange(range);
  });
  await article.bringToFront();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await approveClaim();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  expect(requests[0].body).toEqual({ text: longReply, url: `${baseUrl}/second/status/302`, mode: 'manual', page_title: 'Home / X', capture_kind: 'selection', approved_claim: 'DART changed the orbit of Dimorphos.', confirmation_token: 'fixture-token' });
});

test('Reddit post pages (new and old layouts) send title plus body, not comments', async () => {
  const modern = await analyzeSocialPage('/r/news/comments/abc123/library_cut/');
  expect(modern.text).toBe('The city cut its library budget by 40% this year\n\nCouncil documents show the cut.');
  expect(modern.url).toBe(`${baseUrl}/r/news/comments/abc123/library_cut/`);
  const old = await analyzeSocialPage('/r/old/');
  expect(old.text).toBe('Obama was born in Kenya #Birther\n\nMainstream media lies >>>');
  expect(old.url).toBe(`${baseUrl}/r/old/comments/zz9/obama/`);
});

test('social permalinks must stay on the same site; other sites never adopt social rules', async () => {
  await article.goto(`${baseUrl}/home`);
  await article.evaluate(() => document.querySelectorAll('article').forEach((post, index) => { if (index < 2) post.remove(); }));
  const unsafe = await article.evaluate(contentScript);
  expect(unsafe.article).toBe('Unsafe permalink post.');
  expect(unsafe.articleUrl).toBe(`${baseUrl}/home`);
  await article.setContent('<article><p>A plain news article paragraph.</p></article>');
  const plain = await article.evaluate(contentScript);
  expect(plain.article).toBe('A plain news article paragraph.');
  expect(plain.articleUrl).toBeUndefined();
});

test('generic social hosts (Bluesky) use the post in view', async () => {
  await context.route('https://bsky.app/**', (route) => route.fulfill({ contentType: 'text/html', body:
    `<!doctype html><title>Bluesky</title><article style="display:block;min-height:800px"><p>Water boils at 90C at sea level.</p></article>
     <article><p>${longReply}</p></article>` }));
  await article.goto('https://bsky.app/profile/someone');
  const snapshot = await article.evaluate(contentScript);
  expect(snapshot.article).toBe('Water boils at 90C at sea level.');
  expect(snapshot.articleUrl).toBe('https://bsky.app/profile/someone');
});

test('social posts from the backend are labelled as search-snippet posts', async () => {
  behavior = 'social'; await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await approveClaim();
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  await expect(panel.locator('#sources .source-card')).toHaveCount(3);
  await expect(panel.locator('.source-social')).toHaveCount(1);
  await expect(panel.locator('#sources .source-card').nth(2)).toContainText('Social post · search snippet');
  await expect(panel.locator('.legend')).toContainText('Social post');
  await expect(panel.locator('#network canvas').first()).toBeVisible();
});

// Uses the checked-in NASA source URLs and observed relationship evidence.
// No upstream analysis calls or invented edges are used by this board review.
test('investigation board preserves paper layering, source actions, yarn physics and navigation', async () => {
  await selectClaim();
  await panel.getByRole('button', { name: 'Check selection' }).click();
  await approveClaim();
  await expect(panel.locator('#network .ev-card')).toHaveCount(2);
  await panel.locator('#network .ev-card').first().click();
  await expect(panel.locator('#edge-detail .dossier-open')).toHaveAttribute('href', fixture.sources[0].url);
  await panel.screenshot({ path: 'test-results/polish-panel-selected.png', fullPage: true });
  await panel.setViewportSize({width: 280, height: 850});
  expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await panel.screenshot({path:'test-results/polish-panel-narrow.png',fullPage:true});
  const opened = context.waitForEvent('page');
  await panel.locator('#open-board').click();
  const board = await opened;
  await board.setViewportSize({width:1440,height:1000});
  await expect(board.locator('.ev-card')).toHaveCount(2);
  await board.waitForTimeout(300);
  await board.screenshot({animations:'disabled',path:'test-results/polish-board.png'});
  expect(await board.evaluate(() => {
    const z = s => Number(getComputedStyle(document.querySelector(s)).zIndex) || 0;
    // Yarn runs pin to pin over the paper so every string stays visible; pin heads sit on top.
    return z('.board-world') < z('.board-yarn') && z('.board-yarn') < z('.board-pins');
  })).toBe(true);
  await board.locator('.ev-title').first().click();
  await expect(board.locator('.ev-card.is-selected')).toHaveCount(1);
  await expect(board.locator('.ev-card.is-related')).toHaveCount(1);
  await expect(board.locator('.dossier-open')).toHaveAttribute('href',fixture.sources[0].url);
  await board.screenshot({animations:'disabled',path:'test-results/polish-source.png'});
  await context.route(fixture.sources[0].url,route=>route.fulfill({body:'Original source destination'}));
  const popupPromise=board.waitForEvent('popup');
  await board.locator('.dossier-open').click();
  const popup=await popupPromise; await popup.waitForLoadState();
  expect(popup.url()).toBe(fixture.sources[0].url); await popup.close();
  await board.locator('.dossier-follow').first().click();
  await expect(board.locator('.dossier-title')).toHaveText('s1 cites s2');
  await expect(board.locator('.connection-evidence')).toContainText('Observed link:');
  await board.screenshot({animations:'disabled',path:'test-results/polish-relationship.png'});
  await board.locator('#reset').click();

  // Find a visible point on the first evidenced string, using the shared world transform.
  const stringPoint = () => board.evaluate(() => {
    const root=document.querySelector('#board'), rect=root.getBoundingClientRect();
    const m=new DOMMatrix(getComputedStyle(root.querySelector('.board-world')).transform);
    const card=id=>root.querySelector(`[data-id="${id}"]`);
    const a=card('s1'),b=card('s2');
    const ax=parseFloat(a.style.left), ay=parseFloat(a.style.top), bx=parseFloat(b.style.left), by=parseFloat(b.style.top);
    const sag=Math.min(46,Math.hypot(bx-ax,by-ay)*.055);
    for(const t of [.5,.4,.6,.3,.7]) {
      const p=new DOMPoint(ax+(bx-ax)*t,ay+(by-ay)*t+sag*4*t*(1-t)).matrixTransform(m);
      const x=p.x+rect.left,y=p.y+rect.top;
      if(!document.elementFromPoint(x,y)?.closest('.ev-card'))return {x,y};
    }
    throw new Error('No exposed string segment');
  });
  await board.evaluate(()=>{
    window.boardDraws=0;
    const original=CanvasRenderingContext2D.prototype.clearRect;
    CanvasRenderingContext2D.prototype.clearRect=function(...args){ if(this.canvas.classList.contains('board-yarn'))window.boardDraws++; return original.apply(this,args); };
  });
  const point=await stringPoint();
  await board.mouse.move(point.x,point.y);
  await expect.poll(()=>board.evaluate(()=>window.boardDraws)).toBeGreaterThan(3);
  await board.mouse.click(point.x,point.y);
  await expect(board.locator('.dossier-title')).toHaveText('s1 cites s2');
  await board.mouse.move(5,5);
  // Damped motion must stop scheduling redraws, not merely look stationary.
  await expect.poll(async()=>{
    const n=await board.evaluate(()=>window.boardDraws);
    await board.waitForTimeout(250);
    return (await board.evaluate(()=>window.boardDraws))===n;
  },{timeout:6000}).toBe(true);
  const before=await board.locator('.board-world').getAttribute('style');
  await board.locator('#zoom-in').click();
  await expect.poll(()=>board.locator('.board-world').getAttribute('style')).not.toBe(before);
  await board.waitForTimeout(300);
  await board.locator('#fit').click();
  await board.waitForTimeout(300);
  await board.emulateMedia({reducedMotion:'reduce'});
  const p=await stringPoint();
  await board.mouse.move(p.x,p.y);
  await board.waitForTimeout(100);
  const reduced=await board.evaluate(()=>window.boardDraws);
  await board.waitForTimeout(300);
  expect(await board.evaluate(()=>window.boardDraws)).toBe(reduced);
  const card=board.locator('.ev-card').first();
  const box=await card.boundingBox();
  await board.mouse.move(box.x+box.width/2,box.y+box.height/2);
  const position=await card.getAttribute('style');
  await board.mouse.down();await board.mouse.move(box.x+box.width/2+45,box.y+box.height/2+30,{steps:5});await board.mouse.up();
  expect(await card.getAttribute('style')).not.toBe(position);
  await board.locator('#fit').click();
  const panBefore=await board.locator('.board-world').getAttribute('style');
  const area=await board.locator('#board').boundingBox();
  await board.mouse.move(area.x+30,area.y+area.height/2);
  await board.mouse.down();await board.mouse.move(area.x+70,area.y+area.height/2+25,{steps:4});await board.mouse.up();
  expect(await board.locator('.board-world').getAttribute('style')).not.toBe(panBefore);
  await board.setViewportSize({width:380,height:850});
  expect(await board.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await board.locator('#fit').click();
  await board.screenshot({animations:'disabled',path:'test-results/polish-board-narrow.png'});
});

test('layout stress uses repeated genuine NASA source text without adding relationships', async () => {
  // Display-only repetition, explicitly labelled; this is not an investigation.
  const board=await context.newPage();
  const worker=context.serviceWorkers()[0];
  for(const count of [5,7,10,20]) {
    const sources=Array.from({length:count},(_,i)=>({...fixture.sources[i%2],id:`layout-${i}`,date:i%3?null:fixture.sources[i%2].date}));
    const result={...fixture,investigation_id:'synthetic-layout-only',claim:'Layout test: repeated NASA source text',status:'insufficient_evidence',explanation:'UI layout test only. Repeated source text; no relationships asserted.',sources,graph_nodes:sources,graph_edges:[],earliest_observed_source:null};
    await worker.evaluate(result=>chrome.storage.session.set({investigation:{result,mock:true}}),result);
    await board.setViewportSize({width:1440,height:1000});
    await board.goto(`chrome-extension://${new URL(worker.url()).host}/board.html`);
    await expect(board.locator('.ev-card')).toHaveCount(count);
    await expect(board.locator('#mock-warning')).toBeVisible();
    await expect(board.locator('#dossier-body')).toContainText('No evidence-backed connections');
    await board.waitForTimeout(300);
    const collisions=await board.locator('.ev-card').evaluateAll(cards=>{
      const rects=cards.map(card=>card.getBoundingClientRect());
      return rects.flatMap((a,i)=>rects.slice(i+1).filter(b=>Math.min(a.right,b.right)>Math.max(a.left,b.left)&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top))).length;
    });
    expect(collisions).toBe(0);
    await board.screenshot({animations:'disabled',path:`test-results/polish-layout-${count}.png`});
  }
});

test('trace walkthrough focuses dated sources, returns to overview and handles unknown dates', async () => {
  const [worker] = context.serviceWorkers();
  const result = { ...structuredClone(fixture), claim: "DART changed the orbit of Dimorphos.", status: "supported", explanation: "NASA measured a changed orbit [s1]." };
  result.sources[0].date = null;
  await worker.evaluate(result => chrome.storage.session.set({ investigation: { result, mock: true } }), result);
  const board = await context.newPage();
  await board.setViewportSize({ width: 1440, height: 1000 });
  await board.emulateMedia({ reducedMotion: 'reduce' });
  await board.goto(`chrome-extension://${new URL(worker.url()).host}/board.html`);
  const stops = board.locator('.trace-stop');
  await expect(stops).toHaveCount(result.graph_nodes.length);
  await expect(board.locator('#trace-prev')).toBeDisabled();
  await board.locator('#trace-next').click();
  await expect(stops.first()).toHaveAttribute('aria-pressed', 'true');
  await expect(board.locator('.ev-card.is-selected')).toHaveCount(1);
  const focused = await board.locator('.ev-card.is-selected').boundingBox();
  const stage = await board.locator('#board').boundingBox();
  expect(focused.x).toBeGreaterThan(stage.x);
  expect(focused.x + focused.width).toBeLessThan(stage.x + stage.width);
  await stops.last().click();
  await expect(board.locator('#trace-caption')).toContainText('Date unavailable');
  await expect(board.locator('#trace-next')).toHaveText('Overview');
  await board.locator('#trace-next').click();
  await expect(board.locator('.ev-card.is-selected')).toHaveCount(0);
  await expect(board.locator('#trace-next')).toHaveText('Begin source tour');
  await board.setViewportSize({ width: 380, height: 850 });
  await board.locator('#trace-next').click();
  await expect(board.locator('#trace-next')).toBeInViewport();
  expect(await board.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await board.screenshot({ path: 'test-results/trace-walkthrough-mobile.png' });
  await board.setViewportSize({ width: 1440, height: 1000 });
  await board.locator('#fit').click();
  await board.screenshot({ path: 'test-results/trace-walkthrough-desktop.png' });
});

test('reader edits before retrieval, freezes original context, and cancels without a check', async () => {
  await selectClaim();
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click();
  await expect(panel.locator('#confirmation')).toBeVisible();
  expect(requests).toHaveLength(0);
  await expect(panel.locator('#original-input')).toHaveText("NASA's DART impact altered Dimorphos's orbit around Didymos.");
  await panel.locator('#approved-claim').fill('The reader approved this exact statement.');
  await article.evaluate(() => { getSelection().removeAllRanges(); document.title = 'Changed after capture'; });
  const other = await context.newPage(); await other.goto(`${baseUrl}/home`); await other.bringToFront();
  await approveClaim();
  expect(requests[0].body.approved_claim).toBe('The reader approved this exact statement.');
  expect(requests[0].body.url).toBe(`${baseUrl}/article`);
  await expect(panel.locator('#claim')).toHaveText('The reader approved this exact statement.');
  await expect(panel.locator('#checked-page')).toHaveText('NASA DART demo article');
  await selectClaim(); await openCaptureControls();
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click();
  await expect(panel.locator('#confirmation')).toBeVisible();
  await panel.getByRole('button', { name: 'Cancel confirmation' }).click();
  expect(requests).toHaveLength(1);
  await expect(panel.locator('#claim')).toHaveText('The reader approved this exact statement.');
});

test('failed and cancelled subsequent checks preserve completed result and retry exact approved payload', async () => {
  await selectClaim();
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click();
  await approveClaim();
  const previousClaim = await panel.locator('#claim').textContent();
  await openCaptureControls(); await selectClaim(); behavior = 'server_error';
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click();
  await panel.locator('#approved-claim').fill('A second approved claim.');
  await approveClaim();
  await expect(panel.locator('#error')).toBeVisible();
  await expect(panel.locator('#results')).toBeVisible();
  await expect(panel.locator('#claim')).toHaveText(previousClaim);
  const failedPayload = structuredClone(requests.at(-1).body);
  await article.evaluate(() => getSelection().removeAllRanges());
  const other = await context.newPage(); await other.goto(`${baseUrl}/home`); await other.bringToFront();
  behavior = 'result'; const extractionCount = extractions.length;
  await panel.getByRole('button', { name: 'Retry original capture' }).click();
  await expect(panel.locator('#claim')).toHaveText('A second approved claim.');
  expect(requests.at(-1).body).toEqual(failedPayload);
  expect(extractions.length).toBe(extractionCount);
  await openCaptureControls(); await article.bringToFront();
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click();
  await expect(panel.locator('#error')).toContainText('Select a factual sentence');
  await expect(panel.getByRole('button', { name: 'Capture again', exact: true })).toBeVisible();
  await expect(panel.locator('#claim')).toHaveText('A second approved claim.');
});

test('completed result is immediately readable at 280/380px with keyboard New check and uncertainty', async () => {
  behavior = 'empty'; await selectClaim();
  await panel.emulateMedia({ reducedMotion: 'reduce' });
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click(); await approveClaim();
  for (const width of [280, 380]) {
    await panel.setViewportSize({ width, height: 800 });
    await panel.evaluate(() => scrollTo(0, 0));
    await expect(panel.locator('#status-badge')).toBeInViewport();
    await expect(panel.locator('#start-controls')).toBeHidden();
    await expect(panel.locator('#compact-status')).toHaveText('Insufficient evidence');
    expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await panel.getByRole('button', { name: 'New check', exact: true }).focus(); await panel.keyboard.press('Enter');
  await expect(panel.locator('#check-selection')).toBeFocused();
  await expect(panel.locator('#results')).toBeVisible();
  await expect(panel.locator('#no-sources')).toBeVisible();
});

const highlightedText = () => article.evaluate(() => [...CSS.highlights.values()].flatMap((highlight) => [...highlight].map((range) => range.toString())));

test('Show on page highlights exact inline selection rather than approved wording, then cleans up', async () => {
  await article.setContent('<article><p id="claim">Orbit <em>changed</em> by <a href="#detail">32 minutes</a>.</p><p id="detail">Observed context.</p></article>');
  const before = await article.locator('article').innerHTML();
  await article.emulateMedia({ reducedMotion: 'reduce' });
  await selectClaim();
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click();
  await panel.locator('#approved-claim').fill('Edited claim which never appeared verbatim.');
  await approveClaim();
  await panel.getByRole('button', { name: 'Show original selection on page' }).focus(); await panel.keyboard.press('Enter');
  await expect.poll(highlightedText).toEqual(['Orbit changed by 32 minutes.']);
  expect(await article.locator('article').innerHTML()).toBe(before);
  await expect(article.getByRole('button', { name: 'Remove TruthTrace highlight' })).toBeVisible();
  await article.getByRole('button', { name: 'Remove TruthTrace highlight' }).click();
  await expect.poll(highlightedText).toEqual([]);
  await panel.getByRole('button', { name: 'Show original selection on page' }).click();
  await expect.poll(highlightedText).toHaveLength(1);
  await article.locator('#claim em').evaluate((node) => { node.textContent = 'changed again'; });
  await expect.poll(highlightedText).toEqual([]);
  await panel.getByRole('button', { name: 'Show original selection on page' }).click();
  await expect(panel.locator('#anchor-message')).toContainText('changed');
  await expect.poll(highlightedText).toEqual([]);
});

test('page anchors disambiguate context but refuse identical contexts and replaced documents', async () => {
  await article.setContent(`<article><p>${'A'.repeat(100)}<span>Repeated claim.</span>${'B'.repeat(100)}</p><p>${'C'.repeat(100)}<span id="claim">Repeated claim.</span>${'D'.repeat(100)}</p></article>`);
  await selectClaim(); await panel.getByRole('button', { name: 'Check selection', exact: true }).click(); await approveClaim();
  await panel.getByRole('button', { name: 'Show original selection on page' }).click();
  await expect.poll(highlightedText).toEqual(['Repeated claim.']);
  expect(await article.evaluate(() => [...CSS.highlights.values()][0].values().next().value.startContainer.parentElement.id)).toBe('claim');
  const block = `${'A'.repeat(100)}<span>Repeated claim.</span>${'B'.repeat(100)}`;
  await article.setContent(`<article><p>${block}</p><p>${block.replace('<span>', '<span id="claim">')}</p></article>`);
  await selectClaim(); await openCaptureControls(); await panel.getByRole('button', { name: 'Check selection', exact: true }).click(); await approveClaim();
  await panel.getByRole('button', { name: 'Show original selection on page' }).click();
  await expect(panel.locator('#anchor-message')).toContainText('more than one matching context');
  await expect.poll(highlightedText).toEqual([]);
  await article.reload();
  await panel.getByRole('button', { name: 'Show original selection on page' }).click();
  await expect(panel.locator('#anchor-message')).toContainText('unavailable');
  await expect.poll(highlightedText).toEqual([]);
  await article.close();
  await panel.getByRole('button', { name: 'Show original selection on page' }).click();
  await expect(panel.locator('#anchor-message')).toContainText('closed');
});

test('new investigation clears highlight and changed URL never highlights another document', async () => {
  await selectClaim(); await panel.getByRole('button', { name: 'Check selection', exact: true }).click(); await approveClaim();
  await panel.getByRole('button', { name: 'Show original selection on page' }).click();
  await expect.poll(highlightedText).toHaveLength(1);
  await selectClaim(); await openCaptureControls(); await panel.getByRole('button', { name: 'Check selection', exact: true }).click(); await approveClaim();
  await expect.poll(highlightedText).toEqual([]);
  await article.evaluate(() => history.pushState({}, '', '/other-document'));
  await panel.getByRole('button', { name: 'Show original selection on page' }).click();
  await expect(panel.locator('#anchor-message')).toContainText('page changed or temporary access was lost');
  await expect.poll(highlightedText).toEqual([]);
});

test('video content snapshots exact playhead, optional duration and YouTube navigation separately from text intent', async () => {
  const videoUrl = 'https://www.youtube.com/watch?v=N-q2z4N_Rsk';
  await context.route('https://www.youtube.com/**', (route) => route.fulfill({ contentType: 'text/html', body: '<title>Video fixture</title><link rel="canonical" href="https://www.youtube.com/watch?v=N-q2z4N_Rsk"><main><video style="width:600px;height:340px"></video><p id="claim">Selected text remains separate from a video statement.</p></main>' }));
  await article.goto(videoUrl);
  await article.evaluate(() => {
    const video = document.querySelector('video');
    Object.defineProperties(video, { currentTime: { configurable: true, value: 141.25 }, duration: { configurable: true, value: 205 }, readyState: { configurable: true, value: 4 } });
  });
  await selectClaim();
  const first = await article.evaluate(contentScript);
  expect(first.video).toEqual({ url: videoUrl, video_time_seconds: 141.25, video_duration_seconds: 205 });
  expect(first.selection).toBe('Selected text remains separate from a video statement.');
  await article.evaluate(() => {
    const video = document.querySelector('video');
    Object.defineProperties(video, { currentTime: { configurable: true, value: 188 }, duration: { configurable: true, value: Infinity } });
  });
  expect((await article.evaluate(contentScript)).video).toEqual({ url: videoUrl, video_time_seconds: 188, video_duration_seconds: null });
  expect(first.video.video_time_seconds).toBe(141.25);
  await article.evaluate(() => history.pushState({}, '', '/watch?v=abcdefghijk'));
  expect((await article.evaluate(contentScript)).video).toBeNull();
  await article.evaluate(() => document.querySelector('link[rel=canonical]').href = location.href);
  expect((await article.evaluate(contentScript)).video.url).toBe('https://www.youtube.com/watch?v=abcdefghijk');
  await article.evaluate(() => { document.querySelector('video').remove(); });
  const missing = await article.evaluate(contentScript);
  expect(missing.video).toBeNull(); expect(missing.videoError).toContain('playback position is unavailable');
});

test('video context renders only valid replay times and labels original model wording separately from edits', async () => {
  await panel.evaluate(async () => {
    const { renderVideoContext } = await import('./src/video-context.js');
    const capture = { kind: 'video', url: 'https://www.youtube.com/watch?v=N-q2z4N_Rsk', video_time_seconds: 141.5, video_duration_seconds: 205 };
    const result = { claim_source: { video_url: capture.url, timestamp_seconds: 105, quote: 'Model-detected words.', speaker: 'Detected speaker' }, input_context: { claim_edited: true, reviewed_start_seconds: 81, reviewed_end_seconds: 141, extraction_method: 'video_model' } };
    renderVideoContext(document.getElementById('confirmation-video'), result, capture);
    document.getElementById('confirmation').hidden = false;
  });
  await expect(panel.locator('#confirmation-video')).toContainText('Captured playback moment: 2:21');
  await expect(panel.locator('#confirmation-video')).toContainText('Reviewed range: 1:21–2:21');
  await expect(panel.locator('#confirmation-video')).toContainText('not been verified against a transcript');
  await expect(panel.locator('#confirmation-video')).toContainText('approved claim was edited');
  await expect(panel.getByRole('link', { name: 'Replay at 1:45' })).toHaveAttribute('href', 'https://www.youtube.com/watch?v=N-q2z4N_Rsk&t=105s');
  await panel.evaluate(async () => {
    const { renderVideoContext } = await import('./src/video-context.js');
    renderVideoContext(document.getElementById('confirmation-video'), { claim_source: { video_url: 'https://www.youtube.com/watch?v=N-q2z4N_Rsk', timestamp_seconds: null } }, { kind: 'video', url: 'https://www.youtube.com/watch?v=N-q2z4N_Rsk', video_time_seconds: 141 });
  });
  await expect(panel.locator('#confirmation-video a')).toHaveCount(0);
  await expect(panel.locator('#confirmation-video')).toContainText('timestamp unavailable');
});

test('finding source filters, graph/card selection and connection walkthrough share resettable state at narrow widths', async () => {
  behavior = 'shared';
  await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await approveClaim();
  await expect(panel.locator('#sources .source-card')).toHaveCount(3);
  await expect(panel.locator('#filter-cited')).toHaveText('Cited in this finding (1)');
  await panel.locator('#filter-cited').click();
  await expect(panel.locator('#sources .source-card:visible')).toHaveCount(1);
  await panel.locator('.ev-card[data-id="s3"]').focus();
  await panel.keyboard.press('Enter');
  await expect(panel.locator('#filter-all')).toHaveAttribute('aria-pressed', 'true');
  await expect(panel.locator('#filter-message')).toContainText('Showing all sources');
  await expect(panel.locator('.source-card[data-source-id="s3"]')).toHaveClass(/is-selected/);
  await panel.locator('#reset-selection').click();
  await expect(panel.locator('.source-card.is-selected')).toHaveCount(0);
  await expect(panel.locator('#edge-detail')).toBeEmpty();
  for (const width of [280, 380]) {
    await panel.setViewportSize({ width, height: 900 });
    await panel.locator('#connection-walkthrough').getByRole('button', { name: 'Follow the evidence', exact: true }).click();
    await expect(panel.locator('#edge-detail .dossier-title')).toHaveText('s1 cites s2');
    await expect(panel.locator('#network .ev-card.is-related')).toHaveCount(2);
    await expect(panel.locator('#edge-detail')).toContainText('Observed link:');
    await expect(panel.getByRole('button', { name: 'Next connection', exact: true })).toBeFocused();
    await panel.keyboard.press('Enter');
    await panel.getByRole('button', { name: 'Previous connection', exact: true }).click();
    await panel.getByRole('button', { name: 'Exit walkthrough', exact: true }).click();
    expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await panel.locator('#shared-references summary').click();
  await expect(panel.locator('#shared-references summary')).toContainText('2 retrieved pages cite s2');
  await panel.getByRole('button', { name: 'Inspect s3 citing s2', exact: true }).focus();
  await panel.keyboard.press('Enter');
  await expect(panel.locator('#edge-detail .dossier-title')).toHaveText('s3 cites s2');
  await expect(panel.locator('#edge-detail a')).toHaveCount(3);
  expect(requests).toHaveLength(1);
  // A completely new result clears every selection and resets the list filter.
  await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await approveClaim();
  await expect.poll(() => requests.length).toBe(2);
  await expect(panel.locator('.source-card.is-selected')).toHaveCount(0);
  await expect(panel.locator('#reset-selection')).toBeDisabled();
});

test('copy report click gives success/failure feedback without more analysis', async () => {
  await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await approveClaim();
  await expect(panel.locator('#results')).toBeVisible();
  await panel.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { window.copiedReport = text; } } }));
  await panel.locator('#copy-report').click();
  await expect(panel.locator('#report-message')).toContainText('report copied');
  expect(await panel.evaluate(() => window.copiedReport)).toContain('NASA measured a changed orbit [s1]');
  await panel.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async () => { throw Error('denied'); } } }));
  await panel.locator('#copy-report').click();
  await expect(panel.locator('#report-message')).toContainText('Could not copy');
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  expect(requests).toHaveLength(1);
});

test('loading shows actual pending request and elapsed time, then clears on cancel and retry under reduced motion', async () => {
  behavior = 'analysis_slow';
  await context.serviceWorkers()[0].evaluate(async () => {
    const { connection } = await chrome.storage.local.get('connection');
    await chrome.storage.local.set({ connection: { ...connection, requestTimeoutMs: 10000 } });
  });
  await panel.reload(); await expect(panel.locator('#connection-label')).toHaveText('Backend connected');
  await panel.emulateMedia({ reducedMotion: 'reduce' });
  await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  await expect(panel.locator('#loading-title')).toHaveText('Waiting for the investigation…');
  await expect(panel.locator('#loading-text')).toContainText('request is pending');
  await expect(panel.locator('#loading-elapsed')).toContainText('elapsed', { timeout: 7000 });
  expect(await panel.locator('.tracing-motif path').evaluate(node => getComputedStyle(node).animationName)).toBe('none');
  await panel.locator('#cancel').focus(); await panel.keyboard.press('Enter');
  await expect(panel.locator('#loading')).toBeHidden();
  await expect(panel.locator('#loading-elapsed')).toBeEmpty();
  behavior = 'result';
  await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await approveClaim();
  await expect(panel.locator('#results')).toBeVisible();
  await expect(panel.locator('#loading-elapsed')).toBeEmpty();
});

test('empty, single source, legacy, snippet and shared fact-check relationships retain truthful keyboard evidence', async () => {
  await panel.setViewportSize({ width: 280, height: 900 });
  for (const mode of ['empty', 'one', 'legacy', 'snippet', 'factcheck', 'uncited']) {
    behavior = mode;
    await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await approveClaim();
    await expect(panel.locator('#results')).toBeVisible();
    if (['empty', 'one'].includes(mode)) {
      await expect(panel.locator('#connection-walkthrough')).toContainText('No observed connections');
      await expect(panel.locator('#connection-walkthrough button').first()).toBeDisabled();
      if (mode === 'one') {
        await panel.locator('#sources button').click();
        await expect(panel.locator('#edge-detail')).toContainText('No evidence-backed connections involve this source.');
        await panel.locator('#reset-selection').click();
      }
    } else if (mode === 'uncited') {
      await expect(panel.locator('#filter-cited')).toHaveText('Cited in this finding (0)');
      await panel.locator('#filter-cited').click();
      await expect(panel.locator('#no-cited-sources')).toBeVisible();
      await expect(panel.locator('#sources .source-card:visible')).toHaveCount(0);
    } else {
      await panel.locator('#connections summary').click();
      await panel.locator('#edge-list .connection-select').focus();
      await panel.keyboard.press('Enter');
      await expect(panel.locator('#network .is-related')).toHaveCount(2);
      if (mode === 'legacy') await expect(panel.locator('#edge-detail')).toContainText('Legacy relationship evidence');
      if (mode === 'snippet') {
        await expect(panel.locator('#edge-detail')).toContainText('Search snippet');
        await expect(panel.locator('#edge-detail')).toContainText('not a verified article passage');
        await expect(panel.locator('#edge-detail .evidence-passages > div')).toHaveCount(2);
      }
      if (mode === 'factcheck') await expect(panel.locator('#edge-detail')).toContainText('same published fact-check');
    }
    expect(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test('a genuinely delayed response shows elapsed feedback then renders immediately and clears its timer', async () => {
  behavior = 'delayed';
  await context.serviceWorkers()[0].evaluate(async () => {
    const { connection } = await chrome.storage.local.get('connection');
    await chrome.storage.local.set({ connection: { ...connection, requestTimeoutMs: 10000 } });
  });
  await panel.reload(); await expect(panel.locator('#connection-label')).toHaveText('Backend connected');
  await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await panel.getByRole('button', { name: 'Check this claim', exact: true }).click();
  await expect(panel.locator('#loading-elapsed')).toContainText('elapsed', { timeout: 5400 });
  await expect(panel.locator('#results')).toBeVisible();
  await expect(panel.locator('#loading-elapsed')).toBeEmpty();
  await expect(panel.locator('#loading')).toBeHidden();
});

test('real clipboard stores the report and can be read into notes with test-only read permission', async () => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await approveClaim();
  await expect(panel.locator('#results')).toBeVisible();
  await panel.locator('#copy-report').click();
  await expect(panel.locator('#report-message')).toContainText('report copied');
  const text = await panel.evaluate(async () => {
    const notes = document.createElement('textarea'); document.body.append(notes); notes.id = 'test-notes';
    // Read the actual browser clipboard, not a writeText stub.
    notes.value = await navigator.clipboard.readText(); return notes.value;
  });
  expect(text).toContain('TruthTrace evidence report');
  expect(text).toContain(fixture.sources[0].url);
  expect(text).toContain('NASA measured a changed orbit [s1].');
  expect(requests).toHaveLength(1);
});

test('full board keeps connection walkthrough distinct from publication-date tour and rejects stale selections', async () => {
  await selectClaim(); await openCaptureControls(); await panel.locator('#check-selection').click();
  await approveClaim();
  await expect(panel.locator('#results')).toBeVisible();
  const opened = context.waitForEvent('page'); await panel.locator('#open-board').click();
  const board = await opened;
  await board.setViewportSize({ width: 380, height: 900 });
  await board.locator('#connection-walkthrough').getByRole('button', { name: 'Follow the evidence', exact: true }).click();
  await expect(board.locator('.dossier-title')).toHaveText('s1 cites s2');
  await expect(board.locator('#board .ev-card.is-related')).toHaveCount(2);
  await expect(board.locator('#trace-caption')).toContainText('Explore sources by publication date');
  await board.getByRole('button', { name: 'Next connection', exact: true }).click();
  await expect(board.locator('.dossier-title')).toContainText('share similar wording');
  await expect(board.locator('.evidence-passages > div')).toHaveCount(2);
  await board.getByRole('button', { name: 'Exit walkthrough', exact: true }).click();
  await expect(board.locator('#reset')).toBeDisabled();
  await board.locator('#trace-next').click();
  await expect(board.locator('.dossier-kind')).toContainText('Source');
  await expect(board.locator('#connection-walkthrough .walk-caption')).toContainText('does not establish chronology');
  expect(await board.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const guards = await panel.evaluate(async () => {
    const { createBoard } = await import('./src/board.js');
    const { investigation } = await chrome.storage.session.get('investigation');
    const host = document.createElement('div'); host.style.cssText = 'width:300px;height:320px'; document.body.append(host);
    let calls = 0;
    let disposable = createBoard(host, investigation.result, { onSelect: () => { calls++; } });
    disposable.select({ type: 'source', id: 'missing-id' });
    const unknown = disposable.selection;
    disposable.select({ type: 'edge', index: 999 });
    const invalid = disposable.selection;
    disposable.destroy(); calls = 0;
    disposable = createBoard(host, investigation.result, { onSelect: () => { calls++; } });
    host.querySelector('.ev-card').click(); const afterRecreate = calls;
    disposable.destroy(); host.remove();
    return { unknown, invalid, afterRecreate };
  });
  expect(guards).toEqual({ unknown: null, invalid: null, afterRecreate: 1 });
});

test('video action traverses actual worker transport, then retries the approved original moment after seeking/navigation', async () => {
  const url = 'https://www.youtube.com/watch?v=N-q2z4N_Rsk';
  const media = (await readFile(new URL('./fixtures/video-moment.webm', import.meta.url))).toString('base64');
  await context.route('https://www.youtube.com/**', (route) => route.fulfill({ contentType: 'text/html', body: `<title>Public video fixture</title><link rel="canonical" href="${url}"><main><video preload="auto" src="data:video/webm;base64,${media}" style="width:600px;height:340px"></video><p id="claim">A selected sentence on YouTube.</p></main>` }));
  await article.goto(url);
  await article.waitForFunction(() => document.querySelector('video').readyState > 1);
  await article.evaluate(() => { document.querySelector('video').currentTime = 141.25; });
  await article.waitForFunction(() => !document.querySelector('video').seeking);
  await article.bringToFront();
  await panel.getByRole('button', { name: 'Check around video moment' }).click();
  await expect(panel.locator('#confirmation')).toBeVisible();
  expect(extractions.at(-1).body).toMatchObject({ capture_kind: 'video', url, text: 'Public video fixture', page_title: 'Public video fixture', video_time_seconds: 141.25, video_duration_seconds: 205 });
  await article.evaluate(() => {
    document.querySelector('video').currentTime = 188;
    history.pushState({}, '', '/watch?v=abcdefghijk');
  });
  behavior = 'server_error';
  await panel.locator('#approved-claim').fill('The reader approved this video statement.');
  await approveClaim();
  await expect(panel.locator('#error')).toBeVisible();
  const original = structuredClone(requests.at(-1).body);
  behavior = 'result';
  const other = await context.newPage(); await other.goto(`${baseUrl}/home`); await other.bringToFront();
  await panel.getByRole('button', { name: 'Retry original capture' }).click();
  await expect(panel.locator('#claim')).toHaveText(original.approved_claim);
  expect(requests.at(-1).body).toEqual(original);
  expect(extractions).toHaveLength(1);
  // New text intent on the same YouTube tab sends only the selected sentence.
  await article.evaluate(() => { document.querySelector('link[rel=canonical]').href = location.href; });
  await selectClaim(); await openCaptureControls();
  await panel.getByRole('button', { name: 'Check selection', exact: true }).click(); await approveClaim();
  expect(requests.at(-1).body).toMatchObject({ capture_kind: 'selection', text: 'A selected sentence on YouTube.' });
  expect(requests.at(-1).body).not.toHaveProperty('video_time_seconds');
});


test('lost page permission produces toolbar guidance and cannot highlight unrelated text', async () => {
  // Restrict a test-only copy to localhost: the shipped baseline has broad
  // host permissions, so clearing ordinary browser permissions cannot revoke it.
  const root = await mkdtemp(join(tmpdir(), 'truthtrace-permission-'));
  let restricted;
  try {
    const manifest = JSON.parse(await readFile(new URL('../manifest.json', import.meta.url)));
    manifest.host_permissions = ['http://127.0.0.1/*', 'http://localhost/*'];
    await writeFile(join(root, 'manifest.json'), JSON.stringify(manifest));
    await writeFile(join(root, 'sidepanel.html'), '<!doctype html><title>Permission failure fixture</title>');
    await mkdir(join(root, 'src'));
    for (const name of ['background.js', 'content.js', 'anchor.js']) await copyFile(new URL(`../src/${name}`, import.meta.url), join(root, 'src', name));
    restricted = await chromium.launchPersistentContext('', { channel: 'chromium', headless: true, args: [`--disable-extensions-except=${root}`, `--load-extension=${root}`] });
    let [worker] = restricted.serviceWorkers(); if (!worker) worker = await restricted.waitForEvent('serviceworker');
    await restricted.route('https://example.com/**', route => route.fulfill({ contentType: 'text/html', body: '<article><p>Unrelated private page text must not be highlighted.</p></article>' }));
    const page = await restricted.newPage(); await page.goto('https://example.com/article');
    const trusted = await restricted.newPage(); await trusted.goto(`chrome-extension://${new URL(worker.url()).host}/sidepanel.html`);
    await page.bringToFront();
    const responses = await trusted.evaluate(async () => {
      const window = await chrome.windows.getCurrent();
      const [tab] = await chrome.tabs.query({ active: true, windowId: window.id });
      return [await chrome.runtime.sendMessage({ type: 'CAPTURE', windowId: window.id, kind: 'selection' }),
        await chrome.runtime.sendMessage({ type: 'SHOW_CAPTURE', capture: { tabId: tab.id, documentId: 'expired-document', documentUrl: 'https://example.com/article', selectionAnchor: { documentToken: 'expired', exact: 'Unrelated private page text must not be highlighted.' } } })];
    });
    expect(responses[0].ok).toBe(false); expect(responses[0].error).toContain('toolbar icon');
    expect(responses[1].ok).toBe(false); expect(responses[1].error).toContain('temporary access');
    expect(await page.evaluate(() => CSS.highlights.size)).toBe(0);
    expect(await page.locator('article').textContent()).toBe('Unrelated private page text must not be highlighted.');
  } finally { await restricted?.close(); await rm(root, { recursive: true, force: true }); }
});
