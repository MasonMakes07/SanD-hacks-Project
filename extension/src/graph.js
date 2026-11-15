import { RELATIONSHIPS } from './result.js';

let graph;
let observer;

export function destroyGraph() {
  observer?.disconnect();
  graph?.destroy();
  graph = null;
}

export function renderGraph(result, { container, detail, onSource }) {
  destroyGraph();
  container.replaceChildren();
  detail.replaceChildren();
  const nodes = result.graph_nodes;
  if (!nodes.length) {
    container.textContent = 'No source network is available for this investigation.';
    return;
  }
  if (typeof globalThis.cytoscape !== 'function') {
    container.textContent = 'The network could not load. You can inspect every source and connection below.';
    return;
  }
  const edgeById = new Map(result.graph_edges.map((edge, index) => [`edge:${index}`, edge]));
  const nodeById = new Map(nodes.map((node) => [`node:${node.id}`, node]));
  const elements = nodes.map((node, index) => {
    const angle = (2 * Math.PI * index / nodes.length) - Math.PI / 2;
    const host = new URL(node.url).hostname.replace(/^www\./, '');
    return {
      data: { id: `node:${node.id}`, label: `${node.id}\n${host.length > 22 ? `${host.slice(0, 20)}…` : host}` },
      position: { x: 160 + (nodes.length === 1 ? 0 : 105 * Math.cos(angle)), y: 130 + (nodes.length === 1 ? 0 : 85 * Math.sin(angle)) },
      classes: [node.id === result.earliest_observed_source?.id ? 'earliest' : '', node.kind === 'social_post' ? 'social' : ''].join(' ').trim(),
    };
  }).concat([...edgeById].map(([id, edge]) => ({
    data: { id, source: `node:${edge.source_id}`, target: `node:${edge.target_id}` }, classes: edge.relationship_type,
  })));
  graph = globalThis.cytoscape({
    container, elements, layout: { name: 'preset', padding: 38 },
    minZoom: 0.5, maxZoom: 2, userZoomingEnabled: false, userPanningEnabled: false,
    autoungrabify: true, boxSelectionEnabled: false,
    style: [
      { selector: 'node', style: { width: 38, height: 38, 'background-color': '#127464', 'border-width': 4, 'border-color': '#e0f0eb', label: 'data(label)', color: '#23473d', 'font-size': 10, 'font-family': 'system-ui', 'text-wrap': 'wrap', 'text-valign': 'bottom', 'text-margin-y': 7, 'text-background-color': '#f5f8f5', 'text-background-opacity': 0.9, 'text-background-padding': 2 } },
      { selector: 'node.social', style: { shape: 'diamond', 'background-color': '#6c6a9b' } },
      { selector: 'node.earliest', style: { 'border-color': '#d6aa48', 'border-width': 5 } },
      { selector: 'edge', style: { width: 2, 'line-color': '#6f9a8c', 'target-arrow-color': '#6f9a8c', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'control-point-step-size': 38, 'arrow-scale': 0.85 } },
      { selector: 'edge.similar_wording', style: { 'line-style': 'dashed', 'line-color': '#b28428', 'target-arrow-shape': 'none' } },
      { selector: 'edge.same_fact_check', style: { 'line-style': 'dotted', 'target-arrow-shape': 'none', 'line-color': '#6c6a9b' } },
      { selector: ':selected', style: { 'overlay-opacity': 0.15, 'overlay-color': '#127464' } },
    ],
  });
  graph.on('tap', 'node', (event) => onSource(nodeById.get(event.target.id()).id));
  graph.on('tap', 'edge', (event) => {
    const edge = edgeById.get(event.target.id());
    const heading = document.createElement('strong');
    heading.textContent = `${edge.source_id} ${edge.relationship_type === 'explicit_citation' ? '→' : '—'} ${edge.target_id} · ${RELATIONSHIPS[edge.relationship_type]}`;
    const body = document.createElement('p');
    body.textContent = edge.evidence;
    detail.replaceChildren(heading, body);
  });
  observer = new ResizeObserver(() => { graph?.resize(); graph?.fit(undefined, 38); });
  observer.observe(container);
}
