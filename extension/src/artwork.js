// Original TruthTrace SVG vocabulary, MIT. 24-unit grid, 1.7-unit rounded
// strokes; decorative marks accompany existing textual action names.
const paths = {
  document: ['M6 3h8l4 4v14H6z', 'M14 3v5h4M9 12h6M9 16h6'],
  check: ['M5 12l4 4L19 6'], close: ['M6 6l12 12M18 6 6 18'],
  retry: ['M4 10a8 8 0 1 1 1 8', 'M4 4v6h6'],
  copy: ['M9 8h11v13H9z', 'M15 8V3H4v13h5'],
  network: ['M5 6h4v4H5zM15 4h4v4h-4zM14 16h4v4h-4z', 'M9 8l6-2M8 10l7 6M17 8l-1 8'],
  previous: ['M14 5l-7 7 7 7'], next: ['M10 5l7 7-7 7'],
};
export function decorateIcons(root) {
  for (const node of root.querySelectorAll('[data-icon]')) {
    if (node.querySelector('.action-icon') || !paths[node.dataset.icon]) continue;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
    svg.classList.add('action-icon');
    for (const d of paths[node.dataset.icon]) {
      const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', d); svg.append(path);
    }
    node.prepend(svg);
  }
}
