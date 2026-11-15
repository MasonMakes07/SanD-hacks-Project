export const DEFAULT_REQUEST_TIMEOUT_MS = 100000;

export function validateConfig(value) {
  let url;
  try { url = new URL(value.backendUrl); } catch { throw new Error('Enter a valid local backend URL.'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('Use http://127.0.0.1:<port> or http://localhost:<port> for the local demo backend.');
  }
  const apiKey = typeof value.apiKey === 'string' ? value.apiKey.trim() : '';
  if (/[\r\n]/.test(apiKey)) throw new Error('The API key must be on one line.');
  const requested = Number(value.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS);
  if (!Number.isFinite(requested) || requested < 1000 || requested > 180000) {
    throw new Error('The request timeout must be between 1 and 180 seconds.');
  }
  // Migrate local files AND existing Chrome profile overrides with old budgets.
  const requestTimeoutMs = Math.max(DEFAULT_REQUEST_TIMEOUT_MS, requested);
  return { backendUrl: url.origin, apiKey, requestTimeoutMs };
}

export async function loadConfig() {
  const defaults = await fetch(chrome.runtime.getURL('config.json')).then((r) => r.json());
  let local = {};
  const response = await fetch(chrome.runtime.getURL('config.local.json')).catch(() => null);
  if (response?.ok) {
    try { local = await response.json(); } catch { throw new Error('config.local.json is not valid JSON.'); }
  }
  const { connection = {} } = await chrome.storage.local.get('connection');
  return validateConfig({ ...defaults, ...local, ...connection });
}

export async function saveConfig(value) {
  const config = validateConfig(value);
  await chrome.storage.local.set({ connection: config });
  return config;
}
