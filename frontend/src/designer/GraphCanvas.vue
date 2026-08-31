<script setup lang="ts">
/**
 * GraphCanvas.vue — the pan/zoom board the filter is drawn on.
 *
 * One transformed layer holds everything: an SVG for the wires and absolutely
 * positioned chips on top of it, both inside the same
 * `translate(px,py) scale(z)`. Keeping the wires in the SAME transform as the
 * chips is what makes a port dot and the wire that leaves it stay welded
 * together at every zoom level; drawing the SVG in screen space and converting
 * coordinates per frame is the usual way this is built and it is the usual
 * reason node editors shimmer while you drag.
 *
 * The board is dir="ltr" inside an RTL page on purpose. The graph flows sources
 * → output, left → right, exactly as it does in the reference product, and
 * mirroring it would put the output on the left while the arrowheads still
 * pointed right.
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import NodeChip from "./NodeChip.vue";
import {
  GRID,
  chipTitle,
  canConnect,
  inAnchor,
  nodeSize,
  outAnchor,
  valueAt,
  wirePath,
  type Anchor,
  type Category,
  type Explain,
  type GEdge,
  type GNode,
  type Graph,
  type NodeSpec,
} from "./graph";

const props = defineProps<{
  graph: Graph;
  specs: Map<string, NodeSpec>;
  colors: Map<string, string>;
  categories: Category[];
  selected: string[];
  explain: Explain | null;
  /** Draw only. The results page shows the graph the rows came from, and a
   *  diagram you can accidentally rewire while reading it is a trap — the
   *  edits would go nowhere, because that page has no canvas to save. */
  readonly?: boolean;
}>();

const emit = defineEmits<{
  (e: "select", ids: string[]): void;
  (e: "change"): void;
  (e: "drop-node", payload: { type: string; x: number; y: number }): void;
  (e: "inspect", id: string): void;
}>();

/* ------------------------------------------------------------ view state */
const board = ref<HTMLElement | null>(null);
const zoom = ref(1);
const pan = ref({ x: 40, y: 20 });

/** Chip geometry, recomputed only when a caption or the catalogue changes. */
const sizes = computed(() => {
  const m = new Map<string, { w: number; h: number }>();
  for (const n of props.graph.nodes) {
    const spec = props.specs.get(n.type);
    if (spec) m.set(n.id, nodeSize(spec, chipTitle(spec, n)));
  }
  return m;
});

const nodeById = computed(() => new Map(props.graph.nodes.map((n) => [n.id, n])));

function anchors(e: GEdge): { a: Anchor; b: Anchor } | null {
  const from = nodeById.value.get(e.from);
  const to = nodeById.value.get(e.to);
  const fs = from && props.specs.get(from.type);
  const ts = to && props.specs.get(to.type);
  const fz = from && sizes.value.get(from.id);
  const tz = to && sizes.value.get(to.id);
  if (!from || !to || !fs || !ts || !fz || !tz) return null;
  return {
    a: outAnchor(from, fs, fz, e.fromPort),
    b: inAnchor(to, ts, tz, e.toPort),
  };
}

/**
 * The <svg>'s own box, in graph coordinates.
 *
 * It used to be 1×1 with `overflow: visible`, which paints the wires correctly
 * everywhere and hit-tests them NOWHERE: a pointer event only reaches an SVG
 * through the element's own border box, so "click a wire to delete it" worked
 * in the top-left corner of the board and silently did nothing anywhere else.
 * Sizing the element to the content — with the viewBox set to the same rect, so
 * the paths keep using absolute graph coordinates — makes the whole graph
 * clickable.
 */
const frame = computed(() => {
  const pad = 400;
  let x0 = 0;
  let y0 = 0;
  let x1 = 600;
  let y1 = 400;
  for (const n of props.graph.nodes) {
    const s = sizes.value.get(n.id) ?? { w: 140, h: 30 };
    x0 = Math.min(x0, n.x);
    y0 = Math.min(y0, n.y);
    x1 = Math.max(x1, n.x + s.w);
    y1 = Math.max(y1, n.y + s.h);
  }
  // The padding also has to cover a wire being dragged out past the last chip,
  // which is otherwise painted outside the box and — with the box now sized —
  // clipped away mid-gesture.
  return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + pad * 2, h: y1 - y0 + pad * 2 };
});

