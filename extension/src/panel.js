import { loadConfig, saveConfig } from './config.js';
import { apiRequest } from './api.js';
import { STATUSES, formatDate, normalizeResult, safeUrl } from './result.js';
import { renderGraph, destroyGraph } from './graph.js';
import { createDetective } from './chat.js';
import { createPresentation } from './presentation.js';
import { createLoading } from './loading.js';
import { copyEvidenceReport } from './report.js';
import { decorateIcons } from './artwork.js';

const $ = (id) => document.getElementById(id);
let config;
let backendMock = false;
let busy = false;
let controller;
let lastKind = 'selection';
let current = null;
let presentation = null;
const loading = createLoading({ title: $('loading-title'), text: $('loading-text'), elapsed: $('loading-elapsed') });
decorateIcons(document);
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
  if (!value) loading.stop();
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
      citation.setAttribute('aria-label', `Inspect source ${source.id}: ${source.title}`);
      citation.addEventListener('click', event => { event.preventDefault(); presentation?.select({ type: 'source', id: source.id }, { focus: true }); });
      fragment.append(citation);
    } else fragment.append(document.createTextNode(part));
  }
  $('explanation').replaceChildren(fragment);
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
  let board = null;
  presentation = createPresentation(result, { getBoard: () => board });
  $('report-message').textContent = '';
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
  $('edge-count').textContent = `(${result.graph_edges.length})`;
  $('connections').hidden = !result.graph_edges.length;
  $('connections').open = false;
  $('no-edges').hidden = result.graph_edges.length > 0;
  $('investigation-id').textContent = `Investigation ${result.investigation_id}`;
  $('results').hidden = false;
  $('edge-detail').replaceChildren();
  $('open-board').hidden = !result.graph_nodes.length;
  board = renderGraph(result, {
    container: $('network'), onSelect: selection => presentation.onBoardSelect(selection),
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

async function investigate(kind) {
  if (busy || !config) return;
  lastKind = kind;
  controller = new AbortController();
  const activeController = controller;
  $('error').hidden = true;
  $('results').hidden = true;
  $('empty').hidden = true;
  destroyGraph();
  loading.start('capture', { kind });
  setBusy(true);
  try {
    const window = await chrome.windows.getCurrent();
    const response = await chrome.runtime.sendMessage({ type: 'CAPTURE', windowId: window.id, kind });
    if (activeController.signal.aborted) return;
    if (!response?.ok) throw new Error(response?.error || 'Unable to read this tab. Click the TruthTrace toolbar icon and retry.');
    const capture = response.capture;
    if (!safeUrl(capture.url) || !capture.text?.trim()) throw new Error('No readable article text was found. Select a sentence and try again.');
    loading.start('analysis', { timeoutMs: config.requestTimeoutMs });
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

$('copy-report').addEventListener('click', async () => {
  if (!current) return;
  const snapshot = current;
  try {
    await copyEvidenceReport(snapshot.result, snapshot.capture, { synthetic: !$('mock-warning').hidden });
    if (current === snapshot) $('report-message').textContent = 'Evidence report copied. Paste it into your notes.';
  } catch {
    if (current === snapshot) $('report-message').textContent = 'Could not copy the report. Allow clipboard access and try again.';
  }
});

$('settings-toggle').addEventListener('click', () => showSettings($('settings').hidden));
$('check-selection').addEventListener('click', () => investigate('selection'));
$('analyze-page').addEventListener('click', () => investigate('article'));
$('retry').addEventListener('click', () => investigate(lastKind));
$('cancel').addEventListener('click', () => controller?.abort());
$('reconnect').addEventListener('click', checkHealth);
$('open-board').addEventListener('click', () => chrome.tabs.create({ url: chrome.runtime.getURL('board.html') }));
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
