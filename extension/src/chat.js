// Ask the Detective: follow-up questions answered from this investigation's
// evidence, plus explicit searches for more or opposing evidence. Gemini and
// Bright Data are only ever reached through the local backend; no vendor key
// lives in the extension.
import { apiRequest } from './api.js';
import { formatDate, safeUrl } from './result.js';
import { normalizeEvidenceSource } from './evidence.js';

const SUGGESTIONS = [
  'What evidence contradicts this?',
  'Are these sources independent?',
  'What is the earliest reporting you can find?',
  'What evidence is missing?',
];
const FOLLOW_UP = {
  more: 'What does the newly retrieved evidence add, and is it independent of the sources already on the board?',
  counter: 'Does any of the newly retrieved evidence credibly contradict the claim? Separate what supports it, what contradicts it, and what is still unclear.',
};
const STANCE_LABELS = { supports: 'Supports the claim', contradicts: 'Contradicts the claim', unclear: 'Unclear or incomplete' };
const SEARCH_TIMEOUT_MS = 100000;
const CHAT_TIMEOUT_MS = 50000; // the backend allows two 20-second attempts
const MAX_PAGE_CHARS = 6000;
const BASIS_NOTES = {
  page: 'From the page you checked. The page is what is being investigated, so this is not verification.',
  background: 'General background from the model, not from a retrieved source. Verify it before relying on it.',
};
const ID_PATTERN = /^[A-Za-z]{1,3}\d{1,4}$/;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function sourceLink(text, url, className) {
  const anchor = el('a', className, text);
  anchor.href = url; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
  return anchor;
}

// Keep only well-formed new sources; anything else from the response is ignored.
function usableEvidence(data) {
  const sources = (Array.isArray(data?.sources) ? data.sources : [])
    .filter((s) => s && ID_PATTERN.test(s.id) && typeof s.title === 'string' && safeUrl(s.url))
    .map((s) => ({ ...normalizeEvidenceSource(s), id: s.id, title: s.title, url: safeUrl(s.url), date: typeof s.date === 'string' ? s.date : null, excerpt: typeof s.excerpt === 'string' ? s.excerpt : '' }));
  const ids = new Set(sources.map((s) => s.id));
  const nodes = (Array.isArray(data?.graph_nodes) ? data.graph_nodes : []).filter((n) => n && ids.has(n.id));
  const edges = (Array.isArray(data?.graph_edges) ? data.graph_edges : [])
    .filter((e) => e && (ids.has(e.source_id) || ids.has(e.target_id)));
  return { sources, nodes, edges };
}

