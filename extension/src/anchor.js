// Runs in Chrome's isolated main-frame world. It never wraps or rewrites article
// nodes. The locator is local to this document and bounded to a short selection.
(() => {
  if (globalThis.__truthtraceSelectionAnchor) return;
  const excluded = 'script,style,noscript,nav,header,footer,aside,form,button,input,textarea,select,[contenteditable]:not([contenteditable="false"]),[hidden],[aria-hidden="true"],[inert],[role="navigation"],[role="dialog"],[data-truthtrace-anchor]';
  const documentToken = crypto.randomUUID();
  const highlightName = `truthtrace-original-${documentToken}`;
  let observer, notice, style;
  const eligible = (node) => {
    const element = node?.parentElement;
    if (!element || element.closest(excluded)) return false;
    for (let current = element; current; current = current.parentElement) {
      const css = getComputedStyle(current);
      if (['none'].includes(css.display) || ['hidden', 'collapse'].includes(css.visibility) || css.opacity === '0') return false;
    }
    return element.getClientRects().length > 0;
  };
  function indexText() {
    if (document.contentType !== 'text/html' || !document.body) return null;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const entries = [];
    let text = '', count = 0, node;
    while ((node = walker.nextNode())) {
      if (++count > 50000) return null;
      if (!eligible(node)) continue;
      entries.push({ node, start: text.length, end: text.length + node.length });
      text += node.data;
      if (text.length > 1000000) return null;
    }
    return { text, entries };
  }
  function capture(range) {
    const exact = range?.toString();
    if (!exact?.trim() || exact.length > 4000) return null;
    const index = indexText();
    if (!index) return null;
    const parts = [];
    let start, end;
    for (const entry of index.entries) {
      if (!range.intersectsNode(entry.node)) continue;
      const from = range.startContainer === entry.node ? range.startOffset : 0;
      const to = range.endContainer === entry.node ? range.endOffset : entry.node.length;
      if (from >= to) continue;
      parts.push(entry.node.data.slice(from, to));
      if (start === undefined) start = entry.start + from;
      end = entry.start + to;
    }
    // If an editor/hidden node was selected, the raw range differs. Do not
    // anchor a sanitized approximation as though it were the original selection.
    if (start === undefined || parts.join('') !== exact || index.text.slice(start, end) !== exact) return null;
    return {
      version: 1, documentToken, exact, start, end,
      prefix: index.text.slice(Math.max(0, start - 64), start),
      suffix: index.text.slice(end, end + 64),
    };
  }
  function clear(token) {
    if (token && token !== documentToken) return { ok: false, message: 'The original document has changed.' };
    observer?.disconnect(); observer = null;
    CSS.highlights?.delete(highlightName);
    notice?.remove(); notice = null;
    style?.remove(); style = null;
    window.removeEventListener('pagehide', onPageHide);
    window.removeEventListener('popstate', onPageHide);
    window.removeEventListener('hashchange', onPageHide);
    window.navigation?.removeEventListener('navigate', onPageHide);
    return { ok: true, message: 'Original selection highlight removed.' };
  }
  const onPageHide = () => clear();
  const unavailable = (message) => ({ ok: false, message });
  function show(locator) {
    clear();
    if (!locator || locator.version !== 1 || locator.documentToken !== documentToken) {
      return unavailable('The original document is no longer open. Return to it and capture the selection again.');
    }
    if (typeof locator.exact !== 'string' || !locator.exact.trim() || locator.exact.length > 4000
      || typeof locator.prefix !== 'string' || locator.prefix.length > 64 || typeof locator.suffix !== 'string' || locator.suffix.length > 64) {
      return unavailable('This selection has no usable page locator. Select the original passage and capture it again.');
    }
    if (!CSS.highlights || typeof Highlight !== 'function') return unavailable('This Chrome version cannot highlight this selection. Update Chrome and capture again.');
    const index = indexText();
    if (!index) return unavailable('This document cannot be searched safely. Open a supported HTML article and capture again.');
    const matches = [];
    for (let offset = index.text.indexOf(locator.exact); offset !== -1; offset = index.text.indexOf(locator.exact, offset + 1)) {
      const end = offset + locator.exact.length;
      if (index.text.slice(Math.max(0, offset - locator.prefix.length), offset) === locator.prefix
        && index.text.slice(end, end + locator.suffix.length) === locator.suffix) matches.push({ start: offset, end });
      if (matches.length > 1) break;
    }
    if (matches.length !== 1) return unavailable(matches.length
      ? 'This wording appears in more than one matching context. Select the intended passage and capture it again.'
      : 'The original wording or its surrounding text has changed. Select the intended passage and capture it again.');
    const match = matches[0];
    const first = index.entries.find((entry) => entry.start <= match.start && entry.end > match.start);
    const last = index.entries.find((entry) => entry.start < match.end && entry.end >= match.end);
    if (!first || !last) return unavailable('The original selection is unavailable. Capture it again.');
    const range = document.createRange();
    range.setStart(first.node, match.start - first.start);
    range.setEnd(last.node, match.end - last.start);
    // Never bridge an excluded/private node even if the visible index matches.
    if (range.toString() !== locator.exact) return unavailable('The document changed around this selection. Capture the intended passage again.');
    style = document.createElement('style');
    style.textContent = `::highlight(${highlightName}) { background-color: #ffe49a; color: #172c25; text-decoration: underline wavy #172c25; }`;
    document.head.append(style);
    CSS.highlights.set(highlightName, new Highlight(range));
    notice = document.createElement('div');
    notice.dataset.truthtraceAnchor = '';
    const shadow = notice.attachShadow({ mode: 'open' });
    const sheet = document.createElement('style');
    sheet.textContent = ':host{all:initial;position:fixed;bottom:16px;right:16px;z-index:2147483647;max-width:min(320px,90vw)}section{font:14px/1.5 system-ui;background:#fff8df;color:#172c25;border:2px solid #172c25;border-radius:6px;padding:12px;box-shadow:0 2px 10px #0003}button{font:inherit;cursor:pointer;margin-top:8px;padding:6px}button:focus-visible{outline:3px solid #172c25;outline-offset:2px}';
    const box = document.createElement('section');
    box.setAttribute('role', 'status');
    const label = document.createElement('div');
    label.textContent = 'TruthTrace · Original selection is highlighted and underlined.';
    const remove = document.createElement('button');
    remove.textContent = 'Remove TruthTrace highlight';
    remove.addEventListener('click', () => clear());
    box.append(label, remove); shadow.append(sheet, box); document.body.append(notice);
    first.node.parentElement.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
    // Any subsequent document edit invalidates the visible anchor. This avoids
    // the browser's live Range shifting onto replacement or unrelated text.
    observer = new MutationObserver(() => clear());
    observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true });
    window.addEventListener('pagehide', onPageHide, { once: true });
    window.addEventListener('popstate', onPageHide, { once: true });
    window.addEventListener('hashchange', onPageHide, { once: true });
    window.navigation?.addEventListener('navigate', onPageHide, { once: true });
    return { ok: true, message: 'Original selected wording is highlighted and underlined on its page. Use Remove highlight to clear it.' };
  }
  globalThis.__truthtraceSelectionAnchor = { capture, show, clear };
})();
