/**
 * graph.ts — the designer's data model, geometry and layout.
 *
 * Everything here is pure: given a catalogue and a graph it answers "how big is
 * this chip", "where is that port", "what does the wire look like". The Vue
 * components own the state and the events; nothing in this file touches the DOM,
 * which is what makes the geometry testable and — more usefully — what lets the
 * canvas draw an edge before the node it connects to has ever been rendered.
 */
import { fy } from "../format";

/* ------------------------------------------------------------------ types */

export interface ParamSpec {
  id: string;
  label: string;
  type: "int" | "float" | "select" | "text" | "textarea";
  default: number | string;
  min?: number;
  max?: number;
  step?: number;
  /** `short` is what the CHIP prints when this option is chosen; `l` is what
   *  the dropdown says. «روزانه» has an empty `short` so the default frame puts
   *  no badge on any box — a label every chip carries is a label nobody reads. */
  options?: { v: string; l: string; short?: string }[];
}

export interface PortSpec {
  id: string;
  label: string;
  kind: "num" | "bool" | "text";
  /** An input that accepts several wires — «And» is the only shape that needs it. */
  multi?: boolean;
  /** An input the block works without. «فرمول‌نویسی» offers four and a formula
   *  over price fields alone uses none of them, so lint() must not report the
   *  empty ones as mistakes. */
  optional?: boolean;
}

export interface NodeSpec {
  type: string;
  cat: string;
  /** Second level of the palette — «میانگین‌ها», «حجم», «الگوها». Server-side
   *  (filter_engine.NODE_TYPES), so a new block never edits the front end. */
  sub?: string;
  label: string;
  /** Chip caption template: `{p}` is a parameter, `{~p}` a shift suffix. */
  title: string;
  help?: string;
  inputs: PortSpec[];
  outputs: PortSpec[];
  params: ParamSpec[];
}

export interface Category {
  key: string;
  label: string;
  color: string;
}

export interface Catalog {
  categories: Category[];
  nodes: NodeSpec[];
  limits: { nodes: number; edges: number; period: number; within: number };
  examples: { key: string; name: string; desc: string; graph: Graph }[];
  kind: "stock" | "etf";
  groups: string[];
  group_label: string;
  group: string | null;
  subgroups: string[];
  subgroup: string | null;
  authenticated: boolean;
  as_of: string | null;
}

export type Params = Record<string, number | string>;

export interface GNode {
  id: string;
  type: string;
  x: number;
  y: number;
  params: Params;
}

export interface GEdge {
  from: string;
  fromPort: string;
  to: string;
  toPort: string;
}

export interface Graph {
  nodes: GNode[];
  edges: GEdge[];
}

export interface RunRow {
  id: number;
  ticker: string;
  name: string;
  group: string | null;
  latest: number | null;
  vals: Record<string, number | string | null>;
}

export interface RunResult {
  as_of: string | null;
  rows: RunRow[];
  count: number;
  scanned: number;
  bars: number;
  /** Symbols the engine could not evaluate at all. Normally 0; anything else is
   *  a defect, not a strict filter, and the panel says so out loud. */
  errors?: number;
  truncated?: boolean;
  columns: { id: string; label: string; digits: number; sort?: string; type?: string }[];
  kind: "stock" | "etf";
  /** The graph asked for more history than the deepest bucket holds — a monthly
   *  200-period average, typically. Every value it produced is still warming up,
   *  so the table is empty for a reason the user cannot otherwise see. */
  clipped?: boolean;
  server_ms: number;
}

export type PortValue =
  | { kind: "num"; tail: (number | null)[] }
  | { kind: "bool"; tail: (boolean | null)[] }
  | { kind: "const"; value: number }
  | { kind: "text"; value: string };

export interface Explain {
  ticker: string;
  name: string;
  as_of: string;
  matched: boolean;
  /** Bars back from the last candle where the filter actually fired (0 = today). */
  at: number;
  within: number;
  bars: number;
  ports: Record<string, PortValue>;
  /** The «برچسب سیگنال» blocks that were true on the bar the filter fired on. */
  signals?: string[];
}

/**
 * The value a port held on the bar the filter fired on.
 *
 * Every reader of an explained value goes through here. Reading the last element
 * instead — the obvious thing — answers a different question than the one the
 * user asked: they clicked «چرا؟» on a row the filter RETURNED, and for any
 * filter with «در N کندل اخیر» above 1 the last candle is usually the one where
 * the condition has gone false again.
 */
export function valueAt<T>(tail: T[], at: number): T | undefined {
  return tail[tail.length - 1 - Math.min(at, tail.length - 1)];
}

/* --------------------------------------------------------------- identity */

let seq = 0;

/** A short, collision-free node id. Short because it is stored in every saved
 *  graph and shown in nothing — a uuid would quadruple the JSON for no gain. */
export function newId(prefix = "n"): string {
  seq += 1;
  return `${prefix}${Date.now().toString(36).slice(-4)}${seq.toString(36)}`;
}

