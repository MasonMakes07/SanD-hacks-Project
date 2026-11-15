import { connectionSteps } from './result.js';
import { edgeHeadline } from './detail.js';
import { decorateIcons } from './artwork.js';

// Shared connection tour. Source-by-publication-date browsing is separate.
export function createWalkthrough(container, result, select) {
  const steps = connectionSteps(result);
  let cursor = -1;
  function button(label, handler) {
    const node = document.createElement('button');
    node.type = 'button'; node.className = 'secondary'; node.textContent = label;
    node.addEventListener('click', handler); return node;
  }
  const title = document.createElement('h3'); title.textContent = 'Follow the evidence';
  const caption = document.createElement('p'); caption.className = 'walk-caption'; caption.setAttribute('aria-live', 'polite');
  const controls = document.createElement('div'); controls.className = 'walk-controls';
  const begin = button('Follow the evidence', () => go(0));
  const previous = button('Previous connection', () => go(cursor - 1));
  const next = button('Next connection', () => go(cursor + 1));
  const exit = button('Exit walkthrough', () => { cursor = -1; select(null); update(); begin.focus(); });
  previous.dataset.icon = 'previous'; next.dataset.icon = 'next'; exit.dataset.icon = 'close';
  controls.append(begin, previous, next, exit);
  container.replaceChildren(title, caption, controls);
  function update() {
    const active = cursor >= 0;
    begin.hidden = active; begin.disabled = !steps.length;
    previous.hidden = next.hidden = exit.hidden = !active;
    previous.disabled = cursor <= 0; next.disabled = cursor >= steps.length - 1;
    caption.textContent = !steps.length ? 'No observed connections to walk through. Sources remain available below.'
      : active ? `Connection ${cursor + 1} of ${steps.length}: ${edgeHeadline(steps[cursor].edge)}. Evidence is shown below.`
        : `${steps.length} evidence-backed ${steps.length === 1 ? 'connection' : 'connections'}. This order does not establish chronology, origin, copying, or agreement.`;
  }
  function go(index) {
    if (!steps[index]) return;
    const focusStart = document.activeElement === begin;
    cursor = index; select({ type: 'edge', index: steps[index].index }); update();
    // The start button becomes hidden; keep keyboard focus on a usable control.
    if (focusStart) (steps.length > 1 ? next : exit).focus();
  }
  decorateIcons(container);
  update();
  return { sync(selection) {
    cursor = selection?.type === 'edge' ? steps.findIndex(step => step.index === selection.index) : -1;
    update();
  } };
}
