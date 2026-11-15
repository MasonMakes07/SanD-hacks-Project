import { loadConfig, saveConfig } from './config.js';
import { apiRequest } from './api.js';
import { STATUSES, formatDate, normalizeResult, safeUrl } from './result.js';
import { renderGraph } from './graph.js';
import { renderDetail, edgeHeadline } from './detail.js';
import { RELATION_INFO } from './board.js';
import { createDetective } from './chat.js';
import { createLifecycle } from './lifecycle.js';

const $ = (id) => document.getElementById(id);
let config;
let backendMock = false;
let busy = false;
let current = null;
const detective = createDetective({ getConfig: () => config, onPin: pinEvidence });

function link(text, url, className = '') {
  const anchor = document.createElement('a');
  anchor.textContent = text;
  anchor.href = url;
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  anchor.className = className;
  return anchor;
}

function showSettings(open) {
  $('settings').hidden = !open;
  $('settings-toggle').setAttribute('aria-expanded', String(open));
  if (open) $('backend-url').focus();
}

function setBusy(value) {
  busy = value;
  $('check-selection').disabled = value || !config;
  $('analyze-page').disabled = value || !config;
  $('settings-form').querySelector('button').disabled = value;
  $('loading').hidden = !value;
  $('results').setAttribute('aria-busy', String(value));
}

function showError(message) {
  $('error-message').textContent = message;
  $('error').hidden = false;
}

async function checkHealth() {
  if (!config) return;
  $('reconnect').disabled = true;
  $('connection-label').textContent = 'Connecting…';
  try {
    const health = await apiRequest(config, '/api/health', { timeoutMs: 5000 });
    if (health.status !== 'ok' || typeof health.mock !== 'boolean') throw new Error('Unexpected health response');
    backendMock = health.mock;
    $('connection').className = 'online';
    $('connection-label').textContent = health.mock ? 'Mock backend · synthetic data' : 'Backend connected';
  } catch {
    $('connection').className = 'offline';
    $('connection-label').textContent = 'Backend offline · check settings';
  } finally { $('reconnect').disabled = false; }
}

function renderExplanation(text, sources) {
  const byId = new Map(sources.map((s) => [s.id, s]));
  const fragment = document.createDocumentFragment();
  for (const part of text.split(/(\[[A-Za-z0-9_-]+\])/g)) {
    const source = byId.get(part.slice(1, -1));
    if (part.startsWith('[') && part.endsWith(']') && source) {
      const citation = link(part, source.url, 'citation-link');
      citation.setAttribute('aria-label', `Open source ${source.id}: ${source.title}`);
      fragment.append(citation);
    } else fragment.append(document.createTextNode(part));
  }
  $('explanation').replaceChildren(fragment);
}

function renderSources(sources, nodes) {
  $('sources').replaceChildren();
  $('source-count').textContent = String(sources.length);
  $('no-sources').hidden = sources.length > 0;
  const socialIds = new Set(nodes.filter((node) => node.kind === 'social_post').map((node) => node.id));
  for (const source of sources) {
    const card = document.createElement('article');
    card.className = 'source-card';
    card.tabIndex = -1;
    const meta = document.createElement('div');
    meta.className = 'source-meta';
    const id = document.createElement('span');
    id.className = 'source-id'; id.textContent = source.id;
    const date = document.createElement('span'); date.textContent = formatDate(source.date);
    meta.append(id, date);
    if (socialIds.has(source.id)) {
      const social = document.createElement('span');
      social.className = 'source-social'; social.textContent = 'Social post · search snippet';
      meta.append(social);
    }
    const host = document.createElement('div');
    host.className = 'source-host'; host.textContent = new URL(source.url).hostname;
    const excerpt = document.createElement('p'); excerpt.textContent = source.excerpt;
    card.append(meta, link(source.title, source.url), host, excerpt);
    $('sources').append(card);
  }
}

function renderResult(result, capture, { fresh = true } = {}) {
  current = { result, capture };
  detective.setInvestigation(result, { fresh, text: capture.text || '' });
  $('claim').textContent = result.claim;
  const pageUrl = safeUrl(capture.url);
  $('checked-page').textContent = capture.title || new URL(pageUrl).hostname;
  $('checked-page').href = pageUrl;
  $('capture-note').hidden = !capture.truncated;
  $('status-badge').className = `status-badge ${result.status}`;
  $('status-badge').textContent = STATUSES[result.status].label;
  $('status-note').textContent = STATUSES[result.status].note;
  $('mock-warning').hidden = !(backendMock || /^(mock|synthetic)[-:]/i.test(result.investigation_id));
  renderExplanation(result.explanation, result.sources);
  renderSources(result.sources, result.graph_nodes);
  const count = result.graph_nodes.length;
  $('coverage-note').textContent = `${count} sources · ${result.graph_edges.length} evidenced connections` +
    (count < 7 ? ' · Limited coverage: fewer than 7 relevant sources returned.' : '');
  $('earliest').replaceChildren();
  const earliest = result.earliest_observed_source;
  if (earliest) {
    const date = document.createElement('span');
    date.className = 'earliest-date'; date.textContent = formatDate(earliest.date);
    $('earliest').append(link(earliest.title, earliest.url), date);
  } else $('earliest').textContent = 'No source with a reliable publication date was returned.';
  $('edge-list').replaceChildren();
  for (const edge of result.graph_edges) {
    const item = document.createElement('li');
    const title = document.createElement('strong');
    title.textContent = edgeHeadline(edge);
    const basis = document.createElement('span');
    basis.className = `basis-chip basis-${edge.relationship_type}`; basis.textContent = RELATION_INFO[edge.relationship_type].basis;
    const evidence = document.createElement('p'); evidence.textContent = edge.evidence;
    item.append(title, basis, evidence);
    $('edge-list').append(item);
  }
  $('edge-count').textContent = `(${result.graph_edges.length})`;
  $('connections').hidden = !result.graph_edges.length;
  $('connections').open = false;
  $('no-edges').hidden = result.graph_edges.length > 0;
  $('investigation-id').textContent = `Investigation ${result.investigation_id}`;
  $('results').hidden = false;
  $('edge-detail').replaceChildren();
  $('open-board').hidden = !result.graph_nodes.length;
  const board = renderGraph(result, {
    container: $('network'),
    onSelect: (selection) => renderDetail($('edge-detail'), selection, result, {
      onFollow: (next) => board.select(next), onClose: () => board.select(null),
    }),
  });
  // Hand the investigation to the full board tab. Session storage is cleared
  // when the browser closes and is readable only by this extension's pages.
  const mock = !$('mock-warning').hidden;
  chrome.storage.session?.set({ investigation: { result, mock, page: { url: pageUrl, title: capture.title || '' } } }).catch(() => {});
}