/** Re-key a pasted or example graph so it can be merged into an existing one. */
export function rekey(graph: Graph): Graph {
  const map = new Map<string, string>();
  const nodes = graph.nodes.map((n) => {
    const id = newId();
    map.set(n.id, id);
    return { ...n, id, params: { ...n.params } };
  });
  const edges = graph.edges
    .filter((e) => map.has(e.from) && map.has(e.to))
    .map((e) => ({ ...e, from: map.get(e.from)!, to: map.get(e.to)! }));
  return { nodes, edges };
}

/* ------------------------------------------------------------ chip caption */

/** A number the way the chips print it: no trailing `.0`, Persian digits when
 *  the account asked for them (the same `data-digits` every table obeys). */
function short(v: number | string | undefined): string {
  if (v === undefined || v === null) return "";
  if (typeof v === "string") return v;
  const s = Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6);
  return document.documentElement.dataset.digits === "en" ? s : fy(s);
}

/**
 * The caption on the chip, from the catalogue's template.
 *
 *   "{field}{~shift}"  + {field:"close", shift:1} → "close-۱"
 *   "SMA {n} {src}"    + {n:20, src:"final"}      → "SMA ۲۰ final"
 *
 * `{~p}` is the shift suffix and disappears at zero, which is the whole reason
 * the reference product can print a bare `close` next to a `close-1` and have
 * both read as one idea rather than as two different node types.
 */
export function chipTitle(spec: NodeSpec, node: GNode): string {
  const out = spec.title.replace(/\{(~?)(\w+)\}/g, (_m, tilde: string, key: string) => {
    const raw = node.params?.[key];
    if (tilde) {
      const num = Number(raw);
      return !num ? "" : `-${short(num)}`;
    }
    // A select prints its `short` when the catalogue gives it one. Without this
    // a weekly RSI would read «RSI ۱۴ final M» and a smoothed average «MA smma
    // ۲۰» — the stored value, not the word the user picked out of the dropdown.
    const p = spec.params.find((q) => q.id === key);
    if (p?.type === "select") {
      const opt = p.options?.find((o) => o.v === String(raw));
      if (opt && opt.short !== undefined) return opt.short;
    }
    return short(raw as number | string);
  });
  // Collapse what the empty substitutions left behind: «روزانه» and a zero
  // shift both render as nothing, and a caption is not allowed to end in the
  // gap where a badge would have been.
  return out.replace(/\s+/g, " ").trim();
}

/** A chip caption is one line on a canvas, not a paragraph. «فرمول‌نویسی» and
 *  «توضیحات» both carry free text that can run to 240 characters. */
const MAX_CAPTION = 34;

export function clipCaption(text: string): string {
  return text.length > MAX_CAPTION ? `${text.slice(0, MAX_CAPTION - 1)}…` : text;
}

/* ---------------------------------------------------------------- geometry */

export const GRID = 10;
/** Chip height per port row, and the floor for a chip with none. */
const ROW = 20;
const MIN_H = 30;

export interface Size {
  w: number;
  h: number;
}

/**
 * Chip size, computed from the caption rather than measured.
 *
 * Measuring would be more exact and is not worth it: an edge has to be drawn on
 * the same frame the node moves, and reading offsetWidth there forces a layout
 * per node per frame. The 7.4px-per-character estimate is within a few pixels
 * for both Vazirmatn and the Latin fallback at this size, and the ports are
 * anchored to the computed box, so the wire meets the dot exactly even when the
 * text does not fill it.
 */
export function nodeSize(spec: NodeSpec, title: string): Size {
  let px = 0;
  for (const ch of clipCaption(title)) px += ch.charCodeAt(0) > 0x600 ? 8.6 : 7.4;
  const rows = Math.max(spec.inputs.length, spec.outputs.length, 1);
  return {
    w: Math.min(280, Math.max(78, Math.round(px) + 30)),
    h: Math.max(MIN_H, ROW * rows),
  };
}

/** Vertical centre of port #i of `count`, relative to the chip's top edge. */
export function portY(h: number, i: number, count: number): number {
  return (h * (i + 1)) / (count + 1);
}

export interface Anchor {
  x: number;
  y: number;
}

export function outAnchor(node: GNode, spec: NodeSpec, size: Size, port: string): Anchor {
  const i = Math.max(0, spec.outputs.findIndex((p) => p.id === port));
  return { x: node.x + size.w, y: node.y + portY(size.h, i, spec.outputs.length) };
}

export function inAnchor(node: GNode, spec: NodeSpec, size: Size, port: string): Anchor {
  const i = Math.max(0, spec.inputs.findIndex((p) => p.id === port));
  return { x: node.x, y: node.y + portY(size.h, i, spec.inputs.length) };
}

/**
 * The wire: out to the right, across at the midpoint, in from the left, with
 * rounded corners — the orthogonal routing the reference product uses. A bezier
 * would be one line of code, but with fifteen wires converging on one «And» the
 * curves overlap into a braid and you can no longer tell which comparison feeds
 * what. Right angles stack.
 *
 * Falls back to a curve when the target is to the LEFT of the source, where a
 * midpoint column would run backwards through both chips.
 */
