import { apiRequest } from './api.js';
import { normalizeResult, STATUSES } from './result.js';

export function captureFromSaved(result) {
  const context = result.page_context;
  return context ? { ...context, title: context.title || '', text: context.text || '' }
    : { url: null, title: 'Original page context unavailable', text: '', contextUnavailable: true };
}

// Account 1/3 owns insertion and panel lifecycle. The callback is the sole open hook.
export function createHistory({ container, getConfig, onOpen }) {
  container.classList.add('tt-history');
  let generation = 0, controller;
  const make = (tag, text) => { const el = container.ownerDocument.createElement(tag); el.textContent = text; return el; };
  const title = make('h3', 'Recent saved investigations');
  const note = make('p', 'Reopening a saved result does not run a fresh check.');
  const status = make('p', ''); status.setAttribute('role', 'status');
  const list = make('ul', '');
  const refresh = make('button', 'Refresh history'); refresh.type = 'button';
  container.replaceChildren(title, note, refresh, status, list);

  async function load() {
    const token = ++generation;
    controller?.abort(); controller = new AbortController();
    const signal = controller.signal;
    status.textContent = 'Loading saved investigations…';
    list.replaceChildren();
    try {
      const payload = await apiRequest(await getConfig(), '/api/analyses?limit=20', { signal });
      if (token !== generation) return;
      const items = Array.isArray(payload.items) ? payload.items.slice(0, 20) : [];
      status.textContent = items.length ? '' : 'No saved investigations yet.';
      for (const item of items) {
        if (!/^[A-Za-z0-9_-]{1,64}$/.test(item.analysis_id)) continue;
        const row = make('li', '');
        const button = make('button', `${item.claim} · ${STATUSES[item.status]?.label || 'Finding unknown'} · checked ${item.checked_at || 'time unknown'} · ${item.storage_backend === 'neon_postgres' ? 'Neon' : item.storage_backend === 'local_json' ? 'local JSON backup' : 'storage unknown'}`);
        button.type = 'button'; row.append(button); list.append(row);
        button.addEventListener('click', async () => {
          const openToken = ++generation;
          controller?.abort(); controller = new AbortController();
          button.disabled = true; status.textContent = 'Opening saved result…';
          try {
            const raw = await apiRequest(await getConfig(), `/api/analyses/${encodeURIComponent(item.analysis_id)}`, { signal: controller.signal });
            if (openToken !== generation) return;
            const result = normalizeResult(raw);
            await onOpen(result, captureFromSaved(result));
            if (openToken === generation) status.textContent = 'Saved result reopened. This is not a fresh check.';
          } catch (error) {
            if (openToken === generation && error.code !== 'cancelled') status.textContent = error.message;
          } finally { button.disabled = false; }
        });
      }
    } catch (error) {
      if (token === generation && error.code !== 'cancelled') status.textContent = error.message;
    }
  }
  refresh.addEventListener('click', load);
  return { load, cancel() { generation++; controller?.abort(); } };
}