// Pins evidence the user chose from a search onto this investigation. Sources
// and strings come from the backend response; the verdict is left untouched.
function pinEvidence({ sources, nodes, edges }) {
  if (!current) return;
  const { result, capture } = current;
  const merged = normalizeResult({
    ...result,
    sources: [...result.sources, ...sources],
    graph_nodes: [...result.graph_nodes, ...nodes],
    graph_edges: [...result.graph_edges, ...edges],
  });
  renderResult(merged, capture, { fresh: false });
}

const flow = createLifecycle({
  async capture(kind) {
    const window = await chrome.windows.getCurrent();
    const response = await chrome.runtime.sendMessage({ type: 'CAPTURE', windowId: window.id, kind });
    if (!response?.ok) throw new Error(response?.error || 'Unable to read this tab. Click the TruthTrace toolbar icon and capture again.');
    const capture = response.capture;
    if (!safeUrl(capture.url) || !capture.text?.trim()) throw new Error('No readable article text was found. Select a sentence and capture again.');
    return capture;
  },
  async check(body, signal) {
    // Keep fetch in the panel: MV3 workers have a shorter fetch lifetime.
    return normalizeResult(await apiRequest(config, '/api/analyze', { body, signal }));
  },
  onState(detail) {
    const pending = ['capture', 'analysis'].includes(detail.phase);
    if (pending) {
      $('error').hidden = true;
      $('attempt-note').textContent = '';
      $('empty').hidden = true;
      $('loading-title').textContent = detail.phase === 'capture' ? 'Reading your capture…' : 'Checking one claim…';
      $('loading-text').textContent = detail.phase === 'capture'
        ? 'Looking for the input you asked to check.'
        : `Waiting for the investigation response. The request deadline is ${Math.ceil(config.requestTimeoutMs / 1000)} seconds.`;
    }
    if (detail.phase === 'cancelled') {
      $('attempt-note').textContent = 'Check cancelled. Already-started server work may continue.';
      $('retry').textContent = flow.retrySnapshot ? 'Retry original capture' : 'Capture again';
      $('error-message').textContent = 'The check was cancelled.';
      $('error').hidden = false;
    }
    if (!pending) $('empty').hidden = Boolean(current);
    setBusy(pending);
    // Account 3's loading UI can subscribe without guessing backend substages.
    document.dispatchEvent(new CustomEvent('truthtrace:lifecycle', { detail }));
  },
  onResult: (result, capture) => renderResult(result, capture),
  onError(error) {
    $('retry').textContent = flow.retrySnapshot ? 'Retry original capture' : 'Capture again';
    showError(error.message || 'The check could not be completed. Please retry.');
    if (error.code === 'missing_key' || error.code === 'unauthorized') showSettings(true);
    if (error.code === 'offline') {
      $('connection').className = 'offline';
      $('connection-label').textContent = 'Backend offline · check settings';
    }
  },
});

function investigate(kind) {
  if (!config || busy) return;
  return flow.start(kind);
}

$('settings-toggle').addEventListener('click', () => showSettings($('settings').hidden));
$('check-selection').addEventListener('click', () => investigate('selection'));
$('analyze-page').addEventListener('click', () => investigate('article'));
$('retry').addEventListener('click', () => flow.retry());
$('cancel').addEventListener('click', () => flow.cancel());
window.addEventListener('pagehide', () => flow.cancel());
$('reconnect').addEventListener('click', checkHealth);
$('open-board').addEventListener('click', () => chrome.tabs.create({ url: chrome.runtime.getURL('board.html') }));
$('settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy) return;
  try {
    config = await saveConfig({ backendUrl: $('backend-url').value, apiKey: $('api-key').value, requestTimeoutMs: config?.requestTimeoutMs ?? 100000 });
    $('settings-message').textContent = 'Connection saved.';
    setBusy(false);
    await checkHealth();
  } catch (error) { $('settings-message').textContent = error.message; }
});

try {
  config = await loadConfig();
  $('backend-url').value = config.backendUrl;
  $('api-key').value = config.apiKey;
  setBusy(false);
  if (!config.apiKey) showSettings(true);
  await checkHealth();
} catch (error) {
  $('backend-url').value = 'http://127.0.0.1:8000';
  showSettings(true);
  showError(error.message);
}
