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
