// The evidence board: paper source cards pinned to cork, joined by yarn.
// Cards are DOM (readable, focusable), layered between the yarn canvas and
// the pin canvas. Both canvases share the same world transform. Every card and every string comes from the normalized backend
// result — nothing decorative is added.
import { formatDate } from './result.js';

const SEG = 26; // samples per string
const K = 90, C = 2600, D = 5.5; // rest spring, neighbour tension, damping
const MAX_OFFSET = 16;

export const RELATION_INFO = {
  explicit_citation: { label: 'Cites', basis: 'Confirmed link' },
  similar_wording: { label: 'Similar wording', basis: 'Inferred similarity' },
  same_fact_check: { label: 'Same fact-check', basis: 'Shared fact-check' },
};

const KIND_LABEL = { article: 'Article', fact_check: 'Fact-check', document: 'Document', social_post: 'Social post' };
export const kindLabel = (kind) => KIND_LABEL[kind] || 'Source';
export const hostOf = (url) => new URL(url).hostname.replace(/^www\./, '');

function hash(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 10000) / 10000;
}

const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// Connected sources sit next to each other: breadth-first from the best
// connected card, then laid out in a serpentine so neighbours stay adjacent.
function orderNodes(nodes, edges) {
  const links = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) { links.get(e.source_id)?.push(e.target_id); links.get(e.target_id)?.push(e.source_id); }
  const byDegree = [...nodes].sort((a, b) => links.get(b.id).length - links.get(a.id).length);
  const seen = new Set();
  const ordered = [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const start of byDegree) {
    if (seen.has(start.id)) continue;
    const queue = [start.id]; seen.add(start.id);
    while (queue.length) {
      const id = queue.shift();
      ordered.push(byId.get(id));
      for (const next of links.get(id)) if (!seen.has(next)) { seen.add(next); queue.push(next); }
    }
  }
  return ordered;
}

function layout(nodes, edges, compact, portrait = false) {
  const ordered = orderNodes(nodes, edges);
  const n = ordered.length;
  if (!compact && n >= 4 && edges.length) {
    // The best-connected source sits in the middle and the rest ring it, so
    // most strings radiate outward instead of running across other cards.
    const ring = n - 1;
    // A tall board gets a tall ring, so cards stay readable in a narrow window.
    const rx = portrait ? Math.max(250, 40 * ring) : Math.max(400, 66 * ring);
    const ry = portrait ? Math.max(420, 64 * ring) : Math.max(280, 52 * ring);
    const positions = new Map([[ordered[0].id, { x: 0, y: -40 }]]);
    ordered.slice(1).forEach((node, index) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * (index + 0.5)) / ring;
      positions.set(node.id, {
        x: rx * Math.cos(angle) + (hash(`${node.id}x`) - 0.5) * 26,
        y: ry * Math.sin(angle) - 60 + (hash(`${node.id}y`) - 0.5) * 26,
      });
    });
    return { ordered, positions };
  }
  const cols = compact ? (n <= 6 ? Math.min(n, 2) : 3) : Math.max(1, Math.min(n, Math.ceil(Math.sqrt(n * 1.7))));
  const cellW = compact ? 215 : 268;
  const cellH = compact ? 260 : 280;
  const positions = new Map();
  ordered.forEach((node, index) => {
    const row = Math.floor(index / cols);
    const inRow = Math.min(cols, n - row * cols);
    let col = index % cols;
    if (row % 2) col = inRow - 1 - col;
    const jx = (hash(`${node.id}x`) - 0.5) * 30;
    const jy = (hash(`${node.id}y`) - 0.5) * 26;
    positions.set(node.id, {
      x: (col - (inRow - 1) / 2) * cellW + jx,
      y: row * cellH + (col % 2 ? cellH * 0.2 : 0) + jy,
    });
  });
  return { ordered, positions };
}

