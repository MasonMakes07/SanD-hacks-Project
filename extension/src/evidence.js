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
    if (!source || q.wording_verified !== true || !wording(q.quote)
      || (Array.isArray(result.cited_source_ids) && !result.cited_source_ids.includes(q.source_id))) return false;
    const material = q.passage_id ? source.passages?.find((p) => p.id === q.passage_id)?.text
      : ['search_snippet', 'social_snippet', 'fact_check_summary'].includes(source.provenance?.excerpt_kind || source.evidence_kind) ? source.excerpt?.slice(0, 600) : '';
    return wording(material).includes(wording(q.quote));
  });
}

function add(parent, tag, text) {
  const node = parent.ownerDocument.createElement(tag);
  node.textContent = text;
  parent.append(node);
  return node;
}

export function renderEvidenceInspector(container, result, { onSelect } = {}) {
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
    card.dataset.sourceId = source.id;
    const selection = add(card, 'span', 'Selected evidence'); selection.className = 'selection-cue'; selection.hidden = true;
    if (onSelect) {
      const inspect = add(card, 'button', `Inspect ${source.id} on the board`); inspect.type = 'button'; inspect.className = 'secondary';
      inspect.addEventListener('click', () => onSelect({ type: 'source', id: source.id }));
    }
    add(card, 'h4', `[${source.id}] ${source.title}`);
    add(card, 'p', MATERIAL_LABELS[source.evidence_kind] || MATERIAL_LABELS.unknown);
    if (source.provenance?.role === 'originating_article') add(card, 'p', 'Originating article — input context, not independent corroboration.');
    const url = safeUrl(source.url);
    if (url) { const link = add(card, 'a', 'Open source'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    const matches = quotes.filter((q) => q.source_id === source.id);
    for (const quote of matches) {
      add(card, 'p', MATERIAL_LABELS[quote.material_kind || (quote.passage_id ? 'fetched_article' : source.provenance?.excerpt_kind || source.evidence_kind)] || MATERIAL_LABELS.unknown);
      add(card, 'blockquote', quote.quote);
      add(card, 'p', quote.explanation || 'Interpretation unavailable.');
      add(card, 'small', 'Wording matched to supplied material; interpretation is not verified.');
    }
    if (!matches.length) {
      for (const passage of source.passages || []) add(card, 'blockquote', passage.text);
      if (!source.passages?.length) {
        if (source.evidence_kind === 'fetched_article') add(card, 'p', 'No relevant fetched passage supplied. Any excerpt below is a search snippet or material of unknown provenance.');
        add(card, 'p', source.excerpt || 'No source text available.');
      }
      else add(card, 'small', 'Retrieved passage; no matched quotation selected by analysis.');
    }
  }
  add(container, 'small', 'Links and shared wording do not establish copying, agreement, origin, or source independence.');
}

export function provenanceLines(result) {
  const metadata = result.provenance || {};
  const trace = metadata.source_trace;
  const storageNames = { neon_postgres: 'Neon', local_json: 'local JSON backup', mock: 'mock fixture', none: 'no storage', memory: 'memory' };
  const name = (value) => storageNames[value] || 'unknown storage';
  const time = (value) => typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : 'unknown';
  const lines = result.reassessment ? ['Source metadata describes the original retrieval; this reassessment performed model analysis only.'] : [];
  lines.push(`Checked at: ${time(result.checked_at)}`);
  if (trace) {
    lines.push(trace.storage_backend === 'mock' ? 'Source retrieval: mock fixture'
      : trace.cache_hit === true ? `Source retrieval: loaded from ${name(trace.storage_backend)} source cache`
        : trace.cache_hit === false ? `Source retrieval: live trace; ${name(trace.storage_backend)}` : 'Source retrieval: cache status unknown');
    lines.push(`Sources retrieved at: ${time(trace.retrieved_at)}`);
    lines.push(`Source trace saved: ${trace.saved === true ? 'yes' : trace.saved === false ? 'no' : 'unknown'}`);
    if (Number.isInteger(trace.serp_requests) && Number.isInteger(trace.page_fetches)) lines.push(`Source-tracing requests only: ${trace.serp_requests} searches, ${trace.page_fetches} page fetches. Other analysis calls are separate.`);
  } else lines.push('Source retrieval: provenance unknown');
  const video = metadata.video_extraction;
  lines.push(video ? `Video extraction: ${video.cache_hit === true ? `loaded from ${name(video.storage_backend)} cache` : video.cache_hit === false ? 'new model extraction' : 'cache status unknown'}; extracted at: ${time(video.extracted_at)}` : 'Video extraction: provenance unknown or not applicable');
  const saved = metadata.saved_analysis;
  lines.push(saved?.saved === true ? `Full analysis saved in ${name(saved.storage_backend)} at: ${time(saved.saved_at)}`
    : saved?.saved === false ? `Full analysis not saved (${name(saved.storage_backend)})` : 'Full-analysis storage: unknown');
  if (result.reopened) lines.push('Reopened saved result — this is not a fresh check.');
  return lines;
}

export function renderProvenance(container, result) {
  container.replaceChildren();
  container.classList.add('tt-evidence');
  for (const line of provenanceLines(result)) add(container, 'p', line);
}
