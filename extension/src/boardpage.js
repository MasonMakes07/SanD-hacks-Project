// Full investigation board. Reads the investigation the side panel stored for
// this browser session; it never calls the backend itself.
import { STATUSES, formatDate, normalizeResult } from './result.js';
import { createBoard } from './board.js';
import { renderDetail, edgeHeadline } from './detail.js';
import { createWalkthrough } from './walkthrough.js';
import { renderSharedReferences } from './presentation.js';
import { decorateIcons } from './artwork.js';

const $ = (id) => document.getElementById(id);
let board = null;
let traceSources = [];
let traceIndex = -1;
let connectionTour = null;
decorateIcons(document);

function updateTrace(selection) {
  traceIndex = selection?.type === 'source' ? traceSources.findIndex(source => source.id === selection.id) : -1;
  for (const [index, stop] of [...$('trace-stops').children].entries()) {
    stop.setAttribute('aria-pressed', String(index === traceIndex));
  }
  $('trace-prev').disabled = traceIndex <= 0;
  $('trace-next').textContent = traceIndex < 0 ? 'Begin source tour' : traceIndex === traceSources.length - 1 ? 'Overview' : 'Next source';
  const source = traceSources[traceIndex];
  $('trace-caption').textContent = source ? `${traceIndex + 1} / ${traceSources.length} · ${formatDate(source.date)} · ${source.title}` : 'Explore sources by publication date. Order does not establish origin or copying.';
}

function prepareTrace(result) {
  const timestamp = source => Number.isFinite(Date.parse(source.date)) ? Date.parse(source.date) : Infinity;
  traceSources = [...result.graph_nodes].sort((a, b) => timestamp(a) - timestamp(b));
  $('trace-count').textContent = `${traceSources.length} sources · ${result.graph_edges.length} connections`;
  $('trace-stops').replaceChildren(...traceSources.map((source, index) => {
    const stop = el('button', 'trace-stop', String(index + 1).padStart(2, '0'));
    stop.type = 'button';
    stop.title = `${source.title} · ${formatDate(source.date)}`;
    stop.setAttribute('aria-label', `Explore source ${index + 1}: ${source.title}, ${formatDate(source.date)}`);
    stop.addEventListener('click', () => board?.focusSource(source.id));
    return stop;
  }));
}

$('trace-prev').addEventListener('click', () => {
  if (traceIndex > 0) board?.focusSource(traceSources[traceIndex - 1].id);
});
$('trace-next').addEventListener('click', () => {
  if (traceIndex === traceSources.length - 1) { board?.select(null); board?.fit(); }
  else if (traceSources.length) board?.focusSource(traceSources[traceIndex + 1].id);
});

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// The case file shown when nothing is selected.
function renderSummary(result, page) {
  const body = $('dossier-body');
  body.replaceChildren();
  const stamp = el('div', `status-badge ${result.status}`, STATUSES[result.status].label);
  const explanation = el('p', 'summary-explanation');
  const byId = new Map(result.sources.map((source) => [source.id, source]));
  const onBoard = new Set(result.graph_nodes.map((node) => node.id));
  for (const part of result.explanation.split(/(\[[A-Za-z0-9_-]+\])/g)) {
    const id = part.slice(1, -1);
    if (part.startsWith('[') && part.endsWith(']') && byId.has(id) && onBoard.has(id) && result.cited_source_ids.includes(id)) {
      const cite = el('button', 'cite', part);
      cite.type = 'button';
      cite.setAttribute('aria-label', `Show source ${id} on the board: ${byId.get(id).title}`);
      cite.addEventListener('click', () => board?.select({ type: 'source', id }));
      explanation.append(cite);
    } else explanation.append(document.createTextNode(part));
  }
  body.append(stamp, el('p', 'summary-note', STATUSES[result.status].note), explanation);
  if (page?.url) {
    const from = el('p', 'summary-from', 'Checked on ');
    const link = el('a', '', page.title || new URL(page.url).hostname);
    link.href = page.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    from.append(link);
    body.append(from);
  }
  body.append(el('h4', 'dossier-heading', 'On the board'));
  const facts = el('dl', 'dossier-facts');
  const earliest = result.earliest_observed_source;
  facts.append(el('dt', '', 'Sources'), el('dd', '', String(result.graph_nodes.length)),
    el('dt', '', 'Connections'), el('dd', '', String(result.graph_edges.length)),
    el('dt', '', 'Earliest dated'), el('dd', '', earliest ? `${earliest.id}, ${formatDate(earliest.date)}` : 'No reliable date'));
  body.append(facts);
  if (!result.graph_edges.length) body.append(el('p', 'dossier-none', 'No evidence-backed connections were found among these sources.'));
  const connections = el('details');
  connections.append(el('summary', '', `Inspect connection evidence (${result.graph_edges.length})`));
  result.graph_edges.forEach((edge, index) => {
    const inspect = el('button', 'dossier-follow', edgeHeadline(edge)); inspect.type = 'button';
    inspect.addEventListener('click', () => board?.select({ type: 'edge', index })); connections.append(inspect);
  });
  const shared = el('div', 'shared-references');
  renderSharedReferences(shared, result, next => board?.select(next));
  body.append(connections, shared);
  body.append(el('p', 'summary-hint', 'Select a card to read a source, or a string to see why two sources are connected. Drag the board to move it, scroll to zoom, and drag a card to rearrange it.'),
    el('p', 'summary-hint', 'The earliest dated source is based on available publication dates. It does not establish who first made the claim.'));
}

function show(investigation) {
  board?.destroy();
  board = null;
  connectionTour = null;
  let result = null;
  try { result = investigation?.result && normalizeResult(investigation.result); } catch { result = null; }
  const ready = Boolean(result?.graph_nodes.length);
  $('board-empty').hidden = ready;
  for (const id of ['case', 'dossier', 'board', 'trace', 'connection-walkthrough']) $(id).hidden = !ready;
  document.querySelector('.tools').hidden = !ready;
  document.querySelector('.plaque').hidden = !ready;
  $('mock-warning').hidden = !(ready && investigation.mock);
  if (!ready) {
    if (result) $('board-empty').querySelector('p').textContent = 'This investigation returned no sources, so there is nothing to pin. A lack of evidence does not make a claim false.';
    return;
  }
  $('claim').textContent = result.claim;
  document.title = `${result.claim} · TruthTrace board`;
  prepareTrace(result);
  const onSelect = (selection) => {
    updateTrace(selection);
    connectionTour?.sync(selection);
    $('reset').disabled = !selection;
    $('dossier').classList.toggle('is-open', Boolean(selection));
    if (!selection) { renderSummary(result, investigation.page); return; }
    renderDetail($('dossier-body'), selection, result, {
      onFollow: (next) => board.select(next), onClose: () => board.select(null),
    });
    $('dossier').scrollTop = 0;
  };
  board = createBoard($('board'), result, { onSelect, inset: { top: 50, bottom: matchMedia('(max-width: 760px)').matches ? 0 : 96 } });
  connectionTour = createWalkthrough($('connection-walkthrough'), result, next => board.select(next));
  onSelect(null);
}

$('zoom-in').addEventListener('click', () => board?.zoomBy(1.3));
$('zoom-out').addEventListener('click', () => board?.zoomBy(1 / 1.3));
$('fit').addEventListener('click', () => board?.fit());
$('reset').addEventListener('click', () => board?.select(null));
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') board?.select(null); });

const stored = await chrome.storage.session.get('investigation');
show(stored.investigation);
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'session' && changes.investigation) show(changes.investigation.newValue);
});