const wires = computed(() =>
  props.graph.edges
    .map((e, i) => {
      const p = anchors(e);
      if (!p) return null;
      const key = `${e.from}:${e.fromPort}`;
      const val = props.explain?.ports?.[key];
      // A wire carrying a condition is tinted by that condition's value on the
      // last bar, so «چرا این نماد آمد؟» is answered by looking at the picture
      // rather than by reading a table of numbers.
      let tone = "";
      if (val && val.kind === "bool") {
        const v = valueAt(val.tail, props.explain?.at ?? 0);
        tone = v === true ? "on" : v === false ? "off" : "";
      }
      return { i, e, d: wirePath(p.a, p.b), tone };
    })
    .filter((w): w is { i: number; e: GEdge; d: string; tone: string } => w !== null),
);

/* --------------------------------------------------------- pan and zoom */
function clientToBoard(cx: number, cy: number) {
  const r = board.value!.getBoundingClientRect();
  return { x: (cx - r.left - pan.value.x) / zoom.value, y: (cy - r.top - pan.value.y) / zoom.value };
}

function onWheel(ev: WheelEvent) {
  // Ctrl+wheel is the browser's own page zoom and stays that way; a bare wheel
  // zooms the board, which is what every canvas tool does and what the mouse in
  // the user's hand is already trained for.
  ev.preventDefault();
  const before = clientToBoard(ev.clientX, ev.clientY);
  const next = Math.min(2.5, Math.max(0.25, zoom.value * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
  zoom.value = next;
  const r = board.value!.getBoundingClientRect();
  pan.value = {
    x: ev.clientX - r.left - before.x * next,
    y: ev.clientY - r.top - before.y * next,
  };
}

function zoomBy(f: number) {
  const r = board.value?.getBoundingClientRect();
  if (!r) return;
  const cx = r.width / 2;
  const cy = r.height / 2;
  const before = { x: (cx - pan.value.x) / zoom.value, y: (cy - pan.value.y) / zoom.value };
  zoom.value = Math.min(2.5, Math.max(0.25, zoom.value * f));
  pan.value = { x: cx - before.x * zoom.value, y: cy - before.y * zoom.value };
}

/** Frame the whole graph — the only sane response to opening someone else's
 *  filter, or to losing the chips off the edge of the board. */
function fit() {
  const r = board.value?.getBoundingClientRect();
  if (!r || !props.graph.nodes.length) return;
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const n of props.graph.nodes) {
    const s = sizes.value.get(n.id) ?? { w: 120, h: 30 };
    x0 = Math.min(x0, n.x - 10);
    y0 = Math.min(y0, n.y - 10);
    x1 = Math.max(x1, n.x + s.w + 10);
    y1 = Math.max(y1, n.y + s.h + 34); // + the caption under the chip
  }
  // Never zooms PAST natural size. A four-chip graph would otherwise open at
  // 140 %, where the captions are soft and the board looks like a zoomed
  // screenshot; the useful direction for fit is always outwards.
  const z = Math.min(1, (r.width - 40) / (x1 - x0), (r.height - 40) / (y1 - y0));
  zoom.value = Math.max(0.25, z);
  pan.value = {
    x: (r.width - (x1 - x0) * zoom.value) / 2 - x0 * zoom.value,
    y: (r.height - (y1 - y0) * zoom.value) / 2 - y0 * zoom.value,
  };
}
/**
 * Where a chip added by CLICKING the palette should land: the middle of what the
 * user is currently looking at.
 *
 * The first version put it to the right of everything already on the board,
 * which is fine until the board is panned — then the chip appears somewhere
 * off-screen and the click looks like it did nothing at all. Spawned in view it
 * is always the thing the user sees appear, and it arrives selected, so the
 * inspector is already showing its parameters.
 */
function viewCenter(): { x: number; y: number } {
  const r = board.value?.getBoundingClientRect();
  if (!r) return { x: 60, y: 60 };
  return {
    x: (r.width / 2 - pan.value.x) / zoom.value - 60,
    y: (r.height / 2 - pan.value.y) / zoom.value - 15,
  };
}

defineExpose({ fit, zoomBy, viewCenter });

/* ------------------------------------------------------------ dragging */
type Drag =
  | { kind: "pan"; sx: number; sy: number; px: number; py: number }
  | { kind: "node"; ids: string[]; sx: number; sy: number; start: Map<string, { x: number; y: number }> }
  | { kind: "wire"; from: string; fromPort: string; a: Anchor; cursor: Anchor }
  | { kind: "marquee"; x0: number; y0: number; x1: number; y1: number }
  | null;

const drag = ref<Drag>(null);
const hoverPort = ref<string>("");

function startPan(ev: PointerEvent) {
  if (ev.button === 2) return;
  const onBackground = (ev.target as HTMLElement).dataset.role === "board";
  // Read-only: every chip is inert, so the whole surface is the drag handle. A
  // dense graph otherwise leaves almost no background to grab.
  if (!onBackground && !props.readonly) return;
  ev.preventDefault();
  (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
  if (ev.shiftKey && !props.readonly) {
    const p = clientToBoard(ev.clientX, ev.clientY);
    drag.value = { kind: "marquee", x0: p.x, y0: p.y, x1: p.x, y1: p.y };
  } else {
    emit("select", []);
    drag.value = { kind: "pan", sx: ev.clientX, sy: ev.clientY, px: pan.value.x, py: pan.value.y };
  }
}

function startNodeDrag(ev: PointerEvent, node: GNode) {
  if (props.readonly) return;
  ev.stopPropagation();
  const already = props.selected.includes(node.id);
  const ids = ev.shiftKey
    ? already
      ? props.selected.filter((i) => i !== node.id)
      : [...props.selected, node.id]
    : already
      ? props.selected
      : [node.id];
  emit("select", ids);
  const start = new Map<string, { x: number; y: number }>();
  for (const id of ids) {
    const n = nodeById.value.get(id);
    if (n) start.set(id, { x: n.x, y: n.y });
  }
  (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
  drag.value = { kind: "node", ids, sx: ev.clientX, sy: ev.clientY, start };
}

function startWire(ev: PointerEvent, node: GNode, port: string) {
  if (props.readonly) return;
  ev.stopPropagation();
  ev.preventDefault();
  const spec = props.specs.get(node.type);
  const size = sizes.value.get(node.id);
  if (!spec || !size) return;
  (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
  const a = outAnchor(node, spec, size, port);
  drag.value = { kind: "wire", from: node.id, fromPort: port, a, cursor: a };
}

/**
 * Pulling on an INPUT that already has a wire detaches it and puts the loose end
 * on the cursor, instead of doing nothing. Re-routing a wire is the single most
 * common edit after the first draft, and the alternative is delete-then-redraw.
 */
function grabInput(ev: PointerEvent, node: GNode, port: string) {
  if (props.readonly) return;
  const idx = props.graph.edges.findIndex((e) => e.to === node.id && e.toPort === port);
  if (idx < 0) return;
  ev.stopPropagation();
  ev.preventDefault();
  const edge = props.graph.edges[idx];
  const from = nodeById.value.get(edge.from);
  const fs = from && props.specs.get(from.type);
  const fz = from && sizes.value.get(from.id);
  if (!from || !fs || !fz) return;
  props.graph.edges.splice(idx, 1);
  emit("change");
  (ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
  const a = outAnchor(from, fs, fz, edge.fromPort);
  const p = clientToBoard(ev.clientX, ev.clientY);
  drag.value = { kind: "wire", from: edge.from, fromPort: edge.fromPort, a, cursor: p };
}

function onMove(ev: PointerEvent) {
  const d = drag.value;
  if (!d) return;
  if (d.kind === "pan") {
    pan.value = { x: d.px + (ev.clientX - d.sx), y: d.py + (ev.clientY - d.sy) };
  } else if (d.kind === "node") {
    const dx = (ev.clientX - d.sx) / zoom.value;
    const dy = (ev.clientY - d.sy) / zoom.value;
    for (const id of d.ids) {
      const n = nodeById.value.get(id);
      const s = d.start.get(id);
      if (!n || !s) continue;
      // Snapped to the grid, and only while dragging: a chip nudged with the
      // arrow keys keeps its exact position.
      n.x = Math.round((s.x + dx) / GRID) * GRID;
      n.y = Math.round((s.y + dy) / GRID) * GRID;
    }
  } else if (d.kind === "wire") {
    d.cursor = clientToBoard(ev.clientX, ev.clientY);
    drag.value = { ...d };
  } else if (d.kind === "marquee") {
    const p = clientToBoard(ev.clientX, ev.clientY);
    drag.value = { ...d, x1: p.x, y1: p.y };
  }
}

function onUp(ev: PointerEvent) {
  const d = drag.value;
  drag.value = null;
  hoverPort.value = "";
  if (!d) return;
  if (d.kind === "node") {
    emit("change");
  } else if (d.kind === "marquee") {
    const x0 = Math.min(d.x0, d.x1);
    const x1 = Math.max(d.x0, d.x1);
    const y0 = Math.min(d.y0, d.y1);
    const y1 = Math.max(d.y0, d.y1);
    const hit = props.graph.nodes
      .filter((n) => {
        const s = sizes.value.get(n.id) ?? { w: 0, h: 0 };
        return n.x + s.w >= x0 && n.x <= x1 && n.y + s.h >= y0 && n.y <= y1;
      })
      .map((n) => n.id);
    emit("select", hit);
  } else if (d.kind === "wire") {
    // The drop target is whatever input port is under the cursor. Read from the
    // DOM rather than from geometry: the port dot has a generous hit area and
    // elementFromPoint honours it exactly, so "close enough" means the same
    // thing to the code as it does to the eye.
    const el = document.elementFromPoint(ev.clientX, ev.clientY) as HTMLElement | null;
    const slot = el?.closest<HTMLElement>("[data-in-port]");
    if (slot) connect(d.from, d.fromPort, slot.dataset.node!, slot.dataset.inPort!);
  }
}

function connect(from: string, fromPort: string, to: string, toPort: string) {
  const fromNode = nodeById.value.get(from);
  const toNode = nodeById.value.get(to);
  const fs = fromNode && props.specs.get(fromNode.type);
  const ts = toNode && props.specs.get(toNode.type);
  if (!fs || !ts || !canConnect(fs, fromPort, ts, toPort, from === to)) return;
  const port = ts.inputs.find((p) => p.id === toPort)!;
  const edges = props.graph.edges;
  if (edges.some((e) => e.from === from && e.fromPort === fromPort && e.to === to && e.toPort === toPort)) {
    return;
  }
  if (!port.multi) {
    const i = edges.findIndex((e) => e.to === to && e.toPort === toPort);
    if (i >= 0) edges.splice(i, 1); // a single input holds one wire — replace it
  }
  edges.push({ from, fromPort, to, toPort });
  emit("change");
}

/**
 * The belt to onUp()'s braces. The output port takes a pointer capture when the
 * drag starts, so in most browsers the input port never sees `pointerup` at all
 * and onUp's elementFromPoint hit-test is what actually lands the wire — but a
 * browser that releases capture early (or a stylus that re-targets) delivers it
 * here instead, and dropping the wire on the floor in that case would look like
 * the editor randomly refusing connections.
 */
function dropWire(node: GNode, port: string) {
  const d = drag.value;
  if (d?.kind === "wire") connect(d.from, d.fromPort, node.id, port);
}

const liveWire = computed(() => {
  const d = drag.value;
  return d?.kind === "wire" ? wirePath(d.a, d.cursor) : "";
});

const marquee = computed(() => {
  const d = drag.value;
  if (d?.kind !== "marquee") return null;
  return {
    x: Math.min(d.x0, d.x1),
    y: Math.min(d.y0, d.y1),
    w: Math.abs(d.x1 - d.x0),
    h: Math.abs(d.y1 - d.y0),
  };
});

/* --------------------------------------------------------- drop from palette */
function onDragOver(ev: DragEvent) {
  if (props.readonly) return;
  if (ev.dataTransfer?.types.includes("text/bn-node")) {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = "copy";
  }
}

function onDrop(ev: DragEvent) {
  const type = ev.dataTransfer?.getData("text/bn-node");
  if (!type) return;
  ev.preventDefault();
  const p = clientToBoard(ev.clientX, ev.clientY);
  emit("drop-node", { type, x: Math.round(p.x / GRID) * GRID, y: Math.round(p.y / GRID) * GRID });
}

/* -------------------------------------------------------------- edge trim */
const hotEdge = ref(-1);

function cutEdge(i: number) {
  if (props.readonly) return;
  props.graph.edges.splice(i, 1);
  hotEdge.value = -1;
  emit("change");
}

/* ------------------------------------------------------------- keyboard */
function onKey(ev: KeyboardEvent) {
  if (props.readonly) return;
  const t = ev.target as HTMLElement;
  if (t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return;
  if (!props.selected.length) return;
  const step = ev.shiftKey ? GRID * 5 : GRID;
  const move = { ArrowUp: [0, -step], ArrowDown: [0, step], ArrowLeft: [-step, 0], ArrowRight: [step, 0] }[
    ev.key
  ];
  if (move) {
    ev.preventDefault();
    for (const id of props.selected) {
      const n = nodeById.value.get(id);
      if (n) {
        n.x += move[0];
        n.y += move[1];
      }
    }
    emit("change");
  }
}

onMounted(() => {
  window.addEventListener("keydown", onKey);
  requestAnimationFrame(fit);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));

</script>

<template>
  <div
    ref="board"
    class="dz-board"
    :class="{ 'is-readonly': readonly }"
    dir="ltr"
    data-role="board"
    @pointerdown="startPan"
    @pointermove="onMove"
    @pointerup="onUp"
    @pointercancel="onUp"
    @wheel="onWheel"
    @dragover="onDragOver"
    @drop="onDrop"
    @contextmenu.prevent
  >
    <div class="dz-layer" :style="{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }">
      <svg
        class="dz-wires"
        aria-hidden="true"
        :style="{ left: `${frame.x}px`, top: `${frame.y}px`, width: `${frame.w}px`, height: `${frame.h}px` }"
        :viewBox="`${frame.x} ${frame.y} ${frame.w} ${frame.h}`"
      >
        <g>
          <path
            v-for="w in wires"
            :key="`${w.e.from}-${w.e.fromPort}-${w.e.to}-${w.e.toPort}`"
            :d="w.d"
            class="dz-wire"
            :class="[w.tone && `is-${w.tone}`, hotEdge === w.i && 'is-hot']"
          />
          <!-- A second, invisible, fat copy of every wire: a 2 px line is
               impossible to hit with a mouse, and widening the visible one to
               make it clickable would turn the graph into a cable tray. -->
          <path
            v-for="w in wires"
            v-show="!readonly"
            :key="`hit-${w.i}`"
            :d="w.d"
            class="dz-wire-hit"
            @pointerenter="hotEdge = w.i"
            @pointerleave="hotEdge = hotEdge === w.i ? -1 : hotEdge"
            @click.stop="cutEdge(w.i)"
          >
            <title>برای حذف این اتصال کلیک کنید</title>
          </path>
          <path v-if="liveWire" :d="liveWire" class="dz-wire is-live" />
          <rect
            v-if="marquee"
            :x="marquee.x"
            :y="marquee.y"
            :width="marquee.w"
            :height="marquee.h"
            class="dz-marquee"
          />
        </g>
      </svg>

      <NodeChip
        v-for="n in graph.nodes"
        :key="n.id"
        :node="n"
        :spec="specs.get(n.type)!"
        :size="sizes.get(n.id)!"
        :color="colors.get(specs.get(n.type)?.cat ?? '') ?? '#ddd'"
        :selected="selected.includes(n.id)"
        :explain="explain"
        :wiring="drag?.kind === 'wire'"
        :readonly="readonly"
        @grab="startNodeDrag($event, n)"
        @out-down="startWire($event.ev, n, $event.port)"
        @in-down="grabInput($event.ev, n, $event.port)"
        @in-up="dropWire(n, $event.port)"
        @open="emit('inspect', n.id)"
      />
    </div>

    <div class="dz-zoom" dir="rtl">
      <button type="button" class="icon-btn" title="بزرگ‌نمایی" @click="zoomBy(1.2)">+</button>
      <button type="button" class="icon-btn" title="اندازهٔ مناسب" @click="fit()">⤢</button>
      <button type="button" class="icon-btn" title="کوچک‌نمایی" @click="zoomBy(1 / 1.2)">−</button>
      <span class="dz-zoom-pct">{{ Math.round(zoom * 100) }}٪</span>
    </div>

    <p v-if="!graph.nodes.length && !readonly" class="dz-blank" dir="rtl">
      بومْ خالی است. از پالت سمت راست یک جعبه بکشید و اینجا رها کنید، یا از «نمونه‌ها»
      یکی از فیلترهای آماده را باز کنید.
    </p>
  </div>
</template>
