// Opening the panel must happen within the toolbar click's user gesture.
chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ windowId: tab.windowId }).catch(() => {
    // Chrome reports action failures in the extension service-worker console.
    console.warn('TruthTrace could not open the side panel. Click its toolbar icon again.');
  });
});

// Keep the shared local API credential inaccessible to content scripts.
chrome.storage.local.setAccessLevel({ accessLevel: 'TRUSTED_CONTEXTS' }).catch(() => {});

export async function captureActiveTab(windowId, kind) {
  if (!Number.isInteger(windowId) || !['selection', 'article', 'video'].includes(kind)) {
    throw new Error('Invalid capture request.');
  }
  const [tab] = await chrome.tabs.query({ active: true, windowId });
  if (!tab?.id || !/^https?:\/\//i.test(tab.url || '')) {
    throw new Error('Open a public article and click the TruthTrace toolbar icon on that tab. Chrome settings, PDFs, and the Web Store cannot be read.');
  }
  let frames;
  try {
    frames = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['src/anchor.js', 'src/content.js'],
    });
  } catch {
    throw new Error('Click the TruthTrace toolbar icon on this article to allow access, then try again. This page may be protected by Chrome.');
  }
  const snapshot = frames?.[0]?.result;
  if (!snapshot || snapshot.error) {
    throw new Error(snapshot?.error || 'The article could not be read. Reload the page and try again.');
  }
  if (kind === 'video') {
    if (!snapshot.video) throw new Error(snapshot.videoError || 'The video moment is unavailable. Capture it again.');
    return {
      text: snapshot.title || 'Captured YouTube moment', title: snapshot.title,
      ...snapshot.video, kind, truncated: false, tabId: tab.id,
      documentId: frames[0].documentId, documentUrl: snapshot.url,
    };
  }
  const text = kind === 'selection' ? snapshot.selection : snapshot.article;
  if (!text?.trim()) {
    throw new Error(kind === 'selection'
      ? 'Select a factual sentence in the article, then choose Check selection.'
      : 'No visible article or post text was found. Try selecting a sentence instead.');
  }
  // On social sites content.js also returns the checked post's same-site permalink.
  const postUrl = kind === 'selection' ? snapshot.selectionUrl : snapshot.articleUrl;
  return {
    text: text.slice(0, 20000), url: postUrl || snapshot.url, title: snapshot.title,
    truncated: text.length > 20000, kind, tabId: tab.id,
    selectionAnchor: kind === 'selection' && text.length <= 20000 ? snapshot.selectionAnchor : null,
    documentId: frames[0].documentId, documentUrl: snapshot.url,
  };
}

export async function locateCapture(capture, action) {
  if (!Number.isInteger(capture?.tabId) || typeof capture.documentId !== 'string' || !capture.selectionAnchor) {
    throw new Error('This capture has no original selection locator. Select a short passage on an HTML article and capture it again.');
  }
  let tab;
  try { tab = await chrome.tabs.get(capture.tabId); }
  catch { throw new Error('The originating tab was closed. Open the original page and capture the selection again.'); }
  if (action === 'show' && tab.url !== capture.documentUrl) {
    throw new Error('The originating page changed or temporary access was lost. Return to that page, click the TruthTrace toolbar icon, and capture the selection again.');
  }
  let frames;
  try {
    frames = await chrome.scripting.executeScript({
      target: { tabId: capture.tabId, documentIds: [capture.documentId] },
      func: (locator, action) => {
        const anchor = globalThis.__truthtraceSelectionAnchor;
        if (!anchor) return { ok: false, message: 'The original document changed. Capture the selection again.' };
        return action === 'show' ? anchor.show(locator) : anchor.clear(locator.documentToken);
      },
      args: [capture.selectionAnchor, action],
    });
  } catch {
    throw new Error('The original document or temporary page access is unavailable. Return to the page, click the TruthTrace toolbar icon, and capture again.');
  }
  const result = frames?.[0]?.result;
  if (!result?.ok) throw new Error(result?.message || 'The original selection is unavailable. Capture it again.');
  if (action === 'show') {
    await chrome.tabs.update(capture.tabId, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
  }
  return result;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('sidepanel.html')) return;
  let operation;
  if (message?.type === 'CAPTURE') operation = captureActiveTab(message.windowId, message.kind).then((capture) => ({ ok: true, capture }));
  else if (message?.type === 'SHOW_CAPTURE' || message?.type === 'CLEAR_CAPTURE') {
    operation = locateCapture(message.capture, message.type === 'SHOW_CAPTURE' ? 'show' : 'clear');
  } else return;
  operation.then(sendResponse, (error) => sendResponse({ ok: false, error: error.message }));
  return true;
});
