export const STATUSES = {
  supported: { label: 'Supported', note: 'The retrieved evidence supports this claim.' },
  contradicted: { label: 'Contradicted', note: 'The retrieved evidence directly disagrees with this claim.' },
  misleading: { label: 'Misleading', note: 'The evidence adds context that changes the claim’s meaning.' },
  insufficient_evidence: { label: 'Insufficient evidence', note: 'There is not enough evidence for a reliable conclusion.' },
};

export const RELATIONSHIPS = {
  explicit_citation: 'Cites',
  similar_wording: 'Similar wording',
  same_fact_check: 'Same fact-check',
};

export function safeUrl(value) {
  if (typeof value !== 'string') return null;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function evidenceText(evidence) {
  if (typeof evidence === 'string') return evidence.trim();
  if (!evidence || typeof evidence !== 'object') return '';
  const excerpt = typeof evidence.excerpt === 'string' ? evidence.excerpt.trim() : '';
  const hyperlink = safeUrl(evidence.hyperlink);
  if (!excerpt && !hyperlink) return '';
  return [excerpt, evidence.target_excerpt && `Other source: ${evidence.target_excerpt}`, hyperlink && `Observed link: ${hyperlink}`, evidence.note]
    .filter((value) => typeof value === 'string' && value).join('\n\n');
}

function uniqueById(items) {
  const seen = new Set();
  return items.filter((item) => !seen.has(item.id) && seen.add(item.id));
}

export function normalizeResult(raw) {
  if (!raw || typeof raw.claim !== 'string' || !raw.claim.trim() || typeof raw.explanation !== 'string'
    || !Object.hasOwn(STATUSES, raw.status) || !Array.isArray(raw.sources)
    || !Array.isArray(raw.graph_nodes) || !Array.isArray(raw.graph_edges) || typeof raw.investigation_id !== 'string') {
    throw new Error('The backend response does not match the API contract. Check that the frontend and backend are up to date.');
  }
  const sources = uniqueById(raw.sources.filter((s) => s && typeof s.id === 'string' && s.id && typeof s.title === 'string' && safeUrl(s.url))
    .map((s) => ({ id: s.id, title: s.title, url: safeUrl(s.url), date: typeof s.date === 'string' ? s.date : null, excerpt: typeof s.excerpt === 'string' ? s.excerpt : '' })));
  const byId = new Map(sources.map((s) => [s.id, s]));
  const nodes = uniqueById(raw.graph_nodes.filter((n) => n && byId.has(n.id) && safeUrl(n.url) === byId.get(n.id).url)
    .map((n) => ({ ...byId.get(n.id), kind: n.kind || 'article' })));
  const nodeIds = new Set(nodes.map((n) => n.id));
  const edges = raw.graph_edges.filter((e) => e && nodeIds.has(e.source_id) && nodeIds.has(e.target_id)
    && e.source_id !== e.target_id && Object.hasOwn(RELATIONSHIPS, e.relationship_type) && evidenceText(e.evidence))
    .map((e) => ({ ...e, evidence: evidenceText(e.evidence) }));
  const earliest = raw.earliest_observed_source;
  const matchingEarliest = earliest && byId.get(earliest.id);
  const datedEarliest = matchingEarliest?.date && Number.isFinite(Date.parse(matchingEarliest.date));
  return {
    ...raw, sources, graph_nodes: nodes, graph_edges: edges,
    earliest_observed_source: datedEarliest && matchingEarliest.url === safeUrl(earliest.url) ? matchingEarliest : null,
    status: sources.length ? raw.status : 'insufficient_evidence',
    explanation: sources.length ? raw.explanation : 'No usable sources were returned. This does not establish that the claim is false.',
  };
}

export function formatDate(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Date unavailable';
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(new Date(value));
}
