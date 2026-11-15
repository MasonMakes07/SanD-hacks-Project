import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { normalizeResult, citedSourceIds, sharedReferences, connectionSteps } from '../src/result.js';
import { evidenceReport, copyEvidenceReport } from '../src/report.js';
const fixture = JSON.parse(await readFile(new URL('../../source_tracing/fixtures/example_investigation_response.json', import.meta.url)));
const response = () => ({ ...structuredClone(fixture), claim: 'A claim', status: 'insufficient_evidence', explanation: 'Observed [s1], repeated [s1]. Unknown [s9] and prose [note].' });

test('citation usage resolves only safe sources and displayed validated markers, preserving membership after pins', () => {
  const raw = response();
  raw.sources.push({ id: 's9', title: 'Unsafe', url: 'javascript:alert(1)' }, { id: 'note', title: 'Prose', url: 'https://example.com/note' });
  const result = normalizeResult(raw);
  assert.deepEqual(citedSourceIds(result), ['s1']);
  assert.deepEqual(normalizeResult({ ...result, sources: [...result.sources, { id: 's9', title: 'Pinned later', url: 'https://example.com/pin' }] }).cited_source_ids, ['s1']);
  assert.deepEqual(normalizeResult({ ...raw, cited_source_ids: ['s2'] }).cited_source_ids, []);
  assert.deepEqual(normalizeResult({ ...raw, explanation: 'No citations.' }).cited_source_ids, []);
});

test('shared citations count distinct citing IDs, never duplicate edges, overlap, self edges or invalid endpoints', () => {
  const raw = response();
  raw.sources.push({ id: 's3', title: 'Third', url: 'https://example.com/third' });
  raw.graph_nodes.push({ ...raw.sources.at(-1), kind: 'article' });
  const edge = raw.graph_edges[0];
  raw.graph_edges.push(edge, { ...edge, source_id: 's3' }, { ...edge, source_id: 's4' }, { ...edge, source_id: 's2' }, { ...edge, source_id: 's3', relationship_type: 'copied_from' });
  const result = normalizeResult(raw);
  const groups = sharedReferences(result);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].target_id, 's2');
  assert.deepEqual(groups[0].citing.map(c => c.source_id), ['s1', 's3']);
  assert.equal(sharedReferences(normalizeResult(response())).length, 0);
  assert.deepEqual(connectionSteps(result).map(s => s.index).sort((a,b) => a-b), result.graph_edges.map((_,i) => i));
});

test('structured passages survive normalization and repeated pinned-result normalization', () => {
  const result = normalizeResult(response());
  assert.equal(result.graph_edges[1].evidence_detail.target_excerpt, fixture.graph_edges[1].evidence.target_excerpt);
  assert.deepEqual(normalizeResult(result).graph_edges, result.graph_edges);
});

test('report preserves uncertainty, video model labels, captured position, provenance, IDs and synthetic warning', async () => {
  const result = normalizeResult({ ...response(), investigation_id: 'synthetic-test', analyzed_at: '2026-11-15T05:00:00Z', provenance: { retrieved_at: '2026-11-14T04:00:00Z', cache_hit: true }, claim_source: { type: 'video', video_url: 'https://www.youtube.com/watch?v=123', timestamp_url: 'https://www.youtube.com/watch?v=123&t=20', timestamp: '00:20', quote: 'A detected quote', speaker: 'Unknown speaker' } });
  const capture = { text: 'Original context', url: 'https://www.youtube.com/watch?v=123', video_time_seconds: 12, input_origin: 'page_text_fallback' };
  const report = evidenceReport(result, capture);
  for (const pattern of [/SYNTHETIC/, /Insufficient evidence/, /\[s1\]/, /Cited in this finding/, /Retrieved, not cited/, /12 seconds/, /00:20/, /Model-detected quotation/, /not independently verified/, /page_text_fallback/, /cache hit: true/, /Publication date/, /retrieved at/]) assert.match(report, pattern);
  let copied;
  await copyEvidenceReport(result, capture, {}, { writeText: async text => { copied = text; } });
  assert.equal(copied, report);
  await assert.rejects(copyEvidenceReport(result, capture, {}, { writeText: async () => { throw Error('denied'); } }));
  const empty = evidenceReport(normalizeResult({ ...response(), sources: [], graph_nodes: [], graph_edges: [] }));
  assert.match(empty, /No usable sources/); assert.match(empty, /time: unknown/);
});
