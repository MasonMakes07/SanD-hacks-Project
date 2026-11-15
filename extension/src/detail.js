// The dossier: what a selected source card or string says, in words.
// Shared by the side panel and the full investigation board.
import { formatDate, safeUrl } from './result.js';
import { RELATION_INFO, hostOf, kindLabel } from './board.js';
import { MATERIAL_LABELS } from './evidence.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function swatch(type) {
  const mark = el('i', `yarn-key yarn-${type}`);
  mark.setAttribute('aria-hidden', 'true');
  return mark;
}

// Why two sources are joined, stated no more strongly than the evidence allows.
export function whyConnected(edge) {
  const a = edge.source_id; const b = edge.target_id;
  if (edge.relationship_type === 'explicit_citation') return `${a} contains a direct link or citation to ${b}. A citation does not imply agreement.`;
  if (edge.relationship_type === 'similar_wording') return `${a} and ${b} use closely matching wording. This is an inferred similarity and does not prove copying.`;
  return `${a} and ${b} are tied to the same published fact-check.`;
}

export function edgeHeadline(edge) {
  if (edge.relationship_type === 'explicit_citation') return `${edge.source_id} cites ${edge.target_id}`;
  if (edge.relationship_type === 'similar_wording') return `${edge.source_id} and ${edge.target_id} share similar wording`;
  return `${edge.source_id} and ${edge.target_id} share a fact-check`;
}

function relationFrom(edge, id) {
  const outgoing = edge.source_id === id;
  const other = outgoing ? edge.target_id : edge.source_id;
  if (edge.relationship_type === 'explicit_citation') return { other, text: outgoing ? `Cites ${other}` : `Cited by ${other}` };
  if (edge.relationship_type === 'similar_wording') return { other, text: `Similar wording with ${other}` };
  return { other, text: `Same fact-check as ${other}` };
}

function sourceButton(node, onFollow) {
  const button = el('button', 'dossier-source');
  button.type = 'button';
  button.append(el('span', 'dossier-source-id', node.id), el('span', 'dossier-source-title', node.title), el('span', 'dossier-source-host', hostOf(node.url)));
  button.addEventListener('click', () => onFollow({ type: 'source', id: node.id }));
  return button;
}

export function renderConnectionEvidence(edge) {
  const content = el('div', 'connection-evidence');
  const detail = edge.evidence_detail;
  if (!detail) {
    content.append(el('p', 'dossier-evidence', edge.evidence),
      el('p', 'dossier-basis', 'Legacy relationship evidence; separate passage and retrieval metadata are unavailable.'));
    return content;
  }
  const snippet = /snippet/i.test(`${detail.method || ''} ${detail.note || ''}`);
  const passages = el('div', 'evidence-passages');
  for (const [id, excerpt] of [[edge.source_id, detail.excerpt], [edge.target_id, detail.target_excerpt]]) {
    const passage = el('div');
    passage.append(el('h4', 'dossier-heading', `${id} · ${snippet ? 'Search snippet' : 'Observed material'}`),
      el(excerpt ? 'blockquote' : 'p', 'dossier-evidence', typeof excerpt === 'string' && excerpt ? excerpt : 'No separate passage supplied.'));
    passages.append(passage);
  }
  content.append(passages);
  const hyperlink = safeUrl(detail.hyperlink);
  if (hyperlink) {
    const observed = el('a', 'dossier-open', `Observed link: ${hyperlink}`);
    observed.href = hyperlink; observed.target = '_blank'; observed.rel = 'noopener noreferrer';
    content.append(observed);
  }
  if (typeof detail.method === 'string') content.append(el('p', 'dossier-basis', `Observation method: ${detail.method}`));
  if (typeof detail.note === 'string') content.append(el('p', 'dossier-basis', detail.note));
  if (snippet) content.append(el('p', 'dossier-basis', 'Snippet wording is not a verified article passage.'));
  return content;
}