export function wirePath(a: Anchor, b: Anchor): string {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (Math.abs(dy) < 1.5) return `M${a.x},${a.y} L${b.x},${b.y}`;
  if (dx < 48) {
    const k = Math.max(60, Math.abs(dx) * 0.8 + 40);
    return `M${a.x},${a.y} C${a.x + k},${a.y} ${b.x - k},${b.y} ${b.x},${b.y}`;
  }
  const mx = a.x + dx / 2;
  const dir = dy > 0 ? 1 : -1;
  const r = Math.min(14, Math.abs(dy) / 2, dx / 2);
  return (
    `M${a.x},${a.y} L${mx - r},${a.y} Q${mx},${a.y} ${mx},${a.y + dir * r} ` +
    `L${mx},${b.y - dir * r} Q${mx},${b.y} ${mx + r},${b.y} L${b.x},${b.y}`
  );
}

/* ----------------------------------------------------------------- layout */

/**
 * Tidy the graph: longest-path layering left→right, then pack each column.
 *
 * Deliberately simple — no crossing minimisation. A designed filter is a wide,
 * shallow tree (twelve sources, one sink) where layering alone already produces
 * the reference product's own picture, and a heuristic that reordered rows would
 * move the user's chips somewhere they did not put them. This is the «مرتب‌سازی»
 * button, not a continuous auto-layout: it runs only when asked.
 */
export function autoLayout(graph: Graph, specs: Map<string, NodeSpec>): void {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const parents = new Map<string, string[]>();
  graph.nodes.forEach((n) => parents.set(n.id, []));
  graph.edges.forEach((e) => parents.get(e.to)?.push(e.from));

  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const depthOf = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // a cycle: the server will reject it anyway
    visiting.add(id);
    const ps = parents.get(id) ?? [];
    const d = ps.length ? Math.max(...ps.map(depthOf)) + 1 : 0;
    visiting.delete(id);
    depth.set(id, d);
    return d;
  };
  graph.nodes.forEach((n) => depthOf(n.id));

  // Column width follows the widest chip in it, so a long «MACD ۱۲,۲۶,۹ final»
  // does not overlap the column to its right.
  const cols = new Map<number, GNode[]>();
  graph.nodes.forEach((n) => {
    const d = depth.get(n.id) ?? 0;
    if (!cols.has(d)) cols.set(d, []);
    cols.get(d)!.push(n);
  });

  let x = 40;
  for (const d of [...cols.keys()].sort((p, q) => p - q)) {
    const column = cols.get(d)!;
    // Keep each column in the order the user already had it vertically, so a
    // tidy-up preserves the reading order they built.
    column.sort((p, q) => p.y - q.y);
    let widest = 0;
    let y = 40;
    for (const n of column) {
      const spec = specs.get(n.type);
      if (!spec) continue;
      const size = nodeSize(spec, chipTitle(spec, n));
      n.x = x;
      n.y = y;
      y += size.h + 46;
      widest = Math.max(widest, size.w);
    }
    x += widest + 110;
  }
  byId.clear();
}

/* ------------------------------------------------------------- validation */

/** Problems worth warning about before the user presses «اجرا». The server
 *  validates independently; this is only so the answer arrives instantly. */
export function lint(graph: Graph, specs: Map<string, NodeSpec>): string[] {
  const out: string[] = [];
  const outputs = graph.nodes.filter((n) => n.type === "output");
  if (!outputs.length) out.push("گراف نود «خروجی فیلتر» ندارد.");
  else if (outputs.length > 1) out.push("بیش از یک نود «خروجی فیلتر» روی بوم است.");

  const filled = new Set(graph.edges.map((e) => `${e.to}:${e.toPort}`));
  for (const n of graph.nodes) {
    const spec = specs.get(n.type);
    if (!spec) continue;
    for (const p of spec.inputs) {
      if (p.optional) continue;
      if (!filled.has(`${n.id}:${p.id}`)) {
        const port = p.label ? `«${p.label}»` : "";
        out.push(`ورودی ${port} در «${spec.label}» وصل نشده است.`);
      }
    }
  }
  return out.slice(0, 6);
}

/** Is a wire from this output to that input allowed? */
export function canConnect(
  fromSpec: NodeSpec,
  fromPort: string,
  toSpec: NodeSpec,
  toPort: string,
  sameNode: boolean,
): boolean {
  if (sameNode) return false;
  const src = fromSpec.outputs.find((p) => p.id === fromPort);
  const dst = toSpec.inputs.find((p) => p.id === toPort);
  if (!src || !dst) return false;
  // `text` is a closed world — only «تطبیق متن» consumes it, and feeding a name
  // into a `>` would silently compare nothing at all.
  if (src.kind === "text" || dst.kind === "text") return src.kind === dst.kind;
  return true;
}