export function createDetective({ getConfig, onPin }) {
  const $ = (id) => document.getElementById(id);
  const log = $('chat-log');
  let result = null;
  let pageText = '';
  let history = [];
  let pending = new Map(); // retrieved but not yet pinned: id -> { source, node, edges }
  let busy = false;
  let generation = 0;

  function setBusy(value) {
    busy = value;
    for (const control of document.querySelectorAll('.detective-card button, #chat-input')) control.disabled = value;
  }

  function scrollToEnd() {
    log.lastElementChild?.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }

  function add(node) { log.append(node); scrollToEnd(); return node; }

  // Everything the detective may cite: the board plus retrieved-but-unpinned evidence.
  function context() {
    const extra = [...pending.values()];
    const sources = [...result.sources, ...extra.map((item) => item.source)].slice(0, 40);
    const ids = new Set(sources.map((s) => s.id));
    const edges = [...result.graph_edges, ...extra.flatMap((item) => item.edges)]
      .filter((e) => ids.has(e.source_id) && ids.has(e.target_id));
    const seen = new Set();
    return {
      sources,
      edges: edges.map((e) => ({ source_id: e.source_id, target_id: e.target_id, relationship_type: e.relationship_type }))
        .filter((e) => { const key = `${e.source_id}|${e.target_id}|${e.relationship_type}`; return !seen.has(key) && seen.add(key); })
        .slice(0, 80),
    };
  }

  function renderAnswer(data, sources) {
    const byId = new Map(sources.map((s) => [s.id, s]));
    const note = el('div', 'chat-msg chat-detective');
    const body = el('p', 'chat-text');
    for (const part of String(data.answer).split(/(\[[A-Za-z0-9_, -]+\])/g)) {
      const ids = part.startsWith('[') && part.endsWith(']') ? part.slice(1, -1).split(',').map((id) => id.trim()) : [];
      if (ids.length && ids.every((id) => byId.has(id))) {
        ids.forEach((id, index) => {
          const cite = sourceLink(`[${id}]`, byId.get(id).url, 'citation-link');
          cite.setAttribute('aria-label', `Open source ${id}: ${byId.get(id).title}`);
          if (index) body.append(document.createTextNode(' '));
          body.append(cite);
        });
      } else body.append(document.createTextNode(part));
    }
    note.append(body);
    const judged = new Set(); // one stance per source: the first the backend gave
    const stances = (Array.isArray(data.stances) ? data.stances : [])
      .filter((s) => byId.has(s?.source_id) && Object.hasOwn(STANCE_LABELS, s.stance) && !judged.has(s.source_id) && judged.add(s.source_id));
    if (stances.length) {
      const list = el('dl', 'chat-stances');
      for (const stance of Object.keys(STANCE_LABELS)) {
        const matching = stances.filter((s) => s.stance === stance);
        if (!matching.length) continue;
        const value = el('dd');
        for (const item of matching) value.append(sourceLink(item.source_id, byId.get(item.source_id).url, `stance-chip stance-${stance}`));
        list.append(el('dt', '', STANCE_LABELS[stance]), value);
      }
      note.append(list);
    }
    if (Object.hasOwn(BASIS_NOTES, data.basis)) note.append(el('p', 'chat-caution', BASIS_NOTES[data.basis]));
    else if (!data.grounded) note.append(el('p', 'chat-caution', 'No retrieved source backs this answer. Treat it as reasoning, not evidence.'));
    return note;
  }

  async function ask(question, { echo = true } = {}) {
    const text = question.trim();
    if (!text || !result) return;
    const mine = generation;
    if (echo) add(el('div', 'chat-msg chat-user', text));
    const waiting = add(el('div', 'chat-msg chat-waiting', 'Going through the evidence…'));
    setBusy(true);
    try {
      const { sources, edges } = context();
      const data = await apiRequest(getConfig(), '/api/chat', {
        body: {
          question: text.slice(0, 500), claim: result.claim.slice(0, 600), status: result.status,
          explanation: result.explanation.slice(0, 2000),
          sources: sources.map((s) => ({ id: s.id, title: s.title.slice(0, 400), url: s.url, date: s.date, excerpt: s.excerpt.slice(0, 1200) })),
          edges, history: history.slice(-6), page_text: pageText,
        },
        timeoutMs: CHAT_TIMEOUT_MS,
      });
      if (mine !== generation) return;
      if (typeof data.answer !== 'string' || !data.answer.trim()) throw new Error('The detective returned an unreadable answer. Try again.');
      waiting.replaceWith(renderAnswer(data, sources));
      history.push({ role: 'user', text: text.slice(0, 1700) }, { role: 'detective', text: data.answer.slice(0, 1700) });
    } catch (error) {
      if (mine !== generation) return;
      const failed = el('div', 'chat-msg chat-error', error.message || 'The detective could not answer. Try again.');
      failed.setAttribute('role', 'alert');
      waiting.replaceWith(failed);
    } finally {
      if (mine === generation) { setBusy(false); scrollToEnd(); }
    }
  }

  function pin(ids) {
    const items = ids.map((id) => pending.get(id)).filter(Boolean);
    if (!items.length) return;
    for (const id of ids) pending.delete(id);
    onPin({
      sources: items.map((item) => item.source),
      nodes: items.map((item) => item.node),
      edges: items.flatMap((item) => item.edges),
      pinned_evidence: [...new Set(items.map((item) => item.evidence_set_id).filter(Boolean))]
        .map((evidence_set_id) => ({ evidence_set_id,
          source_ids: items.filter((item) => item.evidence_set_id === evidence_set_id).map((item) => item.source.id) })),
    });
  }

  function renderEvidence(data, evidence, mode) {
    const block = el('div', 'chat-msg chat-evidence');
    const when = Number.isFinite(Date.parse(data.retrieved_at))
      ? new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(data.retrieved_at)) : 'time unknown';
    const kind = mode === 'counter' ? 'Search for counterevidence' : 'Search for more evidence';
    block.append(el('p', 'chat-evidence-title', `${kind}: ${evidence.sources.length} new ${evidence.sources.length === 1 ? 'source' : 'sources'}`),
      el('p', 'chat-evidence-when', data.from_cache ? `Reused from an earlier search, first retrieved ${when}. No new search was spent.` : `Retrieved ${when}.`));
    if (!evidence.sources.length) {
      block.append(el('p', 'chat-text', 'The search found nothing that is not already on the board. That is not evidence for or against the claim.'));
      return block;
    }
    const list = el('ul', 'chat-evidence-list');
    for (const source of evidence.sources) {
      const item = el('li');
      const pinButton = el('button', 'pin-button', 'Pin to board');
      pinButton.type = 'button';
      pinButton.setAttribute('aria-label', `Pin ${source.id} to the board: ${source.title}`);
      pinButton.addEventListener('click', () => { pin([source.id]); pinButton.replaceWith(el('span', 'pinned-note', 'Pinned')); });
      item.append(el('span', 'source-id', source.id), sourceLink(source.title, source.url, 'chat-evidence-link'),
        el('span', 'chat-evidence-meta', `${new URL(source.url).hostname.replace(/^www\./, '')}, ${formatDate(source.date)}`), pinButton);
      list.append(item);
    }
    block.append(list);
    if (mode === 'counter') block.append(el('p', 'chat-caution', 'The verdict above has not changed. Run a new check to re-verify with different evidence.'));
    return block;
  }

  async function search(mode) {
    if (!result || busy) return;
    const mine = generation;
    const waiting = add(el('div', 'chat-msg chat-waiting', mode === 'counter' ? 'Searching for credible counterevidence…' : 'Searching for more evidence…'));
    setBusy(true);
    let found = 0;
    try {
      const known = [...result.sources, ...[...pending.values()].map((item) => item.source)]
        .filter((s) => ID_PATTERN.test(s.id)).slice(0, 60).map((s) => ({ id: s.id, url: s.url }));
      const data = await apiRequest(getConfig(), '/api/evidence/search', {
        body: { claim: result.claim.slice(0, 600), mode, known }, timeoutMs: SEARCH_TIMEOUT_MS,
      });
      if (mine !== generation) return;
      const evidence = usableEvidence(data);
      for (const source of evidence.sources) {
        pending.set(source.id, {
          evidence_set_id: data.evidence_set_id,
          source, node: evidence.nodes.find((n) => n.id === source.id) || { id: source.id, title: source.title, url: source.url, kind: 'article' },
          edges: evidence.edges.filter((e) => e.source_id === source.id || e.target_id === source.id),
        });
      }
      found = evidence.sources.length;
      waiting.replaceWith(renderEvidence(data, evidence, mode));
    } catch (error) {
      if (mine !== generation) return;
      const failed = el('div', 'chat-msg chat-error', error.message || 'The search could not be completed. Try again.');
      failed.setAttribute('role', 'alert');
      waiting.replaceWith(failed);
    } finally {
      if (mine === generation) { setBusy(false); scrollToEnd(); }
    }
    if (found && mine === generation) await ask(FOLLOW_UP[mode]);
  }

  const suggestions = $('chat-suggestions');
  for (const question of SUGGESTIONS) {
    const chip = el('button', 'chat-chip', question);
    chip.type = 'button';
    chip.addEventListener('click', () => ask(question));
    suggestions.append(chip);
  }
  $('chat-form').addEventListener('submit', (event) => {
    event.preventDefault();
    if (busy) return;
    const input = $('chat-input');
    const question = input.value;
    input.value = '';
    ask(question);
  });
  $('search-more').addEventListener('click', () => search('more'));
  $('challenge').addEventListener('click', () => search('counter'));

  return {
    // A new investigation starts a new conversation; pinning evidence keeps it.
    setInvestigation(next, { fresh, text = '' }) {
      result = next;
      pageText = text.slice(0, MAX_PAGE_CHARS);
      if (!fresh) return;
      generation += 1;
      history = []; pending = new Map();
      log.replaceChildren();
      setBusy(false);
    },
  };
}
