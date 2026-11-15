import { safeUrl, STATUSES } from './result.js';

export const MATERIAL_LABELS = {
  fetched_article: 'Fetched article text', search_snippet: 'Search snippet — article body unavailable or not supplied',
  social_snippet: 'Social search snippet — post not retrieved', fact_check_summary: 'Published fact-check summary',
  unknown: 'Material provenance unknown',
};
const wording = (text) => String(text || '').replace(/\s+/gu, ' ').trim();

export function normalizeEvidenceSource(source) {
  return {
    evidence_kind: Object.hasOwn(MATERIAL_LABELS, source.evidence_kind) ? source.evidence_kind : 'unknown',
    retrieved_at: typeof source.retrieved_at === 'string' ? source.retrieved_at : null,
    provenance: source.provenance && typeof source.provenance === 'object' ? source.provenance : {},
    passages: Array.isArray(source.passages) ? source.passages.filter((p) => p && typeof p.id === 'string'
      && p.kind === 'fetched_article' && typeof p.text === 'string' && p.text.length > 0 && p.text.length <= 900).slice(0, 2) : [],
  };
}

export function matchedQuotes(result) {
  const sources = new Map((result.sources || []).map((s) => [s.id, s]));
  return (Array.isArray(result.evidence_quotes) ? result.evidence_quotes : []).slice(0, 8).filter((q) => {
    const source = sources.get(q.source_id);
    if (!source || q.wording_verified !== true || !wording(q.quote)) return false;
    const material = q.passage_id ? source.passages?.find((p) => p.id === q.passage_id)?.text
      : ['search_snippet', 'social_snippet', 'fact_check_summary'].includes(source.evidence_kind) ? source.excerpt?.slice(0, 600) : '';
    return wording(material).includes(wording(q.quote));
  });
}

function add(parent, tag, text) {
  const node = parent.ownerDocument.createElement(tag);
  node.textContent = text;
  parent.append(node);
  return node;
}

export function renderEvidenceInspector(container, result) {
  container.replaceChildren();
  container.classList.add('tt-evidence');
  add(container, 'h3', 'Claim and evidence');
  add(container, 'p', result.claim);
  add(container, 'p', STATUSES[result.status]?.label || 'Insufficient evidence');
  add(container, 'p', result.explanation || 'No explanation supplied.');
  if (result.claim_source) {
    add(container, 'p', 'Model-detected video statement — not matched to an independent transcript');
    add(container, 'blockquote', result.claim_source.quote || 'Statement wording unavailable.');
    const url = safeUrl(result.claim_source.timestamp_url);
    if (url) { const link = add(container, 'a', 'Replay detected statement'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
  }
  const quotes = matchedQuotes(result);
  if (!quotes.length) add(container, 'p', 'No matched quotation supplied. Missing or inconclusive material does not establish that the claim is false.');
  for (const source of (result.sources || [])) {
    const card = add(container, 'article', '');
    add(card, 'h4', `[${source.id}] ${source.title}`);
    add(card, 'p', MATERIAL_LABELS[source.evidence_kind] || MATERIAL_LABELS.unknown);
    const url = safeUrl(source.url);
    if (url) { const link = add(card, 'a', 'Open source'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    const matches = quotes.filter((q) => q.source_id === source.id);
    for (const quote of matches) {
      add(card, 'blockquote', quote.quote);
      add(card, 'p', quote.explanation || 'Interpretation unavailable.');
      add(card, 'small', 'Wording matched to supplied material; interpretation is not verified.');
    }
    if (!matches.length) {
      for (const passage of source.passages || []) add(card, 'blockquote', passage.text);
      if (!source.passages?.length) add(card, 'p', source.excerpt || 'No source text available.');
      else add(card, 'small', 'Retrieved passage; no matched quotation selected by analysis.');
    }
  }
  add(container, 'small', 'Links and shared wording do not establish copying, agreement, origin, or source independence.');
}
