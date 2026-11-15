// Capture context and request identity belong to this panel session, not the
// active tab. Every asynchronous continuation checks its operation identity.
export function immutable(value) {
  const copy = structuredClone(value);
  const freeze = (item) => {
    if (item && typeof item === 'object') {
      Object.values(item).forEach(freeze);
      Object.freeze(item);
    }
    return item;
  };
  return freeze(copy);
}

export function capturePayload(capture) {
  return {
    text: capture.text, url: capture.url, mode: 'manual',
    capture_kind: capture.kind,
    ...(capture.video_time_seconds != null ? { video_time_seconds: capture.video_time_seconds } : {}),
    ...(capture.video_duration_seconds != null ? { video_duration_seconds: capture.video_duration_seconds } : {}),
  };
}

export function createLifecycle({ capture, check, onState, onResult, onError }) {
  let sequence = 0;
  let active = null;
  let retrySnapshot = null;
  let lastKind = 'selection';
  const isCurrent = (operation) => active === operation && sequence === operation.id && !operation.controller.signal.aborted;
  const emit = (phase, operation) => onState?.({ phase, requestId: operation.id, startedAt: operation.startedAt });

  async function run(kind, previous) {
    if (active) return;
    lastKind = kind;
    retrySnapshot = previous || null; // A failed fresh capture cannot retry an older capture.
    const operation = { id: ++sequence, controller: new AbortController(), startedAt: Date.now() };
    active = operation;
    let outcome = 'success';
    emit(previous ? 'analysis' : 'capture', operation);
    try {
      let snapshot = previous;
      if (!snapshot) {
        const captured = await capture(kind, operation.controller.signal);
        if (!isCurrent(operation)) return;
        snapshot = immutable({ capture: captured, body: capturePayload(captured) });
        retrySnapshot = snapshot;
      }
      emit('analysis', operation);
      const result = await check(snapshot.body, operation.controller.signal);
      if (!isCurrent(operation)) return;
      onResult(result, snapshot.capture);
    } catch (error) {
      if (!isCurrent(operation)) return;
      outcome = 'error';
      onError(error);
    } finally {
      if (isCurrent(operation)) {
        active = null;
        emit(outcome, operation);
      }
    }
  }
  return {
    start: (kind) => run(kind, null),
    retry: () => run(lastKind, retrySnapshot),
    get retrySnapshot() { return retrySnapshot; },
    cancel() {
      if (!active) return;
      const operation = active;
      active = null;
      sequence++;
      operation.controller.abort();
      emit('cancelled', operation);
    },
  };
}
