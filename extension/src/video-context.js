// All video words/times are model-derived input context, never verified evidence.
export function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0 || seconds > 86400) return null;
  const value = Math.floor(seconds), hours = Math.floor(value / 3600);
  const tail = `${Math.floor(value / 60) % 60}:${String(value % 60).padStart(2, '0')}`;
  return hours ? `${hours}:${tail.padStart(5, '0')}` : tail;
}

export function videoId(value) {
  try {
    const url = new URL(value), host = url.hostname.toLowerCase();
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
    const youtube = ['youtube.com', 'youtube-nocookie.com'].some((domain) => host === domain || host.endsWith(`.${domain}`));
    const id = host === 'youtu.be' ? url.pathname.split('/')[1] : youtube
      ? url.pathname === '/watch' ? url.searchParams.get('v') : url.pathname.match(/^\/(?:shorts|live|embed|v)\/([^/]+)/)?.[1] : null;
    return /^[A-Za-z0-9_-]{11}$/.test(id || '') ? id : null;
  } catch { return null; }
}

export function replayUrl(source, capture) {
  const seconds = source?.timestamp_seconds;
  const id = videoId(capture.url);
  if (!id || typeof seconds !== 'number' || !Number.isInteger(seconds) || formatTime(seconds) == null
    || (capture.video_duration_seconds != null && seconds > capture.video_duration_seconds)
    || videoId(source.video_url) !== id) return null;
  return `https://www.youtube.com/watch?v=${id}&t=${seconds}s`;
}

export function renderVideoContext(container, result, capture) {
  container.replaceChildren();
  container.hidden = capture.kind !== 'video';
  if (container.hidden) return;
  const paragraph = (text, className = 'muted') => {
    const p = document.createElement('p'); p.className = className; p.textContent = text; container.append(p);
  };
  const context = result.input_context;
  paragraph(`Captured playback moment: ${formatTime(capture.video_time_seconds) ?? 'unknown'}.`);
  if (formatTime(context?.reviewed_start_seconds) != null && formatTime(context?.reviewed_end_seconds) != null) {
    paragraph(`Reviewed range: ${formatTime(context.reviewed_start_seconds)}–${formatTime(context.reviewed_end_seconds)}.`);
  }
  if (context?.fallback_reason) paragraph(context.fallback_reason);
  if (context?.extraction_method === 'video_unavailable' || context?.extraction_method === 'video_no_claim') {
    paragraph('No video statement was checked. Try another moment, or explicitly check selected text.');
    return;
  }
  if (context?.claim_edited) paragraph('The approved claim was edited. Detected wording below is the original model input, not a quotation of the edited claim.');
  const source = result.claim_source;
  if (!source) { paragraph('Video statement context is unavailable.'); return; }
  paragraph('Model-detected video statement · wording, speaker and time have not been verified against a transcript.');
  if (typeof source.quote === 'string' && source.quote) paragraph(`Detected wording: “${source.quote.slice(0, 300)}”`);
  if (typeof source.speaker === 'string' && source.speaker) paragraph(`Detected speaker: ${source.speaker.slice(0, 80)}`);
  const url = replayUrl(source, capture);
  if (!url) paragraph('Statement timestamp unavailable.');
  else {
    const a = document.createElement('a'); a.href = url; a.textContent = `Replay at ${formatTime(source.timestamp_seconds)}`;
    a.target = '_blank'; a.rel = 'noopener noreferrer'; container.append(a);
  }
}