function buildCard(node, index, isEarliest, hasDate) {
  const articleStyles = ['clipping', 'polaroid', 'index'];
  const variant = node.kind === 'social_post' ? 'note'
    : node.kind === 'fact_check' || node.kind === 'document' ? 'document'
      : articleStyles[index % articleStyles.length];
  const host = hostOf(node.url);
  const el = document.createElement('div');
  el.className = `ev-card ev-${variant}`;
  el.setAttribute('role', 'button');
  el.tabIndex = 0;
  el.dataset.id = node.id;
  el.style.setProperty('--rot', `${((hash(`${node.id}r`) - 0.5) * 6.4).toFixed(2)}deg`);
  el.setAttribute('aria-label', `Source ${node.id}: ${node.title}. ${host}. ${hasDate ? formatDate(node.date) : 'Date unavailable'}.`);
  const add = (tag, className, text) => {
    const child = document.createElement(tag);
    child.className = className; child.textContent = text;
    el.append(child);
    return child;
  };
  add('span', 'ev-id', node.id);
  if (variant === 'polaroid') add('div', 'ev-plate', host); // typographic identity, never an invented photo
  add('div', 'ev-kicker', variant === 'polaroid' ? kindLabel(node.kind) : `${kindLabel(node.kind)} · ${host}`);
  add('div', 'ev-title', node.title);
  add('div', 'ev-date', hasDate ? formatDate(node.date) : 'Undated');
  if (isEarliest) add('span', 'ev-stamp', 'Earliest dated');
  return el;
}

