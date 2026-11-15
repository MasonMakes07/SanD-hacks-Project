import { citedSourceIds, sharedReferences, formatDate } from './result.js';
import { edgeHeadline, renderDetail, renderConnectionEvidence } from './detail.js';
import { createWalkthrough } from './walkthrough.js';

const el = (tag, text, className = '') => {
  const node = document.createElement(tag); node.className = className;
  if (text !== undefined) node.textContent = text; return node;
};
const button = (text, handler, className = 'secondary') => {
  const node = el('button', text, className); node.type = 'button'; node.addEventListener('click', handler); return node;
};

export function renderSharedReferences(container, result, select) {
  container.replaceChildren(el('h3', 'Shared references'));
  const groups = sharedReferences(result);
  const byId = new Map(result.sources.map(source => [source.id, source]));
  if (!groups.length) {
    container.append(el('p', result.graph_edges.length ? 'No shared citation targets were observed among these sources.' : 'No observed connections among these sources.', 'muted'));
    return;
  }
  container.append(el('p', 'Counts describe observed links, not independent confirmations or agreement.', 'muted'));
  for (const group of groups) {
    const details = el('details'); const summary = el('summary', `${group.citing.length} retrieved pages cite ${group.target_id}: ${byId.get(group.target_id)?.title || group.target_id}`);
    details.append(summary, button(`Inspect target ${group.target_id}`, () => select({ type: 'source', id: group.target_id })));
    for (const citation of group.citing) {
      const edge = result.graph_edges[citation.index];
      details.append(button(`Inspect ${edge.source_id} citing ${edge.target_id}`, () => select({ type: 'edge', index: citation.index })), renderConnectionEvidence(edge));
    }
    container.append(details);
  }
}

// One selection state drives source cards, graph and detail. Filter changes only
// the list view; selection can reveal a requested retrieved-only source.
export function createPresentation(result, { root = document, getBoard }) {
  const $ = id => root.getElementById(id);
  const cited = new Set(citedSourceIds(result));
  const cards = new Map(); const edgeButtons = [];
  let filter = 'all'; let tour;
  const select = (next, { focus = false } = {}) => {
    const valid = !next || (next.type === 'source' ? result.sources.some(s => s.id === next.id) : result.graph_edges[next.index]);
    if (!valid) next = null;
    // Sources absent from the network still have an inspectable source card.
    getBoard()?.select(next, { silent: true });
    apply(next, focus);
  };
  function apply(next, focus = false) {
    const ids = next?.type === 'source' ? [next.id] : next?.type === 'edge'
      ? [result.graph_edges[next.index].source_id, result.graph_edges[next.index].target_id] : [];
    if (filter === 'cited' && ids.some(id => !cited.has(id))) {
      filter = 'all'; updateFilter();
      $('filter-message').textContent = 'Showing all sources to reveal the requested evidence.';
    }
    for (const [id, card] of cards) {
      card.classList.toggle('is-selected', ids.includes(id));
      card.querySelector('button').setAttribute('aria-pressed', String(ids.includes(id)));
      card.querySelector('.selection-cue').hidden = !ids.includes(id);
    }
    edgeButtons.forEach((node, index) => node.setAttribute('aria-pressed', String(next?.type === 'edge' && next.index === index)));
    $('reset-selection').disabled = !next;
    renderDetail($('edge-detail'), next, result, { onFollow: select, onClose: () => select(null) });
    tour?.sync(next);
    if (focus && ids.length) {
      const card = cards.get(ids[0]);
      card?.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
      card?.focus({ preventScroll: true });
    }
  }
  const all = $('filter-all'); const used = $('filter-cited');
  all.textContent = `All (${result.sources.length})`; used.textContent = `Cited in this finding (${cited.size})`;
  function updateFilter() {
    all.setAttribute('aria-pressed', String(filter === 'all')); used.setAttribute('aria-pressed', String(filter === 'cited'));
    for (const [id, card] of cards) card.hidden = filter === 'cited' && !cited.has(id);
    $('no-cited-sources').hidden = filter !== 'cited' || cited.size > 0;
  }
  // Replacing handlers prevents accumulation when evidence is pinned/replaced.
  all.onclick = () => { filter = 'all'; $('filter-message').textContent = ''; updateFilter(); };
  used.onclick = () => { filter = 'cited'; $('filter-message').textContent = ''; updateFilter(); };
  $('reset-selection').onclick = () => select(null);
  $('sources').replaceChildren();
  $('source-count').textContent = String(result.sources.length);
  $('no-sources').hidden = result.sources.length > 0;
  $('no-evidence-art').hidden = result.sources.length > 0;
  const kinds = new Map(result.graph_nodes.map(node => [node.id, node.kind]));
  for (const source of result.sources) {
    const card = el('article', undefined, 'source-card'); card.tabIndex = -1; card.dataset.sourceId = source.id;
    const meta = el('div', undefined, 'source-meta');
    meta.append(el('span', source.id, 'source-id'), el('span', formatDate(source.date)),
      el('span', cited.has(source.id) ? 'Cited in this finding' : 'Retrieved, not cited', 'citation-membership'));
    if (kinds.get(source.id) === 'social_post') meta.append(el('span', 'Social post · search snippet', 'source-social'));
    const open = el('a', source.title); open.href = source.url; open.target = '_blank'; open.rel = 'noopener noreferrer';
    const cue = el('span', 'Selected evidence', 'selection-cue'); cue.hidden = true;
    card.append(meta, open, el('div', new URL(source.url).hostname, 'source-host'), el('p', source.excerpt),
      button(`Inspect ${source.id}`, () => select({ type: 'source', id: source.id }), 'text-button'), cue);
    cards.set(source.id, card); $('sources').append(card);
  }
  $('edge-list').replaceChildren();
  result.graph_edges.forEach((edge, index) => {
    const item = el('li'); item.dataset.edgeIndex = index;
    const action = button(edgeHeadline(edge), () => select({ type: 'edge', index }), 'connection-select');
    action.setAttribute('aria-pressed', 'false'); edgeButtons.push(action);
    item.append(action, renderConnectionEvidence(edge)); $('edge-list').append(item);
  });
  tour = createWalkthrough($('connection-walkthrough'), result, select);
  renderSharedReferences($('shared-references'), result, select);
  $('filter-message').textContent = '';
  updateFilter();
  apply(null);
  return { select, onBoardSelect: next => apply(next, next?.type === 'source') };
}
