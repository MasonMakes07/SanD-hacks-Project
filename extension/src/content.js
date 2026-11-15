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
  // Snapshot the visible player's position synchronously with its current URL.
  // A selected-text action remains text even on a YouTube watch page.
  const youtubeId = (value = location.href) => {
    let url;
    try { url = new URL(value); } catch { return null; }
    const host = url.hostname.toLowerCase().replace(/\.$/, '');
    const youtube = ['youtube.com', 'youtube-nocookie.com'].some((domain) => host === domain || host.endsWith(`.${domain}`));
    const id = host === 'youtu.be' ? url.pathname.split('/')[1]
      : youtube ? (url.pathname === '/watch' ? url.searchParams.get('v')
        : url.pathname.match(/^\/(?:shorts|live|embed|v)\/([^/]+)/)?.[1]) : null;
    return /^[A-Za-z0-9_-]{11}$/.test(id || '') ? id : null;
  };
  const videoId = youtubeId();
  const area = (v) => { const r = v.getBoundingClientRect(); return Math.max(0, Math.min(r.right, innerWidth) - Math.max(0, r.left)) * Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(0, r.top)); };
  const players = [...document.querySelectorAll('video')].filter((video) => visible(video) && area(video) > 0).sort((a, b) => area(b) - area(a));
  const player = players[0];
  const time = player?.currentTime;
  const rawDuration = player?.duration;
  const duration = Number.isFinite(rawDuration) && rawDuration > 0 && rawDuration <= 86400 ? rawDuration : null;
  // During YouTube SPA navigation the address can change before the old player
  // is replaced. Refuse mismatched standard DOM metadata, rather than attaching
  // the previous video's playhead to the new URL.
  const metadataIds = [document.querySelector('ytd-watch-flexy[video-id]')?.getAttribute('video-id'),
    player?.closest('[data-video-id]')?.getAttribute('data-video-id'),
    youtubeId(document.querySelector('link[rel="canonical"]')?.href)].filter(Boolean);
  const consistent = metadataIds.every((id) => id === videoId);
  const video = videoId && consistent && player && player.readyState > 0 && !player.seeking && !player.closest('.ad-showing') && Number.isFinite(time) && time >= 0 && time <= 86400 && (duration == null || time <= duration)
    ? { url: `https://www.youtube.com/watch?v=${videoId}`, video_time_seconds: time, video_duration_seconds: duration }
    : null;
  const videoError = !videoId ? 'Open a public YouTube video to check a video moment.'
    : 'The video playback position is unavailable. Wait for the main video to load, then capture the moment again.';
  const selection = window.getSelection();
  let selected = '';
  let selectionAnchor = null;
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
    selectionAnchor = globalThis.__truthtraceSelectionAnchor?.capture(range) || null;
  }

  // Social feeds: every post is its own container, so "longest article" would pick
  // a random post. Use the post being viewed, and its same-site permalink as url.
  const host = location.hostname.replace(/^www\./, '');
  const onHost = (domains) => domains.some((domain) => host === domain || host.endsWith(`.${domain}`));
  const sameSiteUrl = (href) => {
    try {
      const url = new URL(href, location.href);
      return /^https?:$/.test(url.protocol) && url.hostname === location.hostname && !url.username && !url.password ? url.href : '';
    } catch { return ''; }
  };
  const textOf = (root, selector) => [...root.querySelectorAll(selector)].filter(visible).map(visibleText).filter(Boolean).join('\n\n');
  const platforms = [
    { // X / Twitter: the main post's timestamp links to the page's /status/<id>.
      posts: () => document.querySelectorAll('article[data-testid="tweet"]'),
      pageId: () => location.pathname.match(/\/status\/(\d+)/)?.[1],
      permalink: (post) => post.querySelector('a[href*="/status/"] time')?.closest('a')?.getAttribute('href') || '',
      text: (post) => visibleText(post.querySelector('[data-testid="tweetText"]') || post),
    },
    { // Reddit: new (shreddit-post) and old (.thing.link) layouts.
      posts: () => document.querySelectorAll('shreddit-post, .thing.link'),
      pageId: () => location.pathname.match(/\/comments\/([a-z0-9]+)/i)?.[1],
      permalink: (post) => post.getAttribute('permalink') || post.getAttribute('data-permalink') || '',
      text: (post) => [post.getAttribute('post-title') || textOf(post, 'a.title'), textOf(post, '[slot="text-body"], .usertext-body')].filter(Boolean).join('\n\n'),
    },
    { // Other social sites: each post is an article.
      posts: () => (onHost(['threads.net', 'threads.com', 'bsky.app', 'facebook.com', 'instagram.com', 'linkedin.com', 'truthsocial.com', 'mastodon.social'])
        ? document.querySelectorAll('article, [role="article"]') : []),
      pageId: () => undefined,
      permalink: () => '',
      text: (post) => visibleText(post),
    },
  ];
  const visibleHeight = (element) => {
    const rect = element.getBoundingClientRect();
    return Math.min(rect.bottom, innerHeight) - Math.max(rect.top, 0);
  };
  let social = null;
  for (const platform of platforms) {
    const posts = [...platform.posts()].filter(visible);
    if (!posts.length) continue;
    const pageId = platform.pageId();
    const opened = pageId && posts.find((post) => platform.permalink(post).includes(pageId));
    const selectedPost = selected && posts.find((post) => post.contains(selection.anchorNode));
    // The post on its own page wins; otherwise the post taking most of the screen.
    const viewed = opened || posts.sort((a, b) => visibleHeight(b) - visibleHeight(a))[0];
    const permalinkOf = (post) => sameSiteUrl(platform.permalink(post)) || location.href;
    social = { article: (platform.text(viewed) || '').trim(), articleUrl: permalinkOf(viewed), selectionUrl: selectedPost ? permalinkOf(selectedPost) : location.href };
    break;
  }
  if (social) {
    return { selectionAnchor, video, videoError, selection: selected, article: social.article.slice(0, MAX), title: document.title.slice(0, 300), url: location.href, articleUrl: social.articleUrl, selectionUrl: social.selectionUrl };
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
  return { selectionAnchor, video, videoError, selection: selected, article: (best?.text || '').slice(0, MAX), title: document.title.slice(0, 300), url: location.href };
})();