export function createBoard(container, result, { compact = false, onSelect = () => {}, inset = {} } = {}) {
  container.replaceChildren();
  container.classList.add('board');
  container.classList.toggle('board-compact', compact);
  const world = document.createElement('div');
  world.className = 'board-world';
  const canvas = document.createElement('canvas');
  canvas.className = 'board-yarn';
  canvas.setAttribute('aria-hidden', 'true');
  const light = document.createElement('div');
  light.className = 'board-light';
  const pins = document.createElement('canvas');
  pins.className = 'board-pins';
  pins.setAttribute('aria-hidden', 'true');
  container.append(canvas, world, pins, light);
  const ctx = canvas.getContext('2d');
  const pinCtx = pins.getContext('2d');

  const nodes = result.graph_nodes;
  const edges = result.graph_edges;
  const isPortrait = () => !compact && container.clientWidth < container.clientHeight * 0.95;
  let portrait = isPortrait();
  let cardsMoved = false;
  const { ordered, positions } = layout(nodes, edges, compact, portrait);
  const cards = new Map();
  ordered.forEach((node, index) => {
    const hasDate = Boolean(node.date) && Number.isFinite(Date.parse(node.date));
    const el = buildCard(node, index, node.id === result.earliest_observed_source?.id, hasDate);
    const { x, y } = positions.get(node.id);
    el.style.left = `${x}px`; el.style.top = `${y}px`;
    world.append(el);
    cards.set(node.id, { node, el, x, y, w: 0, h: 0 });
  });

  const pairCount = new Map();
  const yarns = edges.map((edge, index) => {
    const key = [edge.source_id, edge.target_id].sort().join('|');
    const nth = pairCount.get(key) || 0;
    pairCount.set(key, nth + 1);
    const fuzz = Array.from({ length: 7 }, (_, i) => ({
      at: 2 + Math.floor(hash(`${index}f${i}`) * (SEG - 4)),
      angle: hash(`${index}a${i}`) * Math.PI * 2,
      length: 2.5 + hash(`${index}l${i}`) * 3.5,
    }));
    return {
      edge, index, nth, fuzz, a: cards.get(edge.source_id), b: cards.get(edge.target_id),
      off: new Float32Array((SEG + 1) * 2), vel: new Float32Array((SEG + 1) * 2),
      pts: new Float32Array((SEG + 1) * 2), active: false, lastPluck: 0,
    };
  });

  const view = { x: 0, y: 0, z: 1 };
  let selection = null;
  let hoverYarn = null;
  let userMoved = false;
  let frame = 0;
  let simulating = false;
  let lastTime = 0;
  let viewTween = 0;
  let destroyed = false;
  const yarnWidth = compact ? 4.4 : 3.4;

  function measure() {
    for (const card of cards.values()) { card.w = card.el.offsetWidth; card.h = card.el.offsetHeight; }
  }

  function bounds() {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const card of cards.values()) {
      minX = Math.min(minX, card.x - card.w / 2); maxX = Math.max(maxX, card.x + card.w / 2);
      minY = Math.min(minY, card.y - 16); maxY = Math.max(maxY, card.y + card.h);
    }
    return { minX, minY, maxX, maxY };
  }

  function applyView() {
    world.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.z})`;
    container.style.backgroundPosition = `${view.x}px ${view.y}px`;
    requestDraw();
  }

  function setView(target, animate) {
    cancelAnimationFrame(viewTween);
    if (!animate || reducedMotion()) { Object.assign(view, target); applyView(); return; }
    const from = { ...view };
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / 240);
      const e = 1 - (1 - t) ** 3;
      view.x = from.x + (target.x - from.x) * e;
      view.y = from.y + (target.y - from.y) * e;
      view.z = from.z + (target.z - from.z) * e;
      applyView();
      if (t < 1 && !destroyed) viewTween = requestAnimationFrame(step);
    };
    viewTween = requestAnimationFrame(step);
  }

  function fit(animate = false) {
    if (!cards.size || !container.clientWidth) return;
    measure();
    const b = bounds();
    const pad = compact ? 22 : 44;
    const top = pad + (inset.top || 0); const bottom = pad + (inset.bottom || 0);
    const width = container.clientWidth; const height = container.clientHeight;
    const z = Math.max(0.05, Math.min((width - pad * 2) / (b.maxX - b.minX), (height - top - bottom) / (b.maxY - b.minY), compact ? 1 : 1.2));
    userMoved = false;
    setView({
      z,
      x: width / 2 - ((b.minX + b.maxX) / 2) * z,
      y: top + (height - top - bottom) / 2 - ((b.minY + b.maxY) / 2) * z,
    }, animate);
  }

  function zoomAt(factor, cx = container.clientWidth / 2, cy = container.clientHeight / 2, animate = false) {
    const z = Math.min(2.6, Math.max(0.3, view.z * factor));
    const wx = (cx - view.x) / view.z; const wy = (cy - view.y) / view.z;
    userMoved = true;
    setView({ z, x: cx - wx * z, y: cy - wy * z }, animate);
  }

  // ---- yarn geometry and physics -------------------------------------
  function computePoints(yarn) {
    const { a, b, off, pts } = yarn;
    const dx = b.x - a.x; const dy = b.y - a.y;
    const sag = Math.min(46, Math.hypot(dx, dy) * (0.055 + yarn.nth * 0.07)) + yarn.nth * 6;
    for (let i = 0; i <= SEG; i += 1) {
      const t = i / SEG;
      pts[i * 2] = a.x + dx * t + off[i * 2];
      pts[i * 2 + 1] = a.y + dy * t + sag * 4 * t * (1 - t) + off[i * 2 + 1];
    }
  }

  let simulationRemainder = 0;
  function simulate(dt) {
    let anyActive = false;
    const h = 1 / 120;
    simulationRemainder = Math.min(simulationRemainder + dt, h * 6);
    const steps = Math.floor(simulationRemainder / h);
    simulationRemainder -= steps * h;
    for (const yarn of yarns) {
      if (!yarn.active) continue;
      const { off, vel } = yarn;
      let energy = 0;
      for (let s = 0; s < steps; s += 1) {
        for (let i = 1; i < SEG; i += 1) {
          for (let axis = 0; axis < 2; axis += 1) {
            const j = i * 2 + axis;
            vel[j] += (-K * off[j] + C * (off[j - 2] + off[j + 2] - 2 * off[j]) - D * vel[j]) * h;
          }
        }
        for (let j = 2; j < SEG * 2; j += 1) {
          off[j] = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET, off[j] + vel[j] * h));
        }
      }
      for (let j = 2; j < SEG * 2; j += 1) energy = Math.max(energy, Math.abs(off[j]) * 8, Math.abs(vel[j]));
      if (energy < 0.6) { off.fill(0); vel.fill(0); yarn.active = false; } else anyActive = true;
    }
    return anyActive;
  }

  function wake() {
    if (simulating || destroyed) return;
    simulating = true;
    lastTime = performance.now();
    simulationRemainder = 0;
    const loop = (now) => {
      if (destroyed) return;
      const dt = Math.min(0.05, (now - lastTime) / 1000);
      lastTime = now;
      if (reducedMotion()) {
        for (const yarn of yarns) { yarn.off.fill(0); yarn.vel.fill(0); yarn.active = false; }
      }
      const more = simulate(dt);
      draw();
      if (more) requestAnimationFrame(loop); else simulating = false;
    };
    requestAnimationFrame(loop);
  }

  // A light disturbance centred on the touched sample; the wave then travels
  // along the string through the neighbour coupling and damps out.
  function pluck(yarn, at, vx, vy) {
    if (reducedMotion()) return;
    const now = performance.now();
    if (now - yarn.lastPluck < 90) return;
    yarn.lastPluck = now;
    let speed = Math.hypot(vx, vy);
    if (speed < 40) { // barely moving: a small nudge across the string
      const i = Math.max(1, Math.min(SEG - 1, at));
      const tx = yarn.pts[(i + 1) * 2] - yarn.pts[(i - 1) * 2]; const ty = yarn.pts[(i + 1) * 2 + 1] - yarn.pts[(i - 1) * 2 + 1];
      const tl = Math.hypot(tx, ty) || 1;
      vx = (-ty / tl) * 120; vy = (tx / tl) * 120; speed = 120;
    }
    const scale = Math.min(420, speed * 0.55) / speed;
    for (let i = 1; i < SEG; i += 1) {
      const fall = Math.exp(-(((i - at) / 3.4) ** 2));
      yarn.vel[i * 2] += vx * scale * fall;
      yarn.vel[i * 2 + 1] += vy * scale * fall;
    }
    yarn.active = true;
    wake();
  }

  function nearestYarn(wx, wy) {
    const reach = (7 / view.z) + yarnWidth / 2;
    let best = null;
    for (const yarn of yarns) {
      const { pts } = yarn;
      for (let i = 0; i < SEG; i += 1) {
        const ax = pts[i * 2]; const ay = pts[i * 2 + 1];
        const bx = pts[i * 2 + 2]; const by = pts[i * 2 + 3];
        const dx = bx - ax; const dy = by - ay;
        const t = Math.max(0, Math.min(1, ((wx - ax) * dx + (wy - ay) * dy) / (dx * dx + dy * dy || 1)));
        const dist = Math.hypot(wx - ax - dx * t, wy - ay - dy * t);
        if (dist < reach && (!best || dist < best.dist)) best = { yarn, dist, at: i + (t > 0.5 ? 1 : 0) };
      }
    }
    return best;
  }

  // ---- drawing --------------------------------------------------------
  function trace(pts) {
    ctx.beginPath();
    ctx.moveTo(pts[0], pts[1]);
    for (let i = 1; i < SEG; i += 1) {
      ctx.quadraticCurveTo(pts[i * 2], pts[i * 2 + 1], (pts[i * 2] + pts[i * 2 + 2]) / 2, (pts[i * 2 + 1] + pts[i * 2 + 3]) / 2);
    }
    ctx.lineTo(pts[SEG * 2], pts[SEG * 2 + 1]);
  }

  function normals(pts) {
    const out = new Float32Array(pts.length);
    for (let i = 0; i <= SEG; i += 1) {
      const p = Math.max(0, i - 1); const q = Math.min(SEG, i + 1);
      const tx = pts[q * 2] - pts[p * 2]; const ty = pts[q * 2 + 1] - pts[p * 2 + 1];
      const tl = Math.hypot(tx, ty) || 1;
      let nx = -ty / tl; let ny = tx / tl;
      if (ny > 0) { nx = -nx; ny = -ny; } // normals point up, toward the light
      out[i * 2] = nx; out[i * 2 + 1] = ny;
    }
    return out;
  }

  function shifted(pts, nrm, d) {
    const out = new Float32Array(pts.length);
    for (let i = 0; i < pts.length; i += 1) out[i] = pts[i] + nrm[i] * d;
    return out;
  }

  function strand(pts, nrm, w, tone, { dash = null, twist = true } = {}) {
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.setLineDash(dash || []);
    // soft contact shadow on the cork and on anything the string crosses
    ctx.save();
    ctx.translate(1.6, 3.2);
    trace(pts); ctx.strokeStyle = 'rgba(14, 7, 3, 0.20)'; ctx.lineWidth = w * 2.1; ctx.stroke();
    trace(pts); ctx.strokeStyle = 'rgba(14, 7, 3, 0.34)'; ctx.lineWidth = w * 1.15; ctx.stroke();
    ctx.restore();
    trace(pts); ctx.strokeStyle = tone.edge; ctx.lineWidth = w; ctx.stroke();
    trace(pts); ctx.strokeStyle = tone.body; ctx.lineWidth = w * 0.74; ctx.stroke();
    if (twist) { // the diagonal plies of twisted yarn
      ctx.setLineDash([]);
      ctx.beginPath();
      let carried = 0;
      const stepLen = w * 0.95;
      for (let i = 0; i < SEG; i += 1) {
        const ax = pts[i * 2]; const ay = pts[i * 2 + 1];
        const dx = pts[i * 2 + 2] - ax; const dy = pts[i * 2 + 3] - ay;
        const len = Math.hypot(dx, dy) || 1;
        const tx = dx / len; const ty = dy / len;
        for (let d = stepLen - carried; d < len; d += stepLen) {
          const px = ax + tx * d; const py = ay + ty * d;
          const nx = -ty * w * 0.34; const ny = tx * w * 0.34;
          ctx.moveTo(px + nx - tx * w * 0.3, py + ny - ty * w * 0.3);
          ctx.lineTo(px - nx + tx * w * 0.3, py - ny + ty * w * 0.3);
        }
        carried = (carried + len) % stepLen;
      }
      ctx.strokeStyle = tone.ply; ctx.lineWidth = w * 0.17; ctx.stroke();
    }
    ctx.setLineDash(dash || []);
    trace(shifted(pts, nrm, w * 0.2)); ctx.strokeStyle = tone.sheen; ctx.lineWidth = w * 0.15; ctx.stroke();
    ctx.setLineDash([]);
  }

  const TONES = {
    explicit_citation: { edge: '#5e0a13', body: '#a5141f', ply: 'rgba(58, 4, 10, 0.6)', sheen: 'rgba(255, 168, 150, 0.55)', hair: 'rgba(196, 44, 54, 0.5)' },
    similar_wording: { edge: '#6b1a22', body: '#c0434a', ply: 'rgba(70, 10, 16, 0.5)', sheen: 'rgba(255, 196, 184, 0.6)', hair: 'rgba(214, 90, 96, 0.45)' },
    same_fact_check: { edge: '#3d060d', body: '#7d0f1a', ply: 'rgba(30, 2, 6, 0.6)', sheen: 'rgba(240, 140, 130, 0.45)', hair: 'rgba(150, 30, 40, 0.5)' },
  };
  const BRIGHT = { edge: '#7a0f1a', body: '#d0222e', ply: 'rgba(70, 6, 12, 0.55)', sheen: 'rgba(255, 205, 190, 0.75)', hair: 'rgba(230, 70, 80, 0.6)' };

  function yarnState(yarn) {
    if (!selection) return 'rest';
    if (selection.type === 'edge') return selection.index === yarn.index ? 'lit' : 'dim';
    return yarn.edge.source_id === selection.id || yarn.edge.target_id === selection.id ? 'lit' : 'dim';
  }

  function drawTag(yarn, state, force) {
    const { pts } = yarn;
    let mid = -1;
    for (const t of [0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74]) {
      const i = Math.round(SEG * t);
      const x = pts[i * 2]; const y = pts[i * 2 + 1];
      let clear = true;
      for (const card of cards.values()) {
        if (Math.abs(x - card.x) < card.w / 2 + 34 && y > card.y - 34 && y < card.y + card.h + 20) { clear = false; break; }
      }
      if (clear) { mid = i; break; }
    }
    if (mid < 0) { if (!force) return; mid = Math.floor(SEG / 2); }
    const mx = pts[mid * 2]; const my = pts[mid * 2 + 1];
    let angle = Math.atan2(pts[(mid + 1) * 2 + 1] - pts[(mid - 1) * 2 + 1], pts[(mid + 1) * 2] - pts[(mid - 1) * 2]);
    const flipped = Math.abs(angle) > Math.PI / 2;
    if (flipped) angle += Math.PI;
    const info = RELATION_INFO[yarn.edge.relationship_type];
    const text = info.label.toLowerCase();
    const directed = yarn.edge.relationship_type === 'explicit_citation';
    ctx.save();
    ctx.translate(mx, my); ctx.rotate(angle);
    ctx.font = '600 9.5px "American Typewriter", "Courier New", monospace';
    const width = ctx.measureText(text).width + (directed ? 24 : 14);
    ctx.fillStyle = 'rgba(14, 7, 3, 0.35)';
    ctx.fillRect(-width / 2 + 1.5, 5.5, width, 15);
    ctx.fillStyle = state === 'lit' ? '#fbf3dc' : '#eadfc4';
    ctx.fillRect(-width / 2, 4, width, 15);
    ctx.fillStyle = '#3a2417';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(text, directed ? (flipped ? 5 : -5) : 0, 12);
    if (directed) { // drawn arrowhead pointing from the citing source to the cited one
      const tip = (flipped ? -1 : 1) * (width / 2 - 5);
      const base = tip - (flipped ? -1 : 1) * 6;
      ctx.beginPath(); ctx.moveTo(tip, 11.5); ctx.lineTo(base, 8); ctx.lineTo(base, 15); ctx.closePath(); ctx.fill();
    }
    ctx.restore();
  }

  function drawYarn(yarn) {
    computePoints(yarn);
    const state = yarnState(yarn);
    const hovered = hoverYarn === yarn;
    ctx.globalAlpha = state === 'dim' ? 0.2 : 1;
    const tone = state === 'lit' || hovered ? BRIGHT : TONES[yarn.edge.relationship_type];
    const { pts } = yarn;
    const nrm = normals(pts);
    const w = yarnWidth * (state === 'lit' ? 1.12 : 1);
    const type = yarn.edge.relationship_type;
    if (type === 'same_fact_check') { // two strands running side by side
      strand(shifted(pts, nrm, w * 0.5), nrm, w * 0.62, tone);
      strand(shifted(pts, nrm, -w * 0.5), nrm, w * 0.62, tone);
    } else if (type === 'similar_wording') { // a lighter, stitched thread: inferred, not confirmed
      strand(pts, nrm, w * 0.72, tone, { dash: [w * 3, w * 2.3], twist: false });
    } else strand(pts, nrm, w, tone);
    if (type !== 'similar_wording') {
      ctx.beginPath();
      for (const hair of yarn.fuzz) {
        const x = pts[hair.at * 2]; const y = pts[hair.at * 2 + 1];
        ctx.moveTo(x, y); ctx.lineTo(x + Math.cos(hair.angle) * hair.length, y + Math.sin(hair.angle) * hair.length);
      }
      ctx.strokeStyle = tone.hair; ctx.lineWidth = 0.55; ctx.stroke();
    }
    if (!compact && state !== 'dim' && (view.z > 0.5 || state === 'lit' || hovered)) drawTag(yarn, state, state === 'lit' || hovered);
    ctx.globalAlpha = 1;
  }

  function drawPin(card, tied, ctx) {
    const dim = card.el.classList.contains('is-dimmed');
    const hot = card.el.classList.contains('is-selected') || card.el.matches(':hover, :focus-visible');
    const r = compact ? 7.5 : 6.4;
    const { x, y } = card;
    ctx.globalAlpha = dim ? 0.4 : 1;
    ctx.beginPath(); ctx.ellipse(x + 2.4, y + 4.2, r * 1.05, r * 0.8, 0, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(10, 5, 2, 0.42)'; ctx.fill();
    if (tied) { // yarn wound round the pin shaft
      ctx.beginPath(); ctx.arc(x, y + 0.6, r * 0.95, 0, Math.PI * 2);
      ctx.strokeStyle = '#6a0c16'; ctx.lineWidth = r * 0.5; ctx.stroke();
    }
    if (hot) {
      ctx.beginPath(); ctx.arc(x, y, r * 1.9, 0, Math.PI * 2);
      const glow = ctx.createRadialGradient(x, y, r * 0.6, x, y, r * 1.9);
      glow.addColorStop(0, 'rgba(255, 196, 110, 0.5)'); glow.addColorStop(1, 'rgba(255, 196, 110, 0)');
      ctx.fillStyle = glow; ctx.fill();
    }
    const head = ctx.createRadialGradient(x - r * 0.35, y - r * 0.42, r * 0.08, x, y, r);
    head.addColorStop(0, hot ? '#fff8dc' : '#f6e3aa');
    head.addColorStop(0.36, '#cfa655'); head.addColorStop(0.8, '#84632a'); head.addColorStop(1, '#4a3614');
    ctx.beginPath(); ctx.arc(x, y, r * 0.82, 0, Math.PI * 2);
    ctx.fillStyle = head; ctx.fill();
    ctx.strokeStyle = 'rgba(38, 24, 8, 0.7)'; ctx.lineWidth = 0.6; ctx.stroke();
    ctx.globalAlpha = 1;
  }

  function draw() {
    frame = 0;
    if (destroyed) return;
    const dpr = Math.min(2, devicePixelRatio || 1);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    ctx.translate(view.x, view.y); ctx.scale(view.z, view.z);
    const order = [...yarns].sort((p, q) => (yarnState(p) === 'lit') - (yarnState(q) === 'lit'));
    for (const yarn of order) drawYarn(yarn);
    const tied = new Set(edges.flatMap((e) => [e.source_id, e.target_id]));
    pinCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    pinCtx.clearRect(0, 0, pins.width / dpr, pins.height / dpr);
    pinCtx.translate(view.x, view.y); pinCtx.scale(view.z, view.z);
    for (const card of cards.values()) drawPin(card, tied.has(card.node.id), pinCtx);
  }

  function requestDraw() {
    if (!frame && !simulating) frame = requestAnimationFrame(draw);
  }

  function resize() {
    const dpr = Math.min(2, devicePixelRatio || 1);
    canvas.width = Math.max(1, Math.round(container.clientWidth * dpr));
    canvas.height = Math.max(1, Math.round(container.clientHeight * dpr));
    pins.width = canvas.width; pins.height = canvas.height;
    if (isPortrait() !== portrait && !cardsMoved) { // re-compose when the window flips shape
      portrait = !portrait;
      const next = layout(nodes, edges, compact, portrait).positions;
      for (const [id, card] of cards) {
        Object.assign(card, next.get(id));
        card.el.style.left = `${card.x}px`; card.el.style.top = `${card.y}px`;
      }
      userMoved = false;
    }
    if (!userMoved) fit(); else requestDraw();
  }

  // ---- selection ------------------------------------------------------
  function select(next, { silent = false } = {}) {
    selection = next;
    const related = new Set();
    if (next?.type === 'source') {
      for (const e of edges) {
        if (e.source_id === next.id) related.add(e.target_id);
        if (e.target_id === next.id) related.add(e.source_id);
      }
    } else if (next?.type === 'edge') { related.add(edges[next.index].source_id); related.add(edges[next.index].target_id); }
    container.classList.toggle('has-focus', Boolean(next));
    for (const [id, card] of cards) {
      const selected = next?.type === 'source' && next.id === id;
      card.el.classList.toggle('is-selected', selected);
      card.el.classList.toggle('is-related', !selected && related.has(id));
      card.el.classList.toggle('is-dimmed', Boolean(next) && !selected && !related.has(id));
      card.el.setAttribute('aria-pressed', String(selected));
    }
    requestDraw();
    if (!silent) onSelect(next);
  }

  // ---- pointer handling ----------------------------------------------
  const toWorld = (event) => {
    const bounds = container.getBoundingClientRect();
    const rect = { left: bounds.left + container.clientLeft, top: bounds.top + container.clientTop };
    return { sx: event.clientX - rect.left, sy: event.clientY - rect.top, wx: (event.clientX - rect.left - view.x) / view.z, wy: (event.clientY - rect.top - view.y) / view.z };
  };
  let drag = null;
  let suppressClick = false;
  let lastPointer = null;

  function onPointerDown(event) {
    if (event.button !== 0 || drag) return;
    const cardEl = event.target.closest?.('.ev-card');
    const p = toWorld(event);
    drag = {
      kind: cardEl ? 'card' : nearestYarn(p.wx, p.wy) ? 'yarn' : 'pan',
      card: cardEl && cards.get(cardEl.dataset.id), sx: event.clientX, sy: event.clientY,
      originX: view.x, originY: view.y, lastWx: p.wx, lastWy: p.wy, moved: false,
    };
    if (compact && drag.kind !== 'yarn') drag.kind = 'none'; // the preview stays put so the panel can scroll
  }

  function onPointerMove(event) {
    const p = toWorld(event);
    const now = performance.now();
    let vx = 0; let vy = 0;
    if (lastPointer) {
      const dt = Math.max(8, now - lastPointer.t) / 1000;
      vx = (p.wx - lastPointer.wx) / dt; vy = (p.wy - lastPointer.wy) / dt;
    }
    lastPointer = { wx: p.wx, wy: p.wy, t: now };
    if (drag && (drag.kind === 'pan' || drag.kind === 'card')) {
      if (!drag.moved && Math.hypot(event.clientX - drag.sx, event.clientY - drag.sy) < 4) return;
      if (!drag.moved) { drag.moved = true; container.setPointerCapture?.(event.pointerId); container.classList.add('is-dragging'); }
      if (drag.kind === 'pan') {
        userMoved = true;
        cancelAnimationFrame(viewTween);
        view.x = drag.originX + event.clientX - drag.sx; view.y = drag.originY + event.clientY - drag.sy;
        applyView();
      } else {
        const dx = p.wx - drag.lastWx; const dy = p.wy - drag.lastWy;
        drag.lastWx = p.wx; drag.lastWy = p.wy;
        const card = drag.card;
        cardsMoved = true;
        card.x += dx; card.y += dy;
        card.el.style.left = `${card.x}px`; card.el.style.top = `${card.y}px`;
        if (!reducedMotion()) {
          for (const yarn of yarns) { // attached strings lag a little behind the moving pin
            if (yarn.a !== card && yarn.b !== card) continue;
            for (let i = 1; i < SEG; i += 1) {
              const t = i / SEG;
              const lag = (yarn.a === card ? 1 - t : t) * Math.sin(Math.PI * t) * 0.5;
              yarn.off[i * 2] -= dx * lag; yarn.off[i * 2 + 1] -= dy * lag;
            }
            yarn.active = true;
          }
          wake();
        }
        requestDraw();
      }
      return;
    }
    const hit = event.target.closest?.('.ev-card') ? null : nearestYarn(p.wx, p.wy);
    const next = hit?.yarn || null;
    if (next !== hoverYarn) {
      hoverYarn = next;
      container.classList.toggle('on-yarn', Boolean(next));
      requestDraw();
    }
    if (hit) pluck(hit.yarn, hit.at, vx, vy);
  }

  function onPointerUp() {
    if (drag?.moved) suppressClick = true;
    container.classList.remove('is-dragging');
    drag = null;
  }

  function onClick(event) {
    if (suppressClick) { suppressClick = false; event.stopPropagation(); return; }
    const cardEl = event.target.closest?.('.ev-card');
    if (cardEl) {
      const id = cardEl.dataset.id;
      select(selection?.type === 'source' && selection.id === id ? null : { type: 'source', id });
    } else {
      const p = toWorld(event);
      const hit = nearestYarn(p.wx, p.wy);
      if (hit) { event.stopPropagation(); select({ type: 'edge', index: hit.yarn.index }); }
      else if (selection) select(null);
    }
  }

  function onKeyDown(event) {
    const cardEl = event.target.closest?.('.ev-card');
    if (cardEl && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      select({ type: 'source', id: cardEl.dataset.id });
    } else if (event.key === 'Escape' && selection) select(null);
  }

  function onWheel(event) {
    event.preventDefault();
    const p = toWorld(event);
    zoomAt(Math.exp(-event.deltaY * (event.ctrlKey ? 0.012 : 0.0016)), p.sx, p.sy);
  }

  function onLeave() {
    lastPointer = null;
    if (hoverYarn) { hoverYarn = null; container.classList.remove('on-yarn'); requestDraw(); }
  }

  container.addEventListener('pointerdown', onPointerDown);
  container.addEventListener('pointermove', onPointerMove);
  container.addEventListener('pointerup', onPointerUp);
  container.addEventListener('pointercancel', onPointerUp);
  container.addEventListener('pointerleave', onLeave);
  container.addEventListener('click', onClick, true);
  container.addEventListener('keydown', onKeyDown);
  const repaint = () => requestDraw(); // pin highlights follow card hover and focus
  world.addEventListener('pointerover', repaint);
  world.addEventListener('pointerout', repaint);
  world.addEventListener('focusin', repaint);
  world.addEventListener('focusout', repaint);
  const onDoubleClick = (event) => { if (!event.target.closest?.('.ev-card')) fit(true); };
  if (!compact) {
    container.addEventListener('wheel', onWheel, { passive: false });
    container.addEventListener('dblclick', onDoubleClick);
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();
  document.fonts?.ready.then(() => { if (!destroyed && !userMoved) fit(); });

  return {
    fit: () => fit(true),
    zoomBy: (factor) => zoomAt(factor, undefined, undefined, true),
    select,
    focusSource(id) {
      const card = cards.get(id);
      if (!card) return;
      measure();
      const top = 70;
      const bottom = inset.bottom || 0;
      const available = Math.max(80, container.clientHeight - top - bottom - 30);
      const z = Math.max(0.2, Math.min(1.35, (container.clientWidth - 60) / card.w, available / card.h));
      userMoved = true;
      setView({ z, x: container.clientWidth / 2 - card.x * z,
        y: top + available / 2 - (card.y + card.h / 2 - 14) * z }, true);
      select({ type: 'source', id });
    },
    get selection() { return selection; },
    destroy() {
      destroyed = true;
      observer.disconnect();
      cancelAnimationFrame(frame); cancelAnimationFrame(viewTween);
      container.removeEventListener('pointerdown', onPointerDown);
      container.removeEventListener('pointermove', onPointerMove);
      container.removeEventListener('pointerup', onPointerUp);
      container.removeEventListener('pointercancel', onPointerUp);
      container.removeEventListener('pointerleave', onLeave);
      container.removeEventListener('click', onClick, true);
      container.removeEventListener('keydown', onKeyDown);
      container.removeEventListener('wheel', onWheel);
      container.removeEventListener('dblclick', onDoubleClick);
      container.classList.remove('board', 'board-compact', 'has-focus', 'on-yarn', 'is-dragging');
      container.style.backgroundPosition = '';
      container.replaceChildren();
    },
  };
}
