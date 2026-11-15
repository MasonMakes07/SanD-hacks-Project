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

let server, baseUrl, requests, behavior, context, panel, article;

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
    if (behavior === 'social') {
      const post = { id: 's3', title: 'Post on X', url: 'https://x.com/u/status/9', date: null, excerpt: 'NASA changed the orbit of Dimorphos.' };
      result.sources.push(post);
      result.graph_nodes.push({ id: post.id, title: post.title, url: post.url, kind: 'social_post' });
    }
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

async function analyzeSocialPage(path) {
  const previousRequests = requests.length;
  await article.goto(`${baseUrl}${path}`);
  await article.bringToFront();
  await panel.getByRole('button', { name: 'Analyze page', exact: true }).click();
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
  await expect(panel.locator('#status-badge')).toHaveText('Supported');
  expect(requests[0].body).toEqual({ text: longReply, url: `${baseUrl}/second/status/302`, mode: 'manual' });
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
  await expect(board.locator('.dossier-evidence')).toContainText('Observed link:');
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
  for(const count of [5,10,20]) {
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
  await expect(board.locator('#trace-next')).toHaveText('Overview ↗');
  await board.locator('#trace-next').click();
  await expect(board.locator('.ev-card.is-selected')).toHaveCount(0);
  await expect(board.locator('#trace-next')).toHaveText('Begin trace →');
  await board.setViewportSize({ width: 380, height: 850 });
  await board.locator('#trace-next').click();
  await expect(board.locator('#trace-next')).toBeInViewport();
  expect(await board.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await board.screenshot({ path: 'test-results/trace-walkthrough-mobile.png' });
  await board.setViewportSize({ width: 1440, height: 1000 });
  await board.locator('#fit').click();
  await board.screenshot({ path: 'test-results/trace-walkthrough-desktop.png' });
});
