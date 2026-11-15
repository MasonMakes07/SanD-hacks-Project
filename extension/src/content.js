// The final expression is returned by scripting.executeScript. No persistent
// listeners or page mutations; this runs only after an explicit user action.
(() => {
  const MAX = 20001; // one extra character lets the UI disclose truncation
  const excluded = 'script,style,noscript,nav,header,footer,aside,form,button,input,textarea,select,[contenteditable]:not([contenteditable="false"]),[hidden],[aria-hidden="true"],[inert],[role="navigation"],[role="dialog"]';
  const visible = (element) => {
    if (!element || element.closest(excluded)) return false;
    for (let node = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || style.opacity === '0') return false;
    }
    return element.getClientRects().length > 0;
  };
  const normalize = (text) => text.replace(/\s+/g, ' ').trim();
  const visibleText = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const parts = [];
    let length = 0;
    let node;
    while ((node = walker.nextNode()) && length <= MAX) {
      if (visible(node.parentElement)) { parts.push(node.textContent); length += node.textContent.length; }
    }
    return normalize(parts.join(' '));
  };
  const selection = window.getSelection();
  let selected = '';
  // Inspect original text nodes: a cloned selection can lose its editor or
  // hidden ancestors and accidentally expose text that should be excluded.
  const elementOf = (node) => node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  if (selection?.rangeCount && visible(elementOf(selection.anchorNode)) && visible(elementOf(selection.focusNode))) {
    const range = selection.getRangeAt(0);
    const root = range.commonAncestorContainer;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const parts = [];
    let length = 0;
    let node = root.nodeType === Node.TEXT_NODE ? root : walker.nextNode();
    while (node && length <= MAX) {
      if (range.intersectsNode(node) && visible(node.parentElement)) {
        const start = node === range.startContainer ? range.startOffset : 0;
        const end = node === range.endContainer ? range.endOffset : node.textContent.length;
        const part = node.textContent.slice(start, end);
        parts.push(part); length += part.length;
      }
      node = walker.nextNode();
    }
    selected = normalize(parts.join(' ')).slice(0, MAX);
  }

  const candidates = [...document.querySelectorAll('article, [itemprop="articleBody"], main, [role="main"]')].filter(visible);
  const candidatesWithParagraphs = candidates.map((root) => {
    const paragraphs = [...root.querySelectorAll('h1,h2,h3,p,blockquote,li')]
      .filter((node) => visible(node) && !node.parentElement?.closest('p,blockquote,li'))
      .map((node) => visibleText(node))
      .filter(Boolean);
    return { root, text: paragraphs.join('\n\n') };
  });
  // Prefer semantic article content; use main only when no readable article exists.
  const articles = candidatesWithParagraphs.filter(({ root, text }) => root.matches('article,[itemprop="articleBody"]') && text);
  const best = (articles.length ? articles : candidatesWithParagraphs).sort((a, b) => b.text.length - a.text.length)[0];
  // Do not fall back to body: it may contain account info, menus, or paywall text.
  return { selection: selected, article: (best?.text || '').slice(0, MAX), title: document.title.slice(0, 300), url: location.href };
})();
