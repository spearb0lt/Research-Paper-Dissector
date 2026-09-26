/**
 * The workflow diagram: boxes and arrows, generated rather than drawn.
 *
 * Two panels, the shape a reader expects of a system diagram. (a) is what
 * happens to a PDF on the way in, (b) is what happens to a question on the way
 * out. It is written as a graph of nodes and edges with explicit coordinates,
 * because a layout engine reflows the whole picture every time a label changes
 * and the point of committing a diagram is that it stays put.
 *
 * Colours are the application's own kind colours from app/globals.css, so a
 * table is the same purple here as it is on an element card.
 *
 * Run through `npm run diagram`, which writes the SVG, wraps it in a page and
 * renders that to a PNG for the README and a PDF that stays searchable.
 */

export const WIDTH = 1800;
export const HEIGHT = 1200;

// ------------------------------------------------------------------ palette

const C = {
  text: { s: "#6b7280", f: "#f3f4f6" },
  table: { s: "#7c3aed", f: "#f3ebff" },
  figure: { s: "#c2410c", f: "#ffeee4" },
  formula: { s: "#0369a1", f: "#e2f1fb" },
  reference: { s: "#4d7c0f", f: "#eef5dd" },
  accent: { s: "#0f766e", f: "#d6f0ec" },
  good: { s: "#15803d", f: "#e5f4e9" },
  warn: { s: "#b45309", f: "#fdf0dd" },
  plain: { s: "#9ca3af", f: "#ffffff" },
};

const INK = "#1c1a18";
const MUTED = "#55504b";
const FAINT = "#8a837c";
const LINE = "#8f8880";
const LINE_SOFT = "#b8b1aa";

// ------------------------------------------------------------------- layout
//
// Columns are fixed rather than computed. Panel (a) has seven stages, panel (b)
// has eight, and both are laid across the same 1700pt of usable width.

const AX = [50, 272, 509, 776, 1033, 1280, 1537];
const AW = [160, 175, 205, 195, 185, 195, 215];
const BX = [50, 238, 461, 674, 907, 1130, 1348, 1566];
const BW = [150, 185, 175, 195, 185, 180, 180, 185];
const BY = 760;

const nodes = {};
const edges = [];

const node = (id, spec) => (nodes[id] = { id, ...spec });
const edge = (from, to, opts = {}) => edges.push({ from, to, ...opts });

function anchor(n, side) {
  if (side === "right") return { x: n.x + n.w, y: n.y + n.h / 2 };
  if (side === "left") return { x: n.x, y: n.y + n.h / 2 };
  if (side === "top") return { x: n.x + n.w / 2, y: n.y };
  return { x: n.x + n.w / 2, y: n.y + n.h };
}

// ================================================================ panel (a)

node("pdf", {
  x: AX[0], y: 258, w: AW[0], h: 96, colour: C.plain, stack: 2,
  title: "Research PDF",
  lines: ["upload, arXiv id,", "DOI or URL"],
});

node("fast", {
  x: AX[1], y: 232, w: AW[1], h: 92, colour: C.warn,
  title: "Fast parser",
  lines: ["PyMuPDF + pdfplumber", "no models, ~25 MB"],
});

node("deep", {
  x: AX[1], y: 404, w: AW[1], h: 76, colour: C.plain, dashed: true,
  title: "Deep parser",
  lines: ["Docling + TableFormer", "off on serverless"],
});

const KIND_Y = [150, 216, 282, 348, 414];
node("k_text", {
  x: AX[2], y: KIND_Y[0], w: AW[2], h: 52, colour: C.text,
  title: "Text runs", lines: ["headings, paragraphs, lists"],
});
node("k_table", {
  x: AX[2], y: KIND_Y[1], w: AW[2], h: 52, colour: C.table,
  title: "Tables", lines: ["ruling bands, x-projection"],
});
node("k_fig", {
  x: AX[2], y: KIND_Y[2], w: AW[2], h: 52, colour: C.figure,
  title: "Figures", lines: ["embedded, plus rendered crops"],
});
node("k_form", {
  x: AX[2], y: KIND_Y[3], w: AW[2], h: 52, colour: C.formula,
  title: "Equations", lines: ["no body font on the line"],
});
node("k_ref", {
  x: AX[2], y: KIND_Y[4], w: AW[2], h: 52, colour: C.reference,
  title: "References", lines: ["split, markers linked"],
});

