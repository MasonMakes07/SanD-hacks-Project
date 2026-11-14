export function validateConfig(value) {
  let url;
  try { url = new URL(value.backendUrl); } catch { throw new Error('Enter a valid local backend URL.'); }
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
    throw new Error('Use http://127.0.0.1:<port> or http://localhost:<port> for the local demo backend.');
  }
  const apiKey = typeof value.apiKey === 'string' ? value.apiKey.trim() : '';
  if (/[\r\n]/.test(apiKey)) throw new Error('The API key must be on one line.');
  const requestTimeoutMs = Number(value.requestTimeoutMs ?? 45000);
  if (!Number.isFinite(requestTimeoutMs) || requestTimeoutMs < 1000 || requestTimeoutMs > 120000) {
    throw new Error('The request timeout must be between 1 and 120 seconds.');
  }
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
