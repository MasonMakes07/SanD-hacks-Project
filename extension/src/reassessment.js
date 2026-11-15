import { apiRequest } from './api.js';
import { normalizeResult, STATUSES } from './result.js';
import { renderEvidenceInspector, renderProvenance } from './evidence.js';

export function renderComparison(container, original, revised) {
  container.replaceChildren();
  const doc = container.ownerDocument;
  const heading = doc.createElement('h3');
  heading.textContent = original.status === revised.status ? 'Finding unchanged after reassessment' : 'Finding changed after reassessment';
  container.append(heading);
  for (const [label, result] of [['Original finding', original], ['Reassessed finding', revised]]) {
    const section = doc.createElement('section');
    const title = doc.createElement('h4'); title.textContent = `${label}: ${STATUSES[result.status]?.label || 'Insufficient evidence'} · ${result.analysis_id || 'ID unknown'}`;
    const evidence = doc.createElement('div'), metadata = doc.createElement('div');
    section.append(title, metadata, evidence); container.append(section);
    renderProvenance(metadata, result); renderEvidenceInspector(evidence, result);
  }
  const note = doc.createElement('p'); note.textContent = 'Reassessment used model analysis with the selected evidence. No new source retrieval or claim extraction was performed. Additional sources do not necessarily strengthen or change a finding.';
  container.append(note);
}

export function createReassessment({ container, getConfig, onRevised = () => {} }) {
  container.classList.add('tt-reassessment');
  const doc = container.ownerDocument;
  const make = (tag, text) => { const el = doc.createElement(tag); el.textContent = text; return el; };
  const selected = make('p', '');
  const button = make('button', 'Reassess with pinned evidence'); button.type = 'button';
  const cancelButton = make('button', 'Cancel reassessment'); cancelButton.type = 'button'; cancelButton.hidden = true;
  const status = make('p', ''); status.setAttribute('role', 'status');
  const comparison = make('div', '');
  container.replaceChildren(selected, button, cancelButton, status, comparison);
  let original = null, pinned = [], controller, generation = 0;
  function update() {
    button.disabled = !original?.analysis_id || !pinned.length || !!controller;
    selected.textContent = original ? `Original evidence: ${original.sources.map((s) => s.id).join(', ') || 'none'}. Accepted pinned evidence selected: ${pinned.flatMap((p) => p.source_ids).join(', ') || 'none'}.` : 'Run or reopen an investigation to reassess it.';
  }
  function cancel() {
    const pending = Boolean(controller);
    generation++; controller?.abort(); controller = null; cancelButton.hidden = true;
    if (pending) status.textContent = 'Reassessment cancelled. The original finding is preserved.'; update();
  }
  function setInvestigation(result) {
    const root = result.reassessment?.original_result || result;
    if (root.analysis_id !== original?.analysis_id) {
      generation++; controller?.abort(); controller = null; cancelButton.hidden = true;
      original = structuredClone(normalizeResult(root)); pinned = []; comparison.replaceChildren(); status.textContent = '';
      renderEvidenceInspector(comparison, original);
    }
    if (result.reassessment) {
      pinned = structuredClone(result.reassessment.accepted_evidence || []);
      renderComparison(comparison, original, result);
    }
    update();
  }
  function acceptPinned(selections = []) {
    for (const selection of selections) {
      if (!/^[a-f0-9]{32}$/.test(selection.evidence_set_id) || !Array.isArray(selection.source_ids)) continue;
      const existing = pinned.find((p) => p.evidence_set_id === selection.evidence_set_id);
      if (existing) existing.source_ids = [...new Set([...existing.source_ids, ...selection.source_ids])];
      else pinned.push(structuredClone(selection));
    }
    update();
  }
  button.addEventListener('click', async () => {
    if (button.disabled) return;
    const mine = ++generation;
    controller = new AbortController(); cancelButton.hidden = false; status.textContent = 'Reassessing the unchanged claim with selected evidence…'; update();
    try {
      const payload = await apiRequest(await getConfig(), '/api/reassess', { signal: controller.signal,
        body: { analysis_id: original.analysis_id, claim: original.claim, pinned_evidence: structuredClone(pinned) } });
      if (mine !== generation) return;
      const revised = normalizeResult(payload.revised);
      if (revised.claim !== original.claim || revised.reassessment?.original_analysis_id !== original.analysis_id) throw new Error('The reassessment does not match the original investigation.');
      renderComparison(comparison, original, revised);
      status.textContent = original.status === revised.status ? 'Finding unchanged. Both results are preserved.' : 'Finding changed. Both results are preserved.';
      await onRevised(revised, structuredClone(original));
    } catch (error) {
      if (mine === generation && error.code !== 'cancelled') status.textContent = error.message || 'Reassessment failed. The original finding is preserved.';
    } finally {
      if (mine === generation) { controller = null; cancelButton.hidden = true; update(); }
    }
  });
  cancelButton.addEventListener('click', cancel);
  update();
  return { setInvestigation, acceptPinned, cancel };
}
