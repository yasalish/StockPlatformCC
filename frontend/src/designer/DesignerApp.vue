<script setup lang="ts">
/**
 * DesignerApp.vue — «طراحی فیلتر».
 *
 * Owns the graph and every action on it: the toolbar, undo, the scope bar
 * (سهام/صندوق + گروه), saving, the examples, and sending the graph off to be
 * run. The canvas draws; this decides.
 *
 * «اجرا» NAVIGATES — to /filter-designer/result, which does the running and
 * shows the table. The results used to appear in a panel below the canvas,
 * where the list that is the entire point of the exercise got the bottom third
 * of the screen while the editor kept the rest. The graph travels through the
 * same localStorage draft that already survives a reload (draft.ts), so what
 * runs is exactly what is on the canvas, and coming back restores it.
 *
 * The graph is a plain reactive object rather than a store, and it is what is
 * POSTed: no separate serialisation step, so there is no way for what runs to
 * drift from what is drawn. Undo keeps whole snapshots for the same reason —
 * a command log over a node editor is where the bugs live, and a filter graph is
 * a few kilobytes, so fifty snapshots cost less than the code to avoid them.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import GraphCanvas from "./GraphCanvas.vue";
import PalettePanel from "./PalettePanel.vue";
import InspectorPanel from "./InspectorPanel.vue";
import { backtestUrl, loadDraft, resultUrl, saveDraft } from "./draft";
import {
  autoLayout,
  lint,
  newId,
  rekey,
  type Catalog,
  type Graph,
  type GNode,
} from "./graph";
import { fa } from "../format";
import { postJson, userMessage } from "../http";

const props = defineProps<{
  catalog: Catalog;
  detailBaseStock: string;
  detailBaseEtf: string;
}>();

/* -------------------------------------------------------------- catalogue */
const specs = computed(() => new Map(props.catalog.nodes.map((n) => [n.type, n])));
const colors = computed(() => new Map(props.catalog.categories.map((c) => [c.key, c.color])));

/* ------------------------------------------------------------------ state */
const graph = ref<Graph>({ nodes: [], edges: [] });
const selected = ref<string[]>([]);
const kind = ref<"stock" | "etf">(props.catalog.kind);
const group = ref(props.catalog.group ?? "");
const subgroup = ref(props.catalog.subgroup ?? "");
const groups = ref<string[]>(props.catalog.groups);
const subgroups = ref<string[]>(props.catalog.subgroups);

const busy = ref(false);
const error = ref("");
const notice = ref("");

/** `alert` is set for a filter carrying an «هشدار» block — it runs itself after
 *  every market update. A dropdown of names gives no way to tell those apart,
 *  and a filter that notifies you is exactly the one you want to find again. */
const saved = ref<
  { id: number; name: string; kind: string; updated_at: string; alert?: boolean }[]
>([]);
const currentId = ref<number | null>(null);
const currentName = ref("");
const canvas = ref<InstanceType<typeof GraphCanvas> | null>(null);

let spawnTick = 0;
const problems = computed(() => lint(graph.value, specs.value));
const soloNode = computed<GNode | null>(() =>
  selected.value.length === 1
    ? (graph.value.nodes.find((n) => n.id === selected.value[0]) ?? null)
    : null,
);
const soloSpec = computed(() => (soloNode.value ? (specs.value.get(soloNode.value.type) ?? null) : null));

/* ------------------------------------------------------------------- undo */
const past: string[] = [];
const future: string[] = [];
let lastSnapshot = "";

function snapshot() {
  const s = JSON.stringify(graph.value);
  if (s === lastSnapshot) return;
  past.push(lastSnapshot || s);
  if (past.length > 50) past.shift();
  future.length = 0;
  lastSnapshot = s;
  persist();
}

/**
 * Write the draft.
 *
 * It does two jobs at once, and both matter: unsaved work survives a reload
 * (a node graph is twenty minutes of thinking), and it is the channel «اجرا»
 * hands the graph to the results page through. Because the same record serves
 * both, there is no second serialisation that could disagree with the first —
 * what runs is what the canvas last wrote.
 */
