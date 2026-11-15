export class ApiError extends Error {
  constructor(message, code = 'request_failed') { super(message); this.code = code; }
}

const messages = {
  unauthorized: 'The backend rejected the API key. In Connection settings, enter the same TRUTHTRACE_API_KEY as backend/.env.',
  server_misconfigured: 'The backend has no TRUTHTRACE_API_KEY. Set it in backend/.env and restart the backend.',
  rate_limited: 'Too many checks. Wait a minute, then try again.',
  timeout: 'The investigation took too long. Try a shorter selection or retry.',
  payload_too_large: 'This text is too long. Select a shorter passage.',
  chat_unavailable: 'The detective took too long or could not be reached. Ask again in a moment.',
  search_failed: 'The evidence search failed. Nothing was added. Try again in a moment.',
  search_unavailable: 'Evidence search is turned off while the backend is in mock mode.',
  storage_unavailable: 'Saved investigations are unavailable. New checks still work.',
  analysis_not_found: 'This saved investigation was not found. Refresh history or run a new check.',
  invalid_saved_result: 'This saved record is unreadable. Run a new check.',
  claim_changed: 'Reassessment must use the unchanged original claim.',
  invalid_evidence_set: 'Pinned evidence is stale, duplicated, or unavailable. Search and pin again; the original finding is unchanged.',
  reassessment_unavailable: 'Reassessment was unavailable. The original finding is unchanged.',
  invalid_request: 'The backend rejected this request. Select a factual sentence and try again.',
};

export async function apiRequest(config, path, { body, signal, timeoutMs = config.requestTimeoutMs } = {}) {
  const authenticated = path !== '/api/health';
  if (authenticated && !config.apiKey) throw new ApiError('Add your local backend API key in Connection settings before checking a claim.', 'missing_key');
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  if (signal?.aborted) abort();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const response = await fetch(`${config.backendUrl}${path}`, {
      method: body ? 'POST' : 'GET',
      headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(authenticated ? { 'X-TruthTrace-Key': config.apiKey } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: controller.signal,
      credentials: 'omit',
      redirect: 'error',
      cache: 'no-store',
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const code = data?.error || `http_${response.status}`;
      throw new ApiError(messages[code] || (code === 'input_rejected' && typeof data?.message === 'string' ? data.message : `The backend returned an error (${response.status}). Try again.`), code);
    }
    if (!data || typeof data !== 'object') throw new ApiError('The backend returned an unreadable response.', 'invalid_response');
    return data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (timedOut) throw new ApiError(messages.timeout, 'timeout');
    if (signal?.aborted) throw new ApiError('Check cancelled.', 'cancelled');
    throw new ApiError(`Cannot reach the backend at ${config.backendUrl}. Start the backend, then retry.`, 'offline');
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}
