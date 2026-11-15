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
    page_title: capture.title || null,
    ...(capture.video_time_seconds != null ? { video_time_seconds: capture.video_time_seconds } : {}),
    ...(capture.video_duration_seconds != null ? { video_duration_seconds: capture.video_duration_seconds } : {}),
  };
}

export function createLifecycle({ capture, extract, confirm, check, onState, onResult, onError }) {
  let sequence = 0;
  let active = null;
  let retrySnapshot = null;
  let lastKind = 'selection';
  const isCurrent = (operation) => active === operation && sequence === operation.id && !operation.controller.signal.aborted;
  const emit = (phase, operation) => {
    if (operation.phase === phase) return;
    operation.phase = phase;
    onState?.({ phase, kind: operation.kind, requestId: operation.id, startedAt: operation.startedAt, phaseStartedAt: Date.now() });
  };

  async function run(kind, previous) {
    if (active) return;
    lastKind = kind;
    retrySnapshot = previous || null; // A failed fresh capture cannot retry an older capture.
    const operation = { kind, id: ++sequence, controller: new AbortController(), startedAt: Date.now() };
    active = operation;
    let outcome = 'success';
    emit(previous ? (previous.body.approved_claim ? 'analysis' : 'extraction') : 'capture', operation);
    try {
      let snapshot = previous;
      if (!snapshot) {
        const captured = await capture(kind, operation.controller.signal);
        if (!isCurrent(operation)) return;
        snapshot = immutable({ capture: captured, body: capturePayload(captured) });
        retrySnapshot = snapshot;
      }
      if (extract && !snapshot.body.approved_claim) {
        emit('extraction', operation);
        const proposal = await extract(snapshot.body, operation.controller.signal);
        if (!isCurrent(operation)) return;
        if (!proposal?.checkable || !proposal.claim?.trim() || !proposal.confirmation_token) {
          throw new Error(proposal?.input_context?.fallback_reason || 'No checkable claim was found. Select a factual sentence or capture another moment.');
        }
        emit('confirmation', operation);
        const approved = await confirm(proposal, snapshot.capture, operation.controller.signal);
        if (!isCurrent(operation)) return;
        snapshot = immutable({
          capture: { ...snapshot.capture, approval: { proposed: proposal.claim, approved } },
          body: { ...snapshot.body, approved_claim: approved, confirmation_token: proposal.confirmation_token },
        });
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
