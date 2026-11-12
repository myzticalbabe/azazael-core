import { Graph } from "@cosmograph/cosmos";
import { inflate } from "pako";

// --- global data (injected by script tags in the HTML) ---------------------
const PALETTE = [
  "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F", "#EDC948",
  "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC", "#86BCB6", "#BEE19E",
];

const container = document.getElementById("graph");
const searchInput = document.getElementById("search");
const searchResults = document.getElementById("search-results");
const infoContent = document.getElementById("info-content");
const treeEl = document.getElementById("tree-pane");
const commEl = document.getElementById("comm-pane");
const totalLabel = document.getElementById("total-label");

let labels = window.GV_LABELS || [];
let positions = window.GV_POS;
let links = window.GV_LINKS;
let comm = window.GV_COMM;
let sizes = window.GV_SIZE;
let tree = window.GV_TREE || { dirs: [], communities: [], label: "graph" };
let N = labels.length;

// colors: community palette (Float32Array rgba)
const colors = new Float32Array(N * 4);
for (let i = 0; i < N; i++) {
  const c = PALETTE[comm[i] % PALETTE.length];
  const r = parseInt(c.slice(1, 3), 16) / 255;
  const g = parseInt(c.slice(3, 5), 16) / 255;
  const b = parseInt(c.slice(5, 7), 16) / 255;
  colors[i * 4] = r; colors[i * 4 + 1] = g; colors[i * 4 + 2] = b; colors[i * 4 + 3] = 0.9;
}

// label lookup by index
function nodeAt(i) { return labels[i]; }

// --- Cosmos graph -----------------------------------------------------------
// Single-file mode uses the precomputed static layout (physics off) so the
// page renders instantly at fixed positions; chunked mode lets the GPU
// simulation refine the layout live.
const staticLayout = !!window.GV_LABELS_GZ;
const graph = new Graph(container, {
  spaceSize: 8192,
  enableSimulation: !staticLayout,
  simulationFriction: 0.1,
  simulationGravity: 0.02,
  simulationRepulsion: 0.6,
  simulationLinkSpring: 0.4,
  simulationLinkDistance: 40,
  simulationDecay: 0.001,
  pointSizeScale: 1.4,
  linkColor: "rgba(120,130,180,0.18)",
  linkWidth: 0.5,
  curvedLinks: true,
  backgroundColor: "#0f0f1a",
  fitViewOnInit: true,
  fitViewDelay: 200,
  fitViewPadding: 0.05,
  enableDrag: true,
  highlightedPointIndices: [],
  pointColors: colors,
  pointSizes: sizes,
  onClick: (index) => {
    if (index === undefined) { infoContent.innerHTML = '<span class="empty">Click a node to inspect it</span>'; return; }
    showInfo(index);
  },
  onPointMouseOver: (index) => {
    if (index === undefined) return;
    const n = nodeAt(index);
    if (n) container.title = `${n.label}  —  ${n.file}`;
  },
});

// In single-file mode, positions/links arrive async after decompression;
// setPointPositions/setLinks are called from bootSingleFile instead.
if (positions && links) {
  graph.setPointPositions(positions);
  graph.setLinks(links);
}

let highlighted = [];

function setHighlight(indices, color) {
  if (color) {
    const hc = new Float32Array(N * 4);
    hc.set(colors);
    for (const i of indices) {
      const r = parseInt(color.slice(1, 3), 16) / 255;
      const g = parseInt(color.slice(3, 5), 16) / 255;
      const b = parseInt(color.slice(5, 7), 16) / 255;
      hc[i * 4] = 1; hc[i * 4 + 1] = 1; hc[i * 4 + 2] = 0.4; hc[i * 4 + 3] = 1;
    }
    graph.setPointColors(hc);
  } else {
    graph.setPointColors(colors);
  }
  graph.setConfigPartial({ highlightedPointIndices: indices.slice(0, 20000) });
  highlighted = indices;
}

