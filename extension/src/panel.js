import { loadConfig, saveConfig } from './config.js';
import { apiRequest } from './api.js';
import { STATUSES, RELATIONSHIPS, formatDate, normalizeResult, safeUrl } from './result.js';
import { renderGraph, destroyGraph } from './graph.js';

const $ = (id) => document.getElementById(id);
let config;
let backendMock = false;
let busy = false;
let controller;
let lastKind = 'selection';
const sourceCards = new Map();

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

function focusSource(id) {
  const card = sourceCards.get(id);
  card?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center' });
  card?.focus({ preventScroll: true });
}

function renderSources(sources) {
  sourceCards.clear();
  $('sources').replaceChildren();
  $('source-count').textContent = String(sources.length);
  $('no-sources').hidden = sources.length > 0;
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
    const host = document.createElement('div');
    host.className = 'source-host'; host.textContent = new URL(source.url).hostname;
    const excerpt = document.createElement('p'); excerpt.textContent = source.excerpt;
    card.append(meta, link(`${source.title} ↗`, source.url), host, excerpt);
    sourceCards.set(source.id, card);
    $('sources').append(card);
  }
}

function renderResult(result, capture) {
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
  renderSources(result.sources);
  $('earliest').replaceChildren();
  const earliest = result.earliest_observed_source;
  if (earliest) {
    const date = document.createElement('span');
    date.className = 'earliest-date'; date.textContent = formatDate(earliest.date);
    $('earliest').append(link(`${earliest.title} ↗`, earliest.url), date);
  } else $('earliest').textContent = 'No source with a reliable publication date was returned.';
  $('edge-list').replaceChildren();
  for (const edge of result.graph_edges) {
    const item = document.createElement('li');
    const title = document.createElement('strong');
    title.textContent = `${edge.source_id} ${edge.relationship_type === 'explicit_citation' ? '→' : '—'} ${edge.target_id} · ${RELATIONSHIPS[edge.relationship_type]}`;
    const evidence = document.createElement('p'); evidence.textContent = edge.evidence;
    item.append(title, evidence);
    $('edge-list').append(item);
  }
  $('edge-count').textContent = `(${result.graph_edges.length})`;
  $('connections').hidden = !result.graph_edges.length;
  $('connections').open = false;
  $('no-edges').hidden = result.graph_edges.length > 0;
  $('investigation-id').textContent = `Investigation ${result.investigation_id}`;
  $('results').hidden = false;
  renderGraph(result, { container: $('network'), detail: $('edge-detail'), onSource: focusSource });
}

async function investigate(kind) {
  if (busy || !config) return;
  lastKind = kind;
  controller = new AbortController();
  const activeController = controller;
  $('error').hidden = true;
  $('results').hidden = true;
  $('empty').hidden = true;
  destroyGraph();
  $('loading-title').textContent = 'Reading the article…';
  $('loading-text').textContent = 'Looking for the text you asked to check.';
  setBusy(true);
  try {
    const window = await chrome.windows.getCurrent();
    const response = await chrome.runtime.sendMessage({ type: 'CAPTURE', windowId: window.id, kind });
    if (activeController.signal.aborted) return;
    if (!response?.ok) throw new Error(response?.error || 'Unable to read this tab. Click the TruthTrace toolbar icon and retry.');
    const capture = response.capture;
    if (!safeUrl(capture.url) || !capture.text?.trim()) throw new Error('No readable article text was found. Select a sentence and try again.');
    $('loading-title').textContent = 'Checking one claim…';
    $('loading-text').textContent = `Retrieving sources and checking the evidence. This can take up to ${Math.ceil(config.requestTimeoutMs / 1000)} seconds.`;
    // Run fetch in the persistent panel, not the MV3 worker: long requests can
    // exceed a worker's fetch lifetime. Closing the panel cancels the UI session.
    const raw = await apiRequest(config, '/api/analyze', {
      body: { text: capture.text, url: capture.url, mode: 'manual' }, signal: activeController.signal,
    });
    if (!activeController.signal.aborted) renderResult(normalizeResult(raw), capture);
  } catch (error) {
    if (error.code !== 'cancelled' && !activeController.signal.aborted) {
      showError(error.message || 'The check could not be completed. Please retry.');
      if (error.code === 'missing_key' || error.code === 'unauthorized') showSettings(true);
      if (error.code === 'offline') {
        $('connection').className = 'offline';
        $('connection-label').textContent = 'Backend offline · check settings';
      }
    }
  } finally {
    if (activeController.signal.aborted) $('empty').hidden = false;
    setBusy(false);
    controller = null;
  }
}

$('settings-toggle').addEventListener('click', () => showSettings($('settings').hidden));
$('check-selection').addEventListener('click', () => investigate('selection'));
$('analyze-page').addEventListener('click', () => investigate('article'));
$('retry').addEventListener('click', () => investigate(lastKind));
$('cancel').addEventListener('click', () => controller?.abort());
$('reconnect').addEventListener('click', checkHealth);
$('settings-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy) return;
  try {
    config = await saveConfig({ backendUrl: $('backend-url').value, apiKey: $('api-key').value, requestTimeoutMs: config?.requestTimeoutMs ?? 45000 });
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