export function renderDetail(target, selection, result, { onFollow = () => {}, onClose = null } = {}) {
  for (const animation of target.getAnimations()) animation.cancel();
  target.replaceChildren();
  if (!selection) return;
  // One entrance for the whole sheet, so the change of subject reads as a single move.
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    target.animate([{ opacity: 0.65, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }], { duration: 180, easing: 'cubic-bezier(.16, 1, .3, 1)' });
  }
  const byId = new Map([...result.sources, ...result.graph_nodes].map((node) => [node.id, node]));
  if (onClose) {
    const close = el('button', 'dossier-close', 'Reset focus');
    close.type = 'button';
    close.addEventListener('click', onClose);
    target.append(close);
  }

  if (selection.type === 'source') {
    const node = byId.get(selection.id);
    if (!node) return;
    target.append(el('h3', 'dossier-title', node.title), el('p', 'dossier-kind', `Source ${node.id} · ${kindLabel(node.kind)}`));
    const facts = el('dl', 'dossier-facts');
    facts.append(el('dt', '', 'Publisher'), el('dd', '', hostOf(node.url)), el('dt', '', 'Published'), el('dd', '', formatDate(node.date)));
    target.append(facts);
    target.append(el('p', 'dossier-basis', MATERIAL_LABELS[node.evidence_kind] || MATERIAL_LABELS.unknown));
    if (node.excerpt) target.append(el('p', 'dossier-basis', 'Retrieved excerpt; not automatically a verified passage.'), el('blockquote', 'dossier-excerpt', node.excerpt));
    for (const passage of node.passages || []) target.append(el('h4', 'dossier-heading', `Fetched article passage ${passage.id}`), el('blockquote', 'dossier-excerpt', passage.text));
    const open = el('a', 'dossier-open', 'Open original source');
    open.href = node.url; open.target = '_blank'; open.rel = 'noopener noreferrer';
    target.append(open, el('p', 'dossier-url', node.url));
    const related = result.graph_edges.map((edge, index) => ({ edge, index }))
      .filter(({ edge }) => edge.source_id === node.id || edge.target_id === node.id);
    target.append(el('h4', 'dossier-heading', `Connections (${related.length})`));
    if (!related.length) {
      target.append(el('p', 'dossier-none', 'No evidence-backed connections involve this source.'));
      return;
    }
    const list = el('ul', 'dossier-links');
    for (const { edge, index } of related) {
      const { other, text } = relationFrom(edge, node.id);
      const item = el('li');
      const follow = el('button', 'dossier-follow');
      follow.type = 'button';
      follow.append(swatch(edge.relationship_type), el('span', '', text));
      follow.setAttribute('aria-label', `${text}: ${byId.get(other).title}. Follow this string.`);
      follow.addEventListener('click', () => onFollow({ type: 'edge', index }));
      const otherLink = el('button', 'dossier-other', byId.get(other).title);
      otherLink.type = 'button';
      otherLink.addEventListener('click', () => onFollow({ type: 'source', id: other }));
      item.append(follow, otherLink, el('p', 'dossier-basis', RELATION_INFO[edge.relationship_type].basis), el('p', 'dossier-evidence', edge.evidence));
      list.append(item);
    }
    target.append(list);
    return;
  }

  const edge = result.graph_edges[selection.index];
  if (!edge) return;
  const info = RELATION_INFO[edge.relationship_type];
  const kind = el('p', 'dossier-kind');
  kind.append(swatch(edge.relationship_type), document.createTextNode(` Connection · ${info.basis}`));
  target.append(el('h3', 'dossier-title', edgeHeadline(edge)), kind);
  const pair = el('div', 'dossier-pair');
  pair.append(sourceButton(byId.get(edge.source_id), onFollow), sourceButton(byId.get(edge.target_id), onFollow));
  target.append(pair,
    el('h4', 'dossier-heading', 'Why they are connected'), el('p', 'dossier-why', whyConnected(edge)),
    el('h4', 'dossier-heading', 'Supporting evidence'), renderConnectionEvidence(edge));
  for (const id of [edge.source_id, edge.target_id]) {
    const source = byId.get(id);
    if (!source) continue;
    const open = el('a', 'dossier-open', `Open ${id}: ${source.title}`);
    open.href = source.url; open.target = '_blank'; open.rel = 'noopener noreferrer';
    target.append(open);
  }
}