function persist() {
  saveDraft({
    graph: graph.value,
    kind: kind.value,
    name: currentName.value,
    id: currentId.value,
    group: group.value,
    subgroup: subgroup.value,
  });
}

function apply(next: Graph) {
  graph.value = next;
  // Framing is triggered HERE, not by a watcher inside the canvas: this is the
  // only moment the whole graph is replaced, and a watcher broad enough to catch
  // it reliably is also broad enough to re-frame the board mid-drag.
  requestAnimationFrame(() => canvas.value?.fit());
  selected.value = [];
  lastSnapshot = JSON.stringify(next);
  persist();
}

function undo() {
  const prev = past.pop();
  if (prev === undefined) return;
  future.push(JSON.stringify(graph.value));
  graph.value = JSON.parse(prev) as Graph;
  lastSnapshot = prev;
  selected.value = [];
}

function redo() {
  const next = future.pop();
  if (next === undefined) return;
  past.push(JSON.stringify(graph.value));
  graph.value = JSON.parse(next) as Graph;
  lastSnapshot = next;
  selected.value = [];
}

/* ------------------------------------------------------------------ edits */
function defaults(type: string) {
  const spec = specs.value.get(type)!;
  const p: Record<string, number | string> = {};
  for (const q of spec.params) p[q.id] = q.default;
  return p;
}

function addNode(type: string, at?: { x: number; y: number }) {
  if (graph.value.nodes.length >= props.catalog.limits.nodes) {
    error.value = `بیشترین تعداد جعبه ${fa(props.catalog.limits.nodes)} است.`;
    return;
  }
  // Dropped where the cursor is; clicked from the palette, in the middle of the
  // current view (GraphCanvas.viewCenter) with a small cascade so a run of
  // clicks does not stack every chip on one pixel.
  const centre = canvas.value?.viewCenter?.() ?? { x: 60, y: 60 };
  const spot = at ?? { x: centre.x + (spawnTick % 5) * 26, y: centre.y + (spawnTick % 5) * 34 - 60 };
  if (!at) spawnTick += 1;
  const node: GNode = { id: newId(), type, x: spot.x, y: spot.y, params: defaults(type) };
  graph.value.nodes.push(node);
  selected.value = [node.id];
  snapshot();
}

function removeSelected() {
  if (!selected.value.length) return;
  const gone = new Set(selected.value);
  graph.value.nodes = graph.value.nodes.filter((n) => !gone.has(n.id));
  graph.value.edges = graph.value.edges.filter((e) => !gone.has(e.from) && !gone.has(e.to));
  selected.value = [];
  snapshot();
}

function duplicateSelected() {
  if (!selected.value.length) return;
  const keep = new Set(selected.value);
  const sub: Graph = {
    nodes: graph.value.nodes.filter((n) => keep.has(n.id)).map((n) => ({ ...n, x: n.x + 30, y: n.y + 30 })),
    edges: graph.value.edges.filter((e) => keep.has(e.from) && keep.has(e.to)),
  };
  const copy = rekey(sub);
  graph.value.nodes.push(...copy.nodes);
  graph.value.edges.push(...copy.edges);
  selected.value = copy.nodes.map((n) => n.id);
  snapshot();
}

function clearAll() {
  if (graph.value.nodes.length && !window.confirm("همهٔ جعبه‌های روی بوم پاک شوند؟")) return;
  currentId.value = null;
  currentName.value = "";
  apply({ nodes: [], edges: [] });
}

function tidy() {
  autoLayout(graph.value, specs.value);
  snapshot();
  requestAnimationFrame(() => canvas.value?.fit());
}

function loadExample(key: string) {
  const ex = props.catalog.examples.find((e) => e.key === key);
  if (!ex) return;
  currentId.value = null;
  currentName.value = ex.name;
  apply(rekey(JSON.parse(JSON.stringify(ex.graph)) as Graph));
  notice.value = ex.desc;
}

/* -------------------------------------------------------------------- run */
/* Delegates to http.ts so these POSTs get the bounded wait the review's M-3
   asked for. The thrown message is preserved exactly: userMessage() returns the
   server's own `error` field when there is one, which is what this function
   used to dig out by hand, and falls back to a described failure — "the reply
   took too long", "no connection" — where it previously produced the unhelpful
   `خطای سرور (undefined)` for a request that never arrived at all. */
