import test from 'node:test';
import assert from 'node:assert/strict';
import { createLifecycle } from '../src/lifecycle.js';

const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const original = { text: 'Exact  selection\nwith “quotes”.', url: 'https://example.com/original', title: 'Original', kind: 'selection', truncated: false, video_time_seconds: 123.5, video_duration_seconds: 800 };

test('retry freezes exact text, URL, title and playback after tab/selection changes', async () => {
  const captured = structuredClone(original), requests = [], results = [];
  let captures = 0, failed = true;
  const flow = createLifecycle({
    capture: async () => { captures++; return captured; },
    check: async (body) => { requests.push(body); if (failed) throw new Error('offline'); return 'ok'; },
    onResult: (result, capture) => results.push({ result, capture }), onError() {},
  });
  await flow.start('selection');
  Object.assign(captured, { text: 'A different claim', url: 'https://elsewhere.test/', video_time_seconds: 400 });
  failed = false; await flow.retry();
  assert.equal(captures, 1);
  assert.deepEqual(requests[1], requests[0]);
  assert.equal(requests[1].text, original.text);
  assert.deepEqual(results[0].capture, original);
  assert(Object.isFrozen(flow.retrySnapshot.body));
});

test('failed new capture never retries an older snapshot', async () => {
  let captures = 0, checks = 0;
  const flow = createLifecycle({
    capture: async () => { if (++captures > 1) throw new Error('selection lost'); return original; },
    check: async () => { checks++; }, onResult() {}, onError() {},
  });
  await flow.start('selection');
  await flow.start('article');
  assert.equal(flow.retrySnapshot, null);
  await flow.retry();
  assert.equal(captures, 3);
  assert.equal(checks, 1);
});

test('cancel permits new work immediately and ignores old late result/error/finally', async () => {
  for (const lateFailure of [false, true]) {
    const old = deferred(), results = [], phases = [], errors = [];
    let calls = 0;
    const flow = createLifecycle({
      capture: async () => original,
      check: () => ++calls === 1 ? old.promise : Promise.resolve('new result'),
      onResult: (result) => results.push(result), onError: (error) => errors.push(error),
      onState: ({ phase }) => phases.push(phase),
    });
    const first = flow.start('selection');
    await Promise.resolve(); await Promise.resolve();
    flow.cancel(); await flow.start('article');
    const completedPhases = [...phases];
    lateFailure ? old.reject(new Error('late failure')) : old.resolve('late result');
    await first;
    assert.deepEqual(results, ['new result']);
    assert.deepEqual(errors, []);
    assert.deepEqual(phases, completedPhases);
  }
});