node("elements", {
  x: AX[3], y: 228, w: AW[3], h: 128, colour: C.accent,
  title: "elements",
  lines: ["one row per element", "kind, page, bbox, order", "section path, label, caption"],
});

node("chunks", {
  x: AX[4], y: 240, w: AW[4], h: 104, colour: C.accent,
  title: "Chunker",
  lines: ["450 tokens, 80 overlap", "section path prepended", "tables and figures whole"],
});

node("lex", {
  x: AX[5], y: 214, w: AW[5], h: 76, colour: C.formula,
  title: "BM25 index",
  lines: ["science tokeniser keeps", "41.0 and BLEU-4 whole"],
});

node("dense", {
  x: AX[5], y: 322, w: AW[5], h: 76, colour: C.good,
  title: "Dense matrix",
  lines: ["ONNX MiniLM, 384-d", "numpy, exact, no service"],
});

node("papers", {
  x: AX[6], y: 248, w: AW[6], h: 92, colour: C.plain, stack: 2,
  title: "papers row",
  lines: ["lexical_index, dense_index", "stored as BLOBs, read whole"],
});

node("blobs", {
  x: 1010, y: 546, w: 330, h: 94, colour: C.plain, cylinder: true,
  title: "Blob store, addressed by SHA-256",
  lines: ["PDF, figure crops, page renders", "the disk, or the database where there is none"],
});

edge("pdf", "fast");
edge("pdf", "deep", { dashed: true });
for (const k of ["k_text", "k_table", "k_fig", "k_form", "k_ref"]) edge("fast", k);
edge("deep", "elements", { dashed: true, fromSide: "bottom", toSide: "bottom", sag: 130 });
for (const k of ["k_text", "k_table", "k_fig", "k_form", "k_ref"]) edge(k, "elements");
edge("elements", "chunks");
edge("chunks", "lex");
edge("chunks", "dense");
edge("lex", "papers");
edge("dense", "papers");
// The two long dashed runs into the store. This is what a content addressed
// blob store looks like on a diagram: things write into it, nothing flows
// through it, so the edges sag away from the main line rather than joining it.
edge("pdf", "blobs", { dashed: true, fromSide: "bottom", toSide: "left", sag: 215, label: "the file itself" });
edge("k_fig", "blobs", { dashed: true, fromSide: "bottom", toSide: "top", sag: 205, label: "figure crops" });

// ================================================================ panel (b)

node("q", {
  x: BX[0], y: BY + 96, w: BW[0], h: 86, colour: C.plain,
  title: "User question",
  lines: ["one paper, or", "the whole library"],
});

node("plan", {
  x: BX[1], y: BY + 84, w: BW[1], h: 110, colour: C.accent,
  title: "Query analysis",
  lines: ["label pinning:", "“Figure 3” is a lookup,", "not a ranking problem"],
});

node("legA", {
  x: BX[2], y: BY + 44, w: BW[2], h: 78, colour: C.formula,
  title: "Leg A, lexical",
  lines: ["BM25 over chunks,", "cells and captions"],
});

node("legB", {
  x: BX[2], y: BY + 158, w: BW[2], h: 78, colour: C.good,
  title: "Leg B, dense",
  lines: ["cosine over the", "384-d matrix"],
});

node("fuse", {
  x: BX[3], y: BY + 84, w: BW[3], h: 108, colour: C.accent,
  title: "Reciprocal rank fusion",
  lines: ["k = 60, weights shift", "with the query shape,", "then diversify and pin"],
});

node("rerank", {
  x: BX[3], y: BY + 232, w: BW[3], h: 62, colour: C.plain, dashed: true,
  title: "Cross encoder",
  lines: ["optional, needs PyTorch"],
});

node("evidence", {
  x: BX[4], y: BY + 70, w: BW[4], h: 136, colour: C.good, thick: true,
  title: "Ranked evidence",
  lines: ["page, score, and which", "leg found it", "", "No model needed.", "Many questions stop here."],
});

node("context", {
  x: BX[5], y: BY + 84, w: BW[5], h: 108, colour: C.warn,
  title: "Context assembly",
  lines: ["numbered excerpts", "with paper and page,", "figure crops attached"],
});

node("provider", {
  x: BX[6], y: BY + 84, w: BW[6], h: 108, colour: C.formula,
  title: "Model provider",
  lines: ["19, key from the", "browser or the server,", "streamed as deltas"],
});

node("answer", {
  x: BX[7], y: BY + 84, w: BW[7], h: 108, colour: C.good,
  title: "Cited answer",
  lines: ["every [n] resolved", "against an excerpt,", "the rest stripped"],
});

