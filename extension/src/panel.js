import { loadConfig, saveConfig } from './config.js';
import { apiRequest } from './api.js';
import { STATUSES, formatDate, normalizeResult, safeUrl } from './result.js';
import { renderGraph } from './graph.js';
import { createDetective } from './chat.js';
import { createLifecycle } from './lifecycle.js';
import { renderVideoContext } from './video-context.js';
import { createPresentation } from './presentation.js';
import { createLoading } from './loading.js';
import { copyEvidenceReport } from './report.js';
import { decorateIcons } from './artwork.js';
import { renderEvidenceInspector, renderProvenance } from './evidence.js';
import { createHistory } from './history.js';
import { createReassessment } from './reassessment.js';

const $ = (id) => document.getElementById(id);
let config;
let backendMock = false;
let busy = false;
let current = null;
let highlightedCapture = null;
let highlightCleanup = Promise.resolve();
let presentation = null;
const loading = createLoading({ title: $('loading-title'), text: $('loading-text'), elapsed: $('loading-elapsed') });
decorateIcons(document);
const detective = createDetective({ getConfig: () => config, onPin: pinEvidence });

const reassessment = createReassessment({ container: $('reassessment'), getConfig: () => config,
  onRevised: revised => { if (current) renderResult(revised, current.capture); },
});
const history = createHistory({ container: $('history'), getConfig: () => config,
  onOpen: (result, capture) => {
    flow.cancel(); loading.stop(); reassessment.cancel();
    $('error').hidden = true; $('empty').hidden = true;
    renderResult(result, capture);
  },
});

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
  $('check-video').disabled = value || !config;
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

function renderExplanation(text, sources, citedIds) {
  const cited = new Set(citedIds);
  const byId = new Map(sources.map((s) => [s.id, s]));
  const fragment = document.createDocumentFragment();
  for (const part of text.split(/(\[[A-Za-z0-9_-]+\])/g)) {
    const source = byId.get(part.slice(1, -1));
    if (part.startsWith('[') && part.endsWith(']') && source && cited.has(source.id)) {
      const citation = link(part, source.url, 'citation-link');
      citation.setAttribute('aria-label', `Inspect source ${source.id}: ${source.title}`);
      citation.addEventListener('click', event => { event.preventDefault(); presentation?.select({ type: 'source', id: source.id }, { focus: true }); });
      fragment.append(citation);
    } else fragment.append(document.createTextNode(part));
  }
  $('explanation').replaceChildren(fragment);
}

function renderResult(result, capture, { fresh = true } = {}) {
  if (fresh) clearPageHighlight();
  current = { result, capture };
  $('show-on-page').hidden = !capture.selectionAnchor;
  $('clear-highlight').hidden = highlightedCapture !== capture;
  if (fresh) $('anchor-message').textContent = capture.kind === 'selection' && !capture.selectionAnchor
    ? 'Page highlighting is unavailable for this selection. Select a short passage outside editors on a supported HTML article.' : '';
  detective.setInvestigation(result, { fresh, text: capture.text || '' });
  $('claim').textContent = result.claim;
  $('compact-claim').textContent = result.claim;
  $('compact-preview').textContent = result.claim.length > 110 ? `${result.claim.slice(0, 110)}…` : result.claim;
  $('compact-status').textContent = STATUSES[result.status].label;
  document.body.classList.add('has-result');
  $('new-check').hidden = false;
  if (fresh) {
    $('start-controls').hidden = true;
    $('new-check').textContent = 'New check';
    $('new-check').setAttribute('aria-expanded', 'false');
    $('reading-context').open = false;
  }
  const pageUrl = safeUrl(capture.url);
  $('checked-page').textContent = pageUrl ? (capture.title || new URL(pageUrl).hostname) : 'Original page context unavailable';
  if (pageUrl) $('checked-page').href = pageUrl;
  else $('checked-page').removeAttribute('href');
  $('capture-note').hidden = !capture.truncated;
  renderVideoContext($('video-context'), result, capture);
  $('status-badge').className = `status-badge ${result.status}`;
  $('status-badge').textContent = STATUSES[result.status].label;
  $('status-note').textContent = STATUSES[result.status].note;
  $('mock-warning').hidden = !(backendMock || /^(mock|synthetic)[-:]/i.test(result.investigation_id));
  renderExplanation(result.explanation, result.sources, result.cited_source_ids);
  renderEvidenceInspector($('evidence-inspector'), result, { onSelect: next => presentation?.select(next) });
  renderProvenance($('provenance'), result);
  reassessment.setInvestigation(result);
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
  if (fresh && !result.reopened) history.load();
  chrome.storage.session?.set({ investigation: { result, mock, page: { url: pageUrl, title: capture.title || '' } } }).catch(() => {});
  if (fresh) {
    $('results').scrollIntoView({ block: 'start', behavior: 'instant' });
    $('claim').tabIndex = -1;
    $('claim').focus({ preventScroll: true });
  }
}