async function post<T>(url: string, body: unknown): Promise<T> {
  try {
    return await postJson<T>(url, body);
  } catch (e) {
    throw new Error(userMessage(e));
  }
}

/**
 * Hand the graph to the results page.
 *
 * The lint runs first and BLOCKS on a structural problem, which it did not have
 * to when the answer appeared in a panel three inches below the button: a
 * navigation that lands on «گراف نود خروجی فیلتر ندارد» has thrown away the
 * user's place for a mistake that was visible before they left.
 */
function run() {
  error.value = "";
  notice.value = "";
  if (!graph.value.nodes.length) {
    error.value = "بومْ خالی است — اول فیلتر را بسازید.";
    return;
  }
  const blocking = problems.value;
  if (blocking.length) {
    error.value = blocking.join(" · ");
    return;
  }
  busy.value = true;                       // the button, for the moment before unload
  persist();
  window.location.href = resultUrl({
    id: currentId.value,
    kind: kind.value,
    group: group.value,
    subgroup: subgroup.value,
  });
}

/** «بک‌تست» — the same handoff as «اجرا», to the history page instead.
 *
 * It runs the same lint first. A graph with an unconnected input produces no
 * signals at all, and "۰ سیگنال" after a forty-second backtest is a far worse
 * way to learn that than the message the canvas can show instantly. */
function openBacktest() {
  error.value = "";
  notice.value = "";
  if (!graph.value.nodes.length) {
    error.value = "بومْ خالی است — اول فیلتر را بسازید.";
    return;
  }
  const blocking = problems.value;
  if (blocking.length) {
    error.value = blocking.join(" · ");
    return;
  }
  busy.value = true;
  persist();
  window.location.href = backtestUrl({
    id: currentId.value,
    kind: kind.value,
    group: group.value,
    subgroup: subgroup.value,
  });
}

/* ------------------------------------------------------------------ saved */
async function refreshSaved() {
  if (!props.catalog.authenticated) return;
  try {
    const res = await fetch("/api/designer/filters", { headers: { Accept: "application/json" } });
    if (res.ok) saved.value = (await res.json()).filters ?? [];
  } catch {
    /* the picker is optional chrome; a failure here must not break the canvas */
  }
}

async function save() {
  const name = window.prompt("نام فیلتر:", currentName.value || "فیلتر من");
  if (name === null) return;
  error.value = "";
  try {
    const out = await post<{ id: number; filters: typeof saved.value }>("/api/designer/filters", {
      graph: graph.value,
      kind: kind.value,
      name,
      // Saving under a NEW name makes a new filter; keeping the name updates the
      // one that has it. That is what the server's ON CONFLICT already does, so
      // the id is only sent when the name is unchanged.
      id: name === currentName.value ? currentId.value : null,
    });
    currentId.value = out.id;
    currentName.value = name;
    saved.value = out.filters;
    notice.value = `«${name}» ذخیره شد.`;
  } catch (e) {
    error.value = (e as Error).message;
  }
}

