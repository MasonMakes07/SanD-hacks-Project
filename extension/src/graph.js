// Compact evidence-board preview for the side panel.
import { createBoard } from './board.js';

let board = null;

export function destroyGraph() {
  board?.destroy();
  board = null;
}

export function renderGraph(result, { container, onSelect }) {
  destroyGraph();
  container.replaceChildren();
  if (!result.graph_nodes.length) {
    container.textContent = 'No source network is available for this investigation.';
    return null;
  }
  board = createBoard(container, result, { compact: true, onSelect });
  return board;
}
