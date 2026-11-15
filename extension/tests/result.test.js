import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { evidenceText, formatDate, normalizeResult, safeUrl } from '../src/result.js';
import { validateConfig } from '../src/config.js';

const fixture = JSON.parse(await readFile(new URL('../../source_tracing/fixtures/example_investigation_response.json', import.meta.url)));
const response = () => ({ ...structuredClone(fixture), claim: 'DART changed an orbit.', status: 'insufficient_evidence', explanation: 'See [s1].' });

test('accepts the Person 3 structured evidence fixture without changing its verdict', () => {
  const result = normalizeResult(response());
  assert.equal(result.graph_edges.length, 2);
  assert.match(result.graph_edges[0].evidence, /Observed link: https:\/\/www.nasa.gov/);
  assert.equal(result.earliest_observed_source.id, 's1');
  assert.equal(result.status, 'insufficient_evidence');
});

test('draws only known, evidenced relationships with valid endpoints', () => {
  const raw = response();
  const valid = { source_id: 's1', target_id: 's2', relationship_type: 'explicit_citation', evidence: 'Observed body link.' };
  raw.graph_edges = [valid, { ...valid, evidence: ' ' }, { ...valid, target_id: 'unknown' }, { ...valid, relationship_type: 'copied_from' }, { ...valid, evidence: { note: 'A guess' } }];
  assert.deepEqual(normalizeResult(raw).graph_edges, [valid]);
});

test('rejects unsafe URLs and downgrades a verdict when there are no usable sources', () => {
  const raw = response();
  raw.status = 'contradicted';
  raw.sources = [{ id: 's1', title: 'unsafe', url: 'javascript:alert(1)' }];
  const result = normalizeResult(raw);
  assert.equal(result.status, 'insufficient_evidence');
  assert.equal(result.sources.length, 0);
  assert.equal(result.graph_edges.length, 0);
  assert.equal(result.earliest_observed_source, null);
  assert.match(result.explanation, /does not establish/);
});

test('does not treat an undated source as the earliest publisher', () => {
  const raw = response();
  raw.sources[0].date = null;
  assert.equal(normalizeResult(raw).earliest_observed_source, null);
  assert.equal(formatDate(null), 'Date unavailable');
  assert.equal(formatDate('not-a-date'), 'Date unavailable');
});

test('rejects malformed results rather than displaying an invented status', () => {
  for (const change of [{ status: 'false' }, { status: 'constructor' }, { claim: null }, { graph_nodes: null }, { explanation: 42 }]) {
    assert.throws(() => normalizeResult({ ...response(), ...change }), /API contract/);
  }
});

test('allows HTTP(S) source links without credentials', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,bad', 'https://user:secret@example.com/', '/relative', null]) assert.equal(safeUrl(url), null);
  assert.equal(safeUrl('https://nasa.gov/article'), 'https://nasa.gov/article');
  assert.equal(evidenceText({ note: 'No supporting observation' }), '');
});

test('connection config only sends the API key to declared local backend hosts', () => {
  assert.deepEqual(validateConfig({ backendUrl: 'http://localhost:9000/', apiKey: ' example ' }), { backendUrl: 'http://localhost:9000', apiKey: 'example', requestTimeoutMs: 100000 });
  for (const backendUrl of ['https://attacker.example', 'http://127.0.0.1.evil.test:8000', 'http://localhost:8000/path', 'http://user:pass@localhost:8000', 'http://localhost/?key=bad']) {
    assert.throws(() => validateConfig({ backendUrl }), /local demo backend/);
  }
  assert.throws(() => validateConfig({ backendUrl: 'http://localhost:8000', apiKey: 'a\nb' }), /one line/);
});


test('old file/profile deadlines migrate above the server ceiling', () => {
  for (const requestTimeoutMs of [1000, 45000, 90000, 100000]) {
    assert.equal(validateConfig({ backendUrl: 'http://localhost:8000', requestTimeoutMs }).requestTimeoutMs, 100000);
  }
});
