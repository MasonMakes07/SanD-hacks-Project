import { STATUSES, safeUrl, citedSourceIds } from './result.js';

function time(value) {
  return typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : null;
}
function printable(value) {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : null;
}
function metadata(lines, object, prefix = '') {
  if (!object || typeof object !== 'object') return;
  for (const key of ['analyzed_at', 'checked_at', 'analysis_time', 'retrieved_at', 'retrieval_time', 'cache_hit', 'from_cache', 'cache_status', 'cache_age_seconds', 'input_origin', 'origin', 'material_type']) {
    const value = printable(object[key]);
    if (value !== null) lines.push(`${prefix}${key.replaceAll('_', ' ')}: ${value}`);
  }
}

// Export only the completed, displayed finding. This function has no API access.
export function evidenceReport(result, capture = {}, { synthetic = false } = {}) {
  const cited = new Set(citedSourceIds(result));
  const lines = ['TruthTrace evidence report', `Investigation: ${result.investigation_id}`];
  if (result.analysis_id) lines.push(`Analysis: ${result.analysis_id}`);
  if (synthetic || result.synthetic || /^(mock|synthetic)[-:]/i.test(result.investigation_id)) lines.push('WARNING: SYNTHETIC DEMO DATA. This is not an investigation of the selected text.');
  lines.push('', `Checked claim: ${result.claim}`, `Finding: ${STATUSES[result.status].label}`, STATUSES[result.status].note, '', 'Explanation (existing finding):', result.explanation);
  const unresolved = [...new Set([...result.explanation.matchAll(/\[([A-Za-z]{1,3}\d{1,4})\]/g)].map(match => match[1]).filter(id => !cited.has(id)))];
  if (unresolved.length) lines.push(`Unvalidated markers in the displayed text: ${unresolved.join(', ')}. These are not citations to usable sources in this finding.`);
  const page = safeUrl(capture.url);
  if (page) lines.push('', `Originating page: ${capture.title || page}`, page);
  else lines.push('', 'Originating page: unavailable in this result.');
  if (capture.text) lines.push(`Original captured text: ${capture.text}`);
  if (capture.original_text && capture.original_text !== capture.text) lines.push(`Original selection: ${capture.original_text}`);
  if (Number.isFinite(capture.video_time_seconds)) lines.push(`Playback position captured on click: ${capture.video_time_seconds} seconds`);
  if (capture.approval?.proposed) lines.push(`Originally proposed claim: ${capture.approval.proposed}`);
  if (capture.approval?.approved) lines.push(`Reader-approved claim: ${capture.approval.approved}`);
  const input = result.input_context;
  if (input) {
    for (const key of ['capture_kind', 'extraction_method', 'claim_confirmed', 'claim_edited', 'fallback_reason', 'captured_time_seconds', 'reviewed_start_seconds', 'reviewed_end_seconds']) {
      const value = printable(input[key]); if (value !== null) lines.push(`Input ${key.replaceAll('_', ' ')}: ${value}`);
    }
    if (input.extraction_method === 'text_fallback') lines.push('Input origin: page text/title fallback; this does not establish that the video was analyzed.');
  }
  if (capture.truncated) lines.push('Captured article text was truncated.');
  metadata(lines, capture, 'Capture ');
  const video = result.claim_source;
  if (video?.type === 'video') {
    lines.push('', 'Video context (model-detected; not independently verified transcription):');
    const videoUrl = safeUrl(video.video_url); const replay = safeUrl(video.timestamp_url);
    if (videoUrl) lines.push(`Video: ${videoUrl}`);
    if (replay) lines.push(`Replay detected statement: ${replay}`);
    if (video.timestamp) lines.push(`Detected statement timestamp: ${video.timestamp}`);
    else if (Number.isFinite(video.timestamp_seconds)) lines.push(`Detected statement timestamp: ${video.timestamp_seconds} seconds`);
    if (video.quote) lines.push(`Model-detected quotation: ${video.quote}`);
    if (video.speaker) lines.push(`Model-detected speaker: ${video.speaker}`);
  }
  lines.push('', 'Available provenance (publication dates are separate from retrieval and analysis times):');
  metadata(lines, result); metadata(lines, result.provenance); metadata(lines, result.retrieval);
  if (!time(result.analyzed_at) && !time(result.checked_at)) lines.push('Analysis time: unknown unless recorded above.');
  lines.push('', `Sources (${result.sources.length}; ${cited.size} cited in this finding):`);
  if (!result.sources.length) lines.push('No usable sources were returned. This does not establish that the claim is false.');
  for (const source of result.sources) {
    const url = safeUrl(source.url); if (!url) continue;
    lines.push('', `[${source.id}] ${source.title}`, url, cited.has(source.id) ? 'Cited in this finding' : 'Retrieved, not cited', `Publication date: ${time(source.date) || 'unknown'}`);
    if (source.excerpt) lines.push(`Retrieved excerpt (not automatically a verified passage): ${source.excerpt}`);
    metadata(lines, source, 'Source ');
    metadata(lines, source.provenance, 'Source ');
    if (source.evidence) {
      // Preserve backend evidence labels without upgrading supplied material.
      for (const key of ['material_type', 'verification', 'quote', 'passage', 'note']) {
        const value = printable(source.evidence[key]); if (value) lines.push(`Evidence ${key}: ${value}`);
      }
    }
  }
  lines.push('', `Observed relationships (${result.graph_edges.length}):`);
  for (const edge of result.graph_edges) {
    lines.push(`${edge.source_id} — ${edge.relationship_type} — ${edge.target_id}`, edge.evidence);
    if (time(edge.evidence_detail?.retrieved_at)) lines.push(`Connection observation retrieved at: ${edge.evidence_detail.retrieved_at}`);
  }
  lines.push('Links do not prove support or agreement. Wording overlap does not prove copying, origin, or independence. No observed connections does not establish independence.', '', 'Copied from the existing investigation. No new retrieval or model analysis was requested.');
  return lines.join('\n');
}

export async function copyEvidenceReport(result, capture, options = {}, clipboard = navigator.clipboard) {
  await clipboard.writeText(evidenceReport(result, capture, options));
}