edge("q", "plan");
edge("plan", "legA");
edge("plan", "legB");
edge("legA", "fuse");
edge("legB", "fuse");
edge("fuse", "rerank", { dashed: true, fromSide: "bottom", toSide: "top" });
edge("fuse", "evidence");
edge("rerank", "evidence", { dashed: true });
edge("evidence", "context");
edge("context", "provider");
edge("provider", "answer");

// ------------------------------------------------------------------ drawing

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function drawBox(n) {
  const { x, y, w, h, colour } = n;
  const dash = n.dashed ? ' stroke-dasharray="6 4"' : "";
  const sw = n.thick ? 2.6 : 1.8;
  let out = "";

  // Stacked copies behind, for the stores that hold many of a thing.
  for (let i = n.stack || 0; i > 0; i--) {
    out += `<rect x="${x + i * 6}" y="${y + i * 6}" width="${w}" height="${h}" rx="10" `
      + `fill="${colour.f}" stroke="${colour.s}" stroke-width="1.2" opacity="0.45"/>`;
  }

  if (n.cylinder) {
    const ry = 13;
    out += `<path d="M${x} ${y + ry} a ${w / 2} ${ry} 0 0 1 ${w} 0 v ${h - ry * 2} `
      + `a ${w / 2} ${ry} 0 0 1 ${-w} 0 z" fill="${colour.f}" stroke="${colour.s}" stroke-width="${sw}"/>`;
    out += `<path d="M${x} ${y + ry} a ${w / 2} ${ry} 0 0 0 ${w} 0" fill="none" `
      + `stroke="${colour.s}" stroke-width="${sw}"/>`;
  } else {
    out += `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10" `
      + `fill="${colour.f}" stroke="${colour.s}" stroke-width="${sw}"${dash}/>`;
  }

  const lines = n.lines || [];
  const titleSize = 13.5;
  const bodySize = 11.8;
  const lead = 15;
  const block = titleSize + 4 + lines.length * lead;
  const cx = x + w / 2;
  let cy = y + h / 2 - block / 2 + titleSize + (n.cylinder ? 7 : 0);

  out += `<text x="${cx}" y="${cy}" text-anchor="middle" font-size="${titleSize}" `
    + `font-weight="650" fill="${INK}">${esc(n.title)}</text>`;
  cy += 4;
  for (const line of lines) {
    cy += lead;
    if (!line) continue;
    out += `<text x="${cx}" y="${cy}" text-anchor="middle" font-size="${bodySize}" `
      + `fill="${MUTED}">${esc(line)}</text>`;
  }
  return out;
}

function drawEdge(e) {
  const p1 = anchor(nodes[e.from], e.fromSide || "right");
  const p2 = anchor(nodes[e.to], e.toSide || "left");
  const stroke = e.dashed ? LINE_SOFT : LINE;
  const dash = e.dashed ? ' stroke-dasharray="6 5"' : "";
  const marker = e.dashed ? "ad" : "a";

  let d;
  if (e.sag !== undefined) {
    // Deliberately bowed, so an aside does not read as part of the flow.
    const mx = (p1.x + p2.x) / 2;
    d = `M${p1.x} ${p1.y} C ${p1.x} ${p1.y + e.sag}, ${mx} ${p2.y + 46}, ${p2.x} ${p2.y}`;
  } else if (Math.abs(p1.y - p2.y) < 2.5) {
    d = `M${p1.x} ${p1.y} L ${p2.x} ${p2.y}`;
  } else if (Math.abs(p1.x - p2.x) < 2.5) {
    d = `M${p1.x} ${p1.y} L ${p2.x} ${p2.y}`;
  } else {
    const dx = Math.max(26, (p2.x - p1.x) * 0.45);
    d = `M${p1.x} ${p1.y} C ${p1.x + dx} ${p1.y}, ${p2.x - dx} ${p2.y}, ${p2.x} ${p2.y}`;
  }

  let out = `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.6"${dash} marker-end="url(#${marker})"/>`;
  if (e.label) {
    // B(0.5) of the cubic, so a label on a bowed edge sits on the curve rather
    // than on the straight line between its endpoints, which is nowhere near it.
    const lx = e.sag === undefined
      ? (p1.x + p2.x) / 2
      : (p1.x + 3 * p1.x + 3 * ((p1.x + p2.x) / 2) + p2.x) / 8;
    const ly = e.sag === undefined
      ? (p1.y + p2.y) / 2 - 7
      : (p1.y + 3 * (p1.y + e.sag) + 3 * (p2.y + 46) + p2.y) / 8 + 14;
    out += `<text x="${lx}" y="${ly}" text-anchor="middle" font-size="10.5" `
      + `fill="${FAINT}" font-style="italic">${esc(e.label)}</text>`;
  }
  return out;
}

