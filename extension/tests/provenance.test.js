import { test } from 'node:test';
import assert from 'node:assert/strict';
import { provenanceLines } from '../src/evidence.js';
test('source, video, full analysis and unknown clocks stay separate', () => {
  const lines = provenanceLines({ checked_at: null, provenance: {
    source_trace: { cache_hit: true, storage_backend: 'local_json', retrieved_at: '2026-01-01T00:00:00Z', saved: true, serp_requests: 0, page_fetches: 0 },
    video_extraction: { cache_hit: true, storage_backend: 'memory' }, saved_analysis: { saved: false, storage_backend: 'none' },
  } }).join('\n');
  assert.match(lines, /local JSON backup source cache/);
  assert.match(lines, /memory cache; extracted at: unknown/);
  assert.match(lines, /Other analysis calls are separate/);
  assert.match(lines, /Checked at: unknown/);
  assert.doesNotMatch(lines, /loaded from Neon/);
});

test('grouped citation markers preserve the validated source set after normalization', async () => {
  const { normalizeResult } = await import('../src/result.js');
  const raw = { investigation_id: 'test', claim: 'Claim', status: 'insufficient_evidence',
    explanation: 'Inconclusive [s1, s2]. Unknown [s9].', cited_source_ids: ['s1', 's2'],
    sources: ['s1', 's2'].map((id) => ({ id, title: id, url: `https://example.org/${id}` })), graph_nodes: [], graph_edges: [] };
  assert.deepEqual(normalizeResult(raw).cited_source_ids, ['s1', 's2']);
  assert.deepEqual(normalizeResult(normalizeResult(raw)).cited_source_ids, ['s1', 's2']);
});