function showInfo(index) {
  const n = nodeAt(index);
  if (!n) return;
  const cid = comm[index];
  const neighbors = graph.getNeighboringPointIndices(index);
  const neighHtml = neighbors.slice(0, 30).map((ni) => {
    const nb = nodeAt(ni);
    const cc = PALETTE[comm[ni] % PALETTE.length];
    return `<span class="neighbor-link" style="border-left-color:${cc}" data-nid="${ni}">${esc(nb ? nb.label : ni)}</span>`;
  }).join("");
  infoContent.innerHTML = `
    <div class="field"><b>${esc(n.label)}</b></div>
    <div class="field">Type: ${esc(n.type || "unknown")}</div>
    <div class="field">Community: ${esc("Community " + cid)}</div>
    <div class="field">Source: ${esc(n.file || "-")}</div>
    <div class="field">Degree: ${n.deg || 0}</div>
    ${neighbors.length ? `<div class="field" style="margin-top:8px;color:#aaa;font-size:11px">Neighbors (${neighbors.length})</div><div id="neighbors-list">${neighHtml}</div>` : ""}`;
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

document.addEventListener("click", (e) => {
  const el = e.target.closest(".neighbor-link");
  if (el && el.dataset.nid !== undefined) {
    const idx = Number(el.dataset.nid);
    graph.zoomToPointByIndex(idx, 800, 1.6);
    showInfo(idx);
  }
});

// --- search -----------------------------------------------------------------
searchInput.addEventListener("input", () => {
  const q = searchInput.value.toLowerCase().trim();
  searchResults.innerHTML = "";
  if (!q) { searchResults.style.display = "none"; return; }
  const matches = [];
  for (let i = 0; i < N && matches.length < 25; i++) {
    if (nodeAt(i).label.toLowerCase().includes(q)) matches.push(i);
  }
  if (!matches.length) { searchResults.style.display = "none"; return; }
  searchResults.style.display = "block";
  for (const idx of matches) {
    const n = nodeAt(idx);
    const el = document.createElement("div");
    el.className = "search-item";
    el.textContent = n.label;
    el.style.borderLeft = `3px solid ${PALETTE[comm[idx] % PALETTE.length]}`;
    el.onclick = () => {
      graph.zoomToPointByIndex(idx, 800, 1.8);
      showInfo(idx);
      searchResults.style.display = "none";
      searchInput.value = "";
      setHighlight([idx], "#ffffff");
    };
    searchResults.appendChild(el);
  }
});
document.addEventListener("click", (e) => {
  if (!searchResults.contains(e.target) && e.target !== searchInput) searchResults.style.display = "none";
});

// --- mind-map: directory tree ------------------------------------------------
let treeRoot = null;
function buildTree() {
  const root = { name: "", path: "", children: new Map(), count: 0, files: 0 };
  for (const d of tree.dirs) {
    const parts = d.path.split("/");
    let node = root;
    for (const p of parts) {
      if (!node.children.has(p)) {
        node.children.set(p, { name: p, path: node.path ? `${node.path}/${p}` : p, children: new Map(), count: 0, files: 0 });
      }
      node = node.children.get(p);
    }
    node.count = d.count; node.files = d.files;
  }
  function countUp(node) {
    let c = node.count || 0;
    for (const child of node.children.values()) c += countUp(child);
    node._count = c;
    return c;
  }
  countUp(root);
  return root;
}

treeRoot = buildTree();

function selectNodesFor(node) {
  const prefix = node.path;
  if (!prefix) return [];
  const out = [];
  for (let i = 0; i < N; i++) {
    if (nodeAt(i).file.startsWith(prefix)) out.push(i);
  }
  return out;
}

function renderTree() {
  treeEl.innerHTML = "";
  function makeItem(name, node, depth) {
    const item = document.createElement("div");
    item.className = "tree-item";
    item.style.paddingLeft = `${8 + depth * 14}px`;
    const hasKids = node.children.size > 0;
    const tw = document.createElement("span");
    tw.className = "tw";
    tw.textContent = hasKids ? "▸" : "·";
    const lbl = document.createElement("span");
    lbl.className = "tl";
    lbl.textContent = name;
    const cnt = document.createElement("span");
    cnt.className = "tc";
    cnt.textContent = node._count;
    item.appendChild(tw); item.appendChild(lbl); item.appendChild(cnt);
    item.onclick = () => {
      const sel = selectNodesFor(node);
      if (sel.length) {
        setHighlight(sel, "#FFD700");
        graph.fitViewByPointIndices(sel.slice(0, 20000), 800, 0.15);
        totalLabel.textContent = `${sel.length} nodes in ${name}`;
      }
    };
    treeEl.appendChild(item);
    if (hasKids) {
      const kids = document.createElement("div");
      kids.className = "kids";
      item.appendChild(kids);
      tw.onclick = (e) => { e.stopPropagation(); kids.style.display = kids.style.display === "none" ? "" : "none"; tw.textContent = kids.style.display === "none" ? "▸" : "▾"; };
      for (const [n, c] of [...node.children.entries()].sort((a, b) => b[1]._count - a[1]._count)) {
        kids.appendChild(makeItem(n, c, depth + 1));
      }
    }
    return item;
  }

  const top = [...treeRoot.children.entries()].sort((a, b) => b[1]._count - a[1]._count);
  for (const [name, child] of top.slice(0, 60)) {
    treeEl.appendChild(makeItem(name, child, 0));
  }
  if (top.length > 60) {
    const more = document.createElement("div");
    more.className = "tree-more";
    more.textContent = `+ ${top.length - 60} more top-level dirs (search to find them)`;
    treeEl.appendChild(more);
  }
}

// --- mind-map: community list ------------------------------------------------
function renderCommunities() {
  commEl.innerHTML = "";
  const comms = [...tree.communities].sort((a, b) => b.count - a.count);
  for (const c of comms.slice(0, 300)) {
    const item = document.createElement("div");
    item.className = "comm-item";
    const dot = document.createElement("span");
    dot.className = "comm-dot";
    dot.style.background = PALETTE[c.cid % PALETTE.length];
    const lbl = document.createElement("span");
    lbl.className = "comm-label";
    lbl.textContent = `Community ${c.cid}`;
    const cnt = document.createElement("span");
    cnt.className = "comm-count";
    cnt.textContent = c.count;
    item.appendChild(dot); item.appendChild(lbl); item.appendChild(cnt);
    item.title = (c.top_files || []).slice(0, 5).join("\n");
    item.onclick = () => {
      const sel = [];
      for (let i = 0; i < N; i++) if (comm[i] === c.cid) sel.push(i);
      if (sel.length) {
        setHighlight(sel, PALETTE[c.cid % PALETTE.length]);
        graph.fitViewByPointIndices(sel.slice(0, 10000), 800, 0.1);
        totalLabel.textContent = `${sel.length} nodes in Community ${c.cid}`;
      }
    };
    commEl.appendChild(item);
  }
}

// --- tab switching -----------------------------------------------------------
const tabTree = document.getElementById("tab-tree");
const tabComm = document.getElementById("tab-comm");
const treePane = document.getElementById("tree-pane");
const commPane = document.getElementById("comm-pane");
tabTree.onclick = () => { tabTree.classList.add("active"); tabComm.classList.remove("active"); treePane.style.display = ""; commPane.style.display = "none"; };
tabComm.onclick = () => { tabComm.classList.add("active"); tabTree.classList.remove("active"); commPane.style.display = ""; treePane.style.display = "none"; };

// --- boot -------------------------------------------------------------------
function boot() {
  totalLabel.textContent = `${N.toLocaleString()} nodes · ${(links.length / 2).toLocaleString()} links · ${(tree.communities || []).length.toLocaleString()} communities`;
  renderTree();
  renderCommunities();
  // marker so headless verification can confirm data boot completed even
  // when the WebGL simulation keeps the page busy
  document.body.setAttribute("data-cosmos-booted", String(N));
  graph.render();
  document.getElementById("loading").style.display = "none";
  container.style.display = "block";
}

function b64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

// Synchronous gzip inflate (pako) — deterministic, no async race, works
// identically under headless virtual-time and real browsers.
function gunzipBytes(bytes) {
  return inflate(bytes);
}

// Single-file mode: data embedded as compressed base64 payloads.
// GV_POS_Q/GV_SIZE_Q are quantized (uint16/uint8) with scale metadata;
// GV_LABELS_GZ/GV_TREE_GZ are gzip(base64) JSON.
function bootSingleFile() {
  document.getElementById("loading").textContent = "decompressing data…";
  const lab = gunzipBytes(b64ToBytes(window.GV_LABELS_GZ));
  const tr = window.GV_TREE_GZ ? gunzipBytes(b64ToBytes(window.GV_TREE_GZ)) : null;
  labels = JSON.parse(new TextDecoder().decode(lab));
  if (tr) tree = JSON.parse(new TextDecoder().decode(tr));
  treeRoot = buildTree();
  // un-quantize positions
  const pq = window.GV_POS_Q;
  const posF = new Float32Array(pq.data.length);
  for (let i = 0; i < pq.data.length; i++) {
    posF[i] = (pq.data[i] / 65535) * (pq.max - pq.min) + pq.min;
  }
  positions = posF;
  // un-quantize sizes
  const sq = window.GV_SIZE_Q;
  const sizeF = new Float32Array(sq.data.length);
  for (let i = 0; i < sq.data.length; i++) sizeF[i] = (sq.data[i] / 255) * (sq.max || 1);
  sizes = sizeF;
  N = labels.length;
  rebuildDerived();
  graph.setPointPositions(positions);
  graph.setLinks(links);
  boot();
}

// (re)build things derived from node data after single-file data arrives
function rebuildDerived() {
  for (let i = 0; i < N; i++) {
    const c = PALETTE[comm[i] % PALETTE.length];
    const r = parseInt(c.slice(1, 3), 16) / 255;
    const g = parseInt(c.slice(3, 5), 16) / 255;
    const b = parseInt(c.slice(5, 7), 16) / 255;
    colors[i * 4] = r; colors[i * 4 + 1] = g; colors[i * 4 + 2] = b; colors[i * 4 + 3] = 0.9;
  }
  graph.setPointColors(colors);
}

if (window.GV_LABELS_GZ) {
  bootSingleFile();
} else if (window.GV_LABELS && window.GV_POS && window.GV_LINKS) {
  boot();
} else {
  document.getElementById("loading").textContent = "data not loaded — open via the cosmos folder next to graph.json";
}

window.__COSMOS_READY__ = () => N;