// Pins evidence the user chose from a search onto this investigation. Sources
// and strings come from the backend response; the verdict is left untouched.
function pinEvidence({ sources, nodes, edges, pinned_evidence }) {
  if (!current) return;
  const { result, capture } = current;
  const merged = normalizeResult({
    ...result,
    sources: [...result.sources, ...sources],
    graph_nodes: [...result.graph_nodes, ...nodes],
    graph_edges: [...result.graph_edges, ...edges],
  });
  renderResult(merged, capture, { fresh: false });
  reassessment.acceptPinned(pinned_evidence);
}

function confirmClaim(proposal, capture, signal) {
  $('original-input').textContent = capture.selectionAnchor?.exact || capture.text;
  $('original-location').textContent = `${capture.title || 'Captured page'} · ${capture.url}`;
  $('approved-claim').value = proposal.claim;
  $('approved-claim').setCustomValidity('');
  $('proposal-note').textContent = proposal.input_context?.fallback_reason || 'This is a proposed standalone statement. Edits are your wording, not a verbatim page or video quotation.';
  renderVideoContext($('confirmation-video'), proposal, capture);
  $('confirmation').hidden = false;
  $('approved-claim').focus();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      $('confirmation').hidden = true;
      $('confirmation-form').removeEventListener('submit', submit);
      signal.removeEventListener('abort', abort);
    };
    const abort = () => { cleanup(); reject(Object.assign(new Error('Confirmation cancelled.'), { code: 'cancelled' })); };
    const submit = (event) => {
      event.preventDefault();
      const approved = $('approved-claim').value.trim();
      if (!approved || approved.length > 400) {
        $('approved-claim').setCustomValidity('Enter one factual statement, up to 400 characters.');
        $('approved-claim').reportValidity();
        return;

      }
      cleanup(); resolve(approved);
    };
    $('confirmation-form').addEventListener('submit', submit);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
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
  extract: (body, signal) => apiRequest(config, '/api/extract', { body, signal }),
  confirm: confirmClaim,
  async check(body, signal) {
    // Keep fetch in the panel: MV3 workers have a shorter fetch lifetime.
    return normalizeResult(await apiRequest(config, '/api/analyze', { body, signal }));
  },
  onState(detail) {
    const pending = ['capture', 'extraction', 'confirmation', 'analysis'].includes(detail.phase);
    if (pending) { history.cancel(); reassessment.cancel(); }
    if (pending) {
      $('error').hidden = true;
      $('attempt-note').textContent = '';
      $('empty').hidden = true;
      if (detail.phase !== 'confirmation') loading.start(detail.phase, { kind: detail.kind, timeoutMs: config.requestTimeoutMs });
      else loading.stop();
    }
    if (detail.phase === 'cancelled') {
      $('attempt-note').textContent = 'Check cancelled. Already-started server work may continue.';
      $('retry').textContent = flow.retrySnapshot ? 'Retry original capture' : 'Capture again';
      $('error-message').textContent = 'The check was cancelled.';
      $('error').hidden = false;
    }
    if (!pending) $('empty').hidden = Boolean(current);
    setBusy(pending);
    if (detail.phase === 'confirmation') $('loading').hidden = true;
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

async function clearPageHighlight() {
  const capture = highlightedCapture;
  highlightedCapture = null;
  $('clear-highlight').hidden = true;
  if (capture) highlightCleanup = highlightCleanup.then(() => chrome.runtime.sendMessage({ type: 'CLEAR_CAPTURE', capture }).catch(() => {}));
  await highlightCleanup;
}

$('show-on-page').addEventListener('click', async () => {
  const capture = current?.capture;
  if (!capture) return;
  $('show-on-page').disabled = true;
  try {
    await clearPageHighlight();
    const response = await chrome.runtime.sendMessage({ type: 'SHOW_CAPTURE', capture });
    if (current?.capture !== capture) {
      await chrome.runtime.sendMessage({ type: 'CLEAR_CAPTURE', capture }).catch(() => {});
      return;
    }
    if (!response?.ok) throw new Error(response?.error || 'The original selection is unavailable. Capture it again.');
    highlightedCapture = capture;
    $('clear-highlight').hidden = false;
    $('anchor-message').textContent = response.message;
  } catch (error) {
    if (current?.capture === capture) $('anchor-message').textContent = error.message;
  } finally { $('show-on-page').disabled = false; }
});
$('clear-highlight').addEventListener('click', async () => {
  await clearPageHighlight();
  $('anchor-message').textContent = 'Original selection highlight removed.';
});
window.addEventListener('pagehide', () => clearPageHighlight());

$('new-check').addEventListener('click', () => {
  const open = $('start-controls').hidden;
  $('start-controls').hidden = !open;
  $('new-check').setAttribute('aria-expanded', String(open));
  $('new-check').textContent = open ? 'Hide capture controls' : 'New check';
  if (open) $('check-selection').focus();
});
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
$('check-video').addEventListener('click', () => investigate('video'));
$('retry').addEventListener('click', () => flow.retry());
$('cancel').addEventListener('click', () => flow.cancel());
$('cancel-confirmation').addEventListener('click', () => flow.cancel());
$('approved-claim').addEventListener('input', () => $('approved-claim').setCustomValidity(''));
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
  history.load();
} catch (error) {
  $('backend-url').value = 'http://127.0.0.1:8000';
  showSettings(true);
  showError(error.message);
}

window.addEventListener('pagehide', () => { history.cancel(); reassessment.cancel(); loading.stop(); });