async function openSaved(id: string) {
  if (!id) return;
  error.value = "";
  try {
    const res = await fetch(`/api/designer/filters/${id}`, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error("این فیلتر پیدا نشد.");
    const row = await res.json();
    currentId.value = row.id;
    currentName.value = row.name;
    kind.value = row.kind === "etf" ? "etf" : "stock";
    apply(typeof row.graph === "string" ? JSON.parse(row.graph) : row.graph);
    notice.value = `«${row.name}» باز شد.`;
  } catch (e) {
    error.value = (e as Error).message;
  }
}

async function removeSaved() {
  if (!currentId.value) return;
  if (!window.confirm(`فیلتر «${currentName.value}» حذف شود؟`)) return;
  const res = await fetch(`/api/designer/filters/${currentId.value}`, { method: "DELETE" });
  if (res.ok) {
    saved.value = (await res.json()).filters ?? [];
    currentId.value = null;
    currentName.value = "";
    notice.value = "فیلتر حذف شد.";
  }
}

/* ------------------------------------------------------- import / export */
function exportJson() {
  const blob = new Blob([JSON.stringify({ name: currentName.value, kind: kind.value, graph: graph.value }, null, 2)], {
    type: "application/json",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${currentName.value || "filter"}.json`;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function importJson(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const parsed = JSON.parse(String(reader.result));
      const g = (parsed.graph ?? parsed) as Graph;
      if (!Array.isArray(g.nodes)) throw new Error("bad");
      currentId.value = null;
      currentName.value = parsed.name ?? "";
      if (parsed.kind === "etf" || parsed.kind === "stock") kind.value = parsed.kind;
      apply(rekey(g));
      notice.value = "فیلتر از فایل بارگذاری شد.";
    } catch {
      error.value = "این فایل یک فیلتر معتبر نیست.";
    }
  };
  reader.readAsText(file);
  (ev.target as HTMLInputElement).value = "";
}

/* ------------------------------------------------------------------ scope */
watch(kind, async (k) => {
  // The group lists belong to the kind. Refetching the catalogue is one small
  // request and keeps the lists exactly what the server would validate against.
  group.value = "";
  subgroup.value = "";
  persist();
  try {
    const res = await fetch(`/api/designer/catalog?kind=${k}`, { headers: { Accept: "application/json" } });
    if (res.ok) {
      const c = await res.json();
      groups.value = c.groups;
      subgroups.value = c.subgroups;
    }
  } catch { /* keep the old lists rather than emptying the dropdown */ }
});

watch(group, async (g) => {
  subgroup.value = "";
  persist();
  if (kind.value !== "stock") return;
  try {
    const res = await fetch(`/api/designer/catalog?kind=stock&group=${encodeURIComponent(g)}`, {
      headers: { Accept: "application/json" },
    });
    if (res.ok) subgroups.value = (await res.json()).subgroups;
  } catch { /* ignore */ }
});

watch(subgroup, persist);

/* --------------------------------------------------------------- keyboard */
function onKey(ev: KeyboardEvent) {
  const t = ev.target as HTMLElement;
  const typing = t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName);
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
    ev.preventDefault();
    void run();
    return;
  }
  if (typing) return;
  if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z") {
    ev.preventDefault();
    ev.shiftKey ? redo() : undo();
  } else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "y") {
    ev.preventDefault();
    redo();
  } else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "d") {
    ev.preventDefault();
    duplicateSelected();
  } else if (ev.key === "Delete" || ev.key === "Backspace") {
    ev.preventDefault();
    removeSelected();
  }
}

onMounted(async () => {
  window.addEventListener("keydown", onKey);
  void refreshSaved();

  // «بازگشت به طراحی» from a saved filter's results page comes back here as
  // ?filter=<id>, and so does any link to one. It wins over the draft.
  const wanted = Number(new URLSearchParams(window.location.search).get("filter") || 0);
  if (wanted > 0) {
    await openSaved(String(wanted));
    if (graph.value.nodes.length) return;
  }

  const draft = loadDraft();
  if (draft && draft.graph.nodes.length) {
    kind.value = draft.kind;
    currentName.value = draft.name;
    currentId.value = draft.id;
    group.value = draft.group;
    subgroup.value = draft.subgroup;
    apply(draft.graph);
    return;
  }
  // A first visit opens on a working filter, not on an empty board: this page
  // teaches itself far better from one wired example than from any tour.
  loadExample(props.catalog.examples[0]?.key ?? "");
  notice.value = "";
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div class="dz">
    <!-- ─────────────────────────────────────────────── toolbar -->
    <div class="dz-bar">
      <div class="dz-bar-g">
        <button type="button" class="btn btn-primary" :disabled="busy" @click="run">
          <span v-if="busy">در حال باز کردن نتیجه…</span><span v-else>▶ اجرا</span>
        </button>
        <span class="dz-kbd muted small">Ctrl+Enter</span>
        <button type="button" class="btn" :disabled="busy || !graph.nodes.length"
                title="همین فیلتر را روی تاریخچه اجرا کن" @click="openBacktest">
          ⏱ بک‌تست
        </button>
      </div>

      <div class="dz-bar-g">
        <label class="dz-sel">
          <span>بازار</span>
          <select v-model="kind">
            <option value="stock">سهام</option>
            <option value="etf">صندوق‌ها</option>
          </select>
        </label>
        <label class="dz-sel">
          <span>{{ catalog.group_label }}</span>
          <select v-model="group">
            <option value="">همه</option>
            <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
          </select>
        </label>
        <label v-if="kind === 'stock' && subgroups.length" class="dz-sel">
          <span>زیرگروه</span>
          <select v-model="subgroup">
            <option value="">همه</option>
            <option v-for="g in subgroups" :key="g" :value="g">{{ g }}</option>
          </select>
        </label>
      </div>

      <div class="dz-bar-g">
        <label class="dz-sel">
          <span>نمونه‌ها</span>
          <select :value="''" @change="loadExample(($event.target as HTMLSelectElement).value)">
            <option value="">انتخاب…</option>
            <option v-for="e in catalog.examples" :key="e.key" :value="e.key">{{ e.name }}</option>
          </select>
        </label>
        <label v-if="catalog.authenticated" class="dz-sel">
          <span>فیلترهای من</span>
          <select :value="currentId ?? ''" @change="openSaved(($event.target as HTMLSelectElement).value)">
            <option value="">انتخاب…</option>
            <option v-for="f in saved" :key="f.id" :value="f.id">
              {{ f.alert ? `🔔 ${f.name}` : f.name }}
            </option>
          </select>
        </label>
      </div>

      <div class="dz-bar-g dz-bar-end">
        <button type="button" class="btn btn-sm" title="واگرد (Ctrl+Z)" @click="undo">↶</button>
        <button type="button" class="btn btn-sm" title="ازنو (Ctrl+Shift+Z)" @click="redo">↷</button>
        <button type="button" class="btn btn-sm" title="چیدمان خودکار" @click="tidy">مرتب‌سازی</button>
        <button v-if="catalog.authenticated" type="button" class="btn btn-sm" @click="save">ذخیره</button>
        <button
          v-if="catalog.authenticated && currentId"
          type="button"
          class="btn btn-sm btn-danger"
          @click="removeSaved"
        >
          حذف
        </button>
        <button type="button" class="btn btn-sm" title="خروجی JSON" @click="exportJson">برون‌بری</button>
        <label class="btn btn-sm dz-file" title="بارگذاری از فایل JSON">
          درون‌بری
          <input type="file" accept="application/json,.json" hidden @change="importJson" />
        </label>
        <button type="button" class="btn btn-sm btn-danger" @click="clearAll">پاک‌کردن بوم</button>
      </div>
    </div>

    <div v-if="currentName" class="dz-name muted small">
      فیلتر باز: <b>{{ currentName }}</b>
      <template v-if="!catalog.authenticated"> · برای ذخیرهٔ دائمی وارد حساب شوید.</template>
    </div>

    <p v-if="error" class="flash error dz-msg">{{ error }}</p>
    <p v-else-if="notice" class="flash info dz-msg">{{ notice }}</p>
    <p v-else-if="problems.length" class="flash warn dz-msg">
      {{ problems.join(" · ") }}
    </p>

    <!-- ─────────────────────────────────────────────── canvas -->
    <div class="dz-stage">
      <PalettePanel :nodes="catalog.nodes" :categories="catalog.categories" @add="addNode($event)" />
      <GraphCanvas
        ref="canvas"
        :graph="graph"
        :specs="specs"
        :colors="colors"
        :categories="catalog.categories"
        :selected="selected"
        :explain="null"
        @select="selected = $event"
        @change="snapshot"
        @drop-node="addNode($event.type, { x: $event.x, y: $event.y })"
        @inspect="selected = [$event]"
      />
      <InspectorPanel
        :node="soloNode"
        :spec="soloSpec"
        :explain="null"
        :count="selected.length"
        @change="snapshot"
        @delete="removeSelected"
        @duplicate="duplicateSelected"
      />
    </div>
  </div>
</template>
