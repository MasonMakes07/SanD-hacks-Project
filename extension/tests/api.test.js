import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { apiRequest } from '../src/api.js';

test('API transport sends the agreed payload and shared key; health remains public', async () => {
  const requests = [];
  const server = createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    requests.push({ url: req.url, key: req.headers['x-truthtrace-key'], body });
    res.setHeader('Content-Type', 'application/json'); res.end('{"status":"ok","mock":true}');
  }).listen(0, '127.0.0.1');
  await once(server, 'listening');
  const config = { backendUrl: `http://127.0.0.1:${server.address().port}`, apiKey: 'test-only-key', requestTimeoutMs: 1000 };
  try {
    await apiRequest(config, '/api/health');
    const body = { text: 'A checkable sentence.', url: 'https://nasa.gov/', mode: 'manual' };
    await apiRequest(config, '/api/analyze', { body });
    assert.equal(requests[0].key, undefined);
    assert.equal(requests[1].key, config.apiKey);
    assert.deepEqual(JSON.parse(requests[1].body), body);
    await assert.rejects(apiRequest({ ...config, apiKey: '' }, '/api/analyze', { body }), { code: 'missing_key' });
  } finally { server.closeAllConnections(); await new Promise((r) => server.close(r)); }
});

test('API transport distinguishes timeouts, cancellation, unauthorized, and offline failures', async () => {
  const server = createServer((req, res) => {
    if (req.url === '/slow') return;
    res.writeHead(401, { 'Content-Type': 'application/json' });
    res.end('{"error":"unauthorized","message":"not shared"}');
  }).listen(0, '127.0.0.1');
  await once(server, 'listening');
  const config = { backendUrl: `http://127.0.0.1:${server.address().port}`, apiKey: 'test', requestTimeoutMs: 1000 };
  try {
    await assert.rejects(apiRequest(config, '/api/analyze'), { code: 'unauthorized' });
    await assert.rejects(apiRequest(config, '/slow', { timeoutMs: 20 }), { code: 'timeout' });
    const abort = new AbortController(); abort.abort();
    await assert.rejects(apiRequest(config, '/slow', { signal: abort.signal }), { code: 'cancelled' });
  } finally { server.closeAllConnections(); await new Promise((r) => server.close(r)); }
  await assert.rejects(apiRequest(config, '/api/health'), { code: 'offline' });
});