export function buildSvg() {
  let out = `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" `
    + `viewBox="0 0 ${WIDTH} ${HEIGHT}" `
    + `font-family="ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif">`;

  out += `<defs>`
    + `<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">`
    + `<path d="M0 0 L10 5 L0 10 z" fill="${LINE}"/></marker>`
    + `<marker id="ad" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">`
    + `<path d="M0 0 L10 5 L0 10 z" fill="${LINE_SOFT}"/></marker>`
    + `</defs>`;

  out += `<rect width="${WIDTH}" height="${HEIGHT}" fill="#ffffff"/>`;

  out += `<text x="${WIDTH / 2}" y="46" text-anchor="middle" font-size="26" font-weight="680" fill="${INK}">`
    + `Dissect: how a paper becomes an answer you can check</text>`;
  out += `<text x="${WIDTH / 2}" y="72" text-anchor="middle" font-size="13.5" fill="${MUTED}">`
    + `Element level extraction &#183; hybrid retrieval with no vector database &#183; every citation resolves to a box on a page</text>`;

  out += `<text x="50" y="118" font-size="16" font-weight="700" fill="${INK}">`
    + `(a)&#160;&#160;Ingestion, extraction and indexing</text>`;
  out += `<line x1="50" y1="${BY - 76}" x2="${WIDTH - 50}" y2="${BY - 76}" stroke="#e3dfdb" stroke-width="1.5"/>`;
  out += `<text x="50" y="${BY - 40}" font-size="16" font-weight="700" fill="${INK}">`
    + `(b)&#160;&#160;Question, retrieval and a grounded answer</text>`;

  for (const e of edges) out += drawEdge(e);
  for (const id of Object.keys(nodes)) out += drawBox(nodes[id]);

  const ans = nodes.answer;
  out += `<text x="${ans.x + ans.w / 2}" y="${ans.y + ans.h + 24}" text-anchor="middle" `
    + `font-size="11" fill="${C.good.s}" font-weight="600">`
    + `[TEXT &#183; p7]&#160;&#160;[TABLE 2 &#183; p8]&#160;&#160;[FIG 3 &#183; p4]</text>`;
  out += `<text x="${ans.x + ans.w / 2}" y="${ans.y + ans.h + 41}" text-anchor="middle" `
    + `font-size="10.5" fill="${FAINT}">each opens the page with the box drawn on it</text>`;

  // ---------------------------------------------------------------- legend
  const ly = HEIGHT - 74;
  out += `<text x="50" y="${ly}" font-size="12" font-weight="700" fill="${INK}">`
    + `Element kinds, coloured as they are in the app:</text>`;
  let kx = 346;
  for (const [label, colour] of [
    ["Text", C.text], ["Tables", C.table], ["Figures", C.figure],
    ["Equations", C.formula], ["References", C.reference],
  ]) {
    out += `<rect x="${kx}" y="${ly - 11}" width="22" height="13" rx="3.5" `
      + `fill="${colour.f}" stroke="${colour.s}" stroke-width="1.6"/>`;
    out += `<text x="${kx + 29}" y="${ly}" font-size="12" fill="${MUTED}">${label}</text>`;
    kx += 29 + label.length * 7.2 + 32;
  }
  out += `<line x1="${kx + 4}" y1="${ly - 4}" x2="${kx + 40}" y2="${ly - 4}" `
    + `stroke="${LINE_SOFT}" stroke-width="1.6" stroke-dasharray="6 5"/>`;
  out += `<text x="${kx + 48}" y="${ly}" font-size="12" fill="${MUTED}">`
    + `optional, or unavailable on a serverless tier</text>`;

  out += `<text x="50" y="${HEIGHT - 34}" font-size="11" fill="${FAINT}">`
    + `Generated by scripts/workflow.mjs. Regenerate with npm run diagram.</text>`;
  out += `<text x="${WIDTH - 50}" y="${HEIGHT - 34}" text-anchor="end" font-size="11" `
    + `fill="${FAINT}" font-style="italic">github.com/spearb0lt/Research-Paper-Dissector</text>`;

  out += `</svg>`;
  return out;
}
