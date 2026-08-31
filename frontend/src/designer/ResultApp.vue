<script setup lang="ts">
/**
 * ResultApp.vue — /filter-designer/result, the page «اجرا» lands on.
 *
 * The results used to sit in a panel under the canvas, where the table that is
 * the whole point of running a filter got the bottom third of the screen and
 * the editor kept the rest. They have their own page now: full width, full
 * height, sortable, exportable.
 *
 * It still carries the graph, read-only, in a panel above the table — for two
 * reasons that are not decoration. It says WHICH filter produced these rows,
 * which a bare table cannot; and it is where «چرا؟» draws its answer, painting
 * ✓/✕ on the chips of the symbol you asked about. The panel starts collapsed so
 * the table opens first, and opens itself the moment a «چرا؟» is asked.
 *
 * Where the graph comes from:
 *   ?filter=<id>  a saved filter, read from the database — bookmarkable, and
 *                 the URL worth sending to someone else;
 *   otherwise     the draft the canvas left in localStorage (see draft.ts).
 *
 * And on the right, «فیلترهای دیگر»: every saved filter and every ready-made
 * example, one click each. Running a screener is not one question — it is
 * "what does THIS one say, and that one, and the other one" — and making that
 * a round trip through the canvas turned a ten-second comparison into three
 * page loads and a dropdown hunt. Picking one here swaps the graph, re-runs it
 * in place, rewrites the URL and updates the draft, so «بازگشت به طراحی» opens
 * the filter you just looked at rather than the one you arrived with.
 */
import { computed, nextTick, onMounted, ref } from "vue";
import GraphCanvas from "./GraphCanvas.vue";
import ResultPanel from "./ResultPanel.vue";
import { backtestUrl, loadDraft, saveDraft, designerUrl, resultUrl, type Draft } from "./draft";
import {
  chipTitle,
  valueAt,
  type Catalog,
  type Explain,
  type Graph,
  type NodeSpec,
  type RunResult,
} from "./graph";
import { fa } from "../format";

const props = defineProps<{
  catalog: Catalog;
  detailBaseStock: string;
  detailBaseEtf: string;
  filterId: number | null;
}>();

const specs = computed(() => new Map(props.catalog.nodes.map((n) => [n.type, n])));
const colors = computed(() => new Map(props.catalog.categories.map((c) => [c.key, c.color])));

/* ------------------------------------------------------------------ state */
const graph = ref<Graph>({ nodes: [], edges: [] });
const name = ref("");
const savedId = ref<number | null>(props.filterId);
const kind = ref<"stock" | "etf">(props.catalog.kind);
const group = ref(props.catalog.group ?? "");
const subgroup = ref(props.catalog.subgroup ?? "");
const groups = ref<string[]>(props.catalog.groups);
const subgroups = ref<string[]>(props.catalog.subgroups);

const result = ref<RunResult | null>(null);
const explain = ref<Explain | null>(null);
const inspected = ref("");
const busy = ref(false);
const error = ref("");
/** No graph anywhere — a bookmark to a deleted filter, or a first visit that
 *  skipped the canvas. Not an error: a signpost back to the designer. */
const missing = ref(false);

/** The signed-in user's saved filters, for the rail. Examples come with the
 *  catalogue, so only this needs fetching. */
const savedList = ref<
  { id: number; name: string; kind: string; updated_at: string; alert?: boolean }[]
>([]);
const switching = ref(0);

const graphOpen = ref(false);
const canvas = ref<InstanceType<typeof GraphCanvas> | null>(null);
const detailBase = computed(() =>
  kind.value === "stock" ? props.detailBaseStock : props.detailBaseEtf,
);
const backUrl = computed(() => designerUrl(savedId.value));
const btUrl = computed(() =>
  backtestUrl({
    id: savedId.value,
    kind: kind.value,
    group: group.value,
    subgroup: subgroup.value,
  }),
);

/* ------------------------------------------------------------------ fetch */
async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error || `خطای سرور (${res.status})`);
  return data as T;
}

let token = 0;

async function run() {
  if (!graph.value.nodes.length) return;
  const mine = ++token;
  busy.value = true;
  error.value = "";
  explain.value = null;
  inspected.value = "";
  try {
    const out = await post<RunResult>("/api/designer/run", {
      graph: graph.value,
      kind: kind.value,
      group: group.value || null,
      subgroup: subgroup.value || null,
    });
    if (mine !== token) return;
    result.value = out;
  } catch (e) {
    if (mine === token) {
      error.value = (e as Error).message;
      result.value = null;
    }
  } finally {
    if (mine === token) busy.value = false;
  }
}

async function askWhy(ticker: string) {
  error.value = "";
  try {
    const out = await post<Explain>("/api/designer/explain", {
      graph: graph.value,
      kind: kind.value,
      ticker,
    });
    explain.value = out;
    inspected.value = ticker;
    // Opening the panel is the point of the button — an answer painted on a
    // collapsed diagram is no answer at all.
    if (!graphOpen.value) await toggleGraph(true);
    else await nextTick();
  } catch (e) {
    error.value = (e as Error).message;
  }
}

async function toggleGraph(open?: boolean) {
  graphOpen.value = open ?? !graphOpen.value;
  if (graphOpen.value) {
    // The board has no size until it is displayed, so framing has to wait for
    // the DOM and then for one paint.
    await nextTick();
    requestAnimationFrame(() => canvas.value?.fit());
  }
}

/* ------------------------------------------------- switching filter, in place */
/** Swap the graph and re-run, without leaving the page. */
async function activate(g: Graph, label: string, id: number | null) {
  explain.value = null;
  inspected.value = "";
  savedId.value = id;
  name.value = label;
  graph.value = g;
  missing.value = false;
  // The draft follows what is on screen, so «بازگشت به طراحی» opens the filter
  // being looked at — not the one this page was opened with.
  saveDraft({
    graph: g,
    kind: kind.value,
    name: label,
    id,
    group: group.value,
    subgroup: subgroup.value,
  });
  pushUrl();
  if (graphOpen.value) {
    await nextTick();
    requestAnimationFrame(() => canvas.value?.fit());
  }
  await run();
}

function openExample(key: string) {
  const ex = props.catalog.examples.find((e) => e.key === key);
  if (!ex) return;
  // A structural copy: the catalogue object is shared with the rail, and
  // handing it straight to the canvas would let a later edit mutate the menu.
  void activate(JSON.parse(JSON.stringify(ex.graph)) as Graph, ex.name, null);
}

async function openSaved(id: number) {
  const token = ++switching.value;
  error.value = "";
  try {
    const res = await fetch(`/api/designer/filters/${id}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error("این فیلتر پیدا نشد؛ شاید حذف شده باشد.");
    const row = await res.json();
    if (token !== switching.value) return;
    if (row.kind === "etf" || row.kind === "stock") kind.value = row.kind;
    await activate(
      typeof row.graph === "string" ? JSON.parse(row.graph) : row.graph,
      row.name,
      row.id,
    );
  } catch (e) {
    if (token === switching.value) error.value = (e as Error).message;
  }
}

async function refreshSaved() {
  try {
    const res = await fetch("/api/designer/filters", { headers: { Accept: "application/json" } });
    if (res.ok) savedList.value = (await res.json()).filters ?? [];
  } catch {
    /* the rail is a convenience; losing it must not break the table */
  }
}

/* ------------------------------------------------------------------ scope */
function pushUrl() {
  const url = resultUrl({
    id: savedId.value,
    kind: kind.value,
    group: group.value,
    subgroup: subgroup.value,
  });
  window.history.replaceState(null, "", url);
}

async function rescope() {
  pushUrl();
  // The group lists belong to the kind, and the sub-group list to the group.
  try {
    const q = new URLSearchParams({ kind: kind.value });
    if (group.value) q.set("group", group.value);
    const res = await fetch(`/api/designer/catalog?${q}`, { headers: { Accept: "application/json" } });
    if (res.ok) {
      const c = await res.json();
      groups.value = c.groups;
      subgroups.value = c.subgroups;
      if (!c.groups.includes(group.value)) group.value = "";
      if (!c.subgroups.includes(subgroup.value)) subgroup.value = "";
    }
  } catch {
    /* keep the lists we have rather than emptying the dropdowns */
  }
  await run();
}

/* --------------------------------------------------- «مقدار هر جعبه» table */
/** The explanation as rows, for readers who would rather have the numbers than
 *  the picture. Same data the chips are painted from. */
const explainRows = computed(() => {
  const ex = explain.value;
  if (!ex) return [];
  const out: { label: string; expr: string; value: string; tone: string }[] = [];
  for (const node of graph.value.nodes) {
    const spec: NodeSpec | undefined = specs.value.get(node.type);
    if (!spec) continue;
    for (const port of spec.outputs) {
      const v = ex.ports[`${node.id}:${port.id}`];
      if (!v) continue;
      let text = "—";
      let tone = "";
      if (v.kind === "bool") {
        const b = valueAt(v.tail, ex.at);
        text = b === true ? "برقرار ✓" : b === false ? "برقرار نیست ✕" : "—";
        tone = b === true ? "on" : b === false ? "off" : "";
      } else if (v.kind === "const") {
        text = fa(v.value);
      } else if (v.kind === "text") {
        text = v.value || "—";
      } else {
        const n = valueAt(v.tail, ex.at) ?? [...v.tail].reverse().find((x) => x !== null);
        text = n === null || n === undefined ? "—" : fa(Math.round(n * 100) / 100);
      }
      out.push({
        label: spec.label,
        // The port name only earns a column when a node HAS more than one —
        // «خروجی» was an em dash on every row of a graph made of comparisons.
        // On MACD or Ichimoku it is the difference between three identical rows
        // and three readable ones, so it goes in the expression instead.
        expr: chipTitle(spec, node) + (spec.outputs.length > 1 && port.label ? ` › ${port.label}` : ""),
        value: text,
        tone,
      });
    }
  }
  return out;
});

/* ------------------------------------------------------------------- boot */
onMounted(async () => {
  void refreshSaved();
  let d: Draft | null = null;
  if (props.filterId) {
    try {
      const res = await fetch(`/api/designer/filters/${props.filterId}`, {
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new Error("gone");
      const row = await res.json();
      d = {
        graph: typeof row.graph === "string" ? JSON.parse(row.graph) : row.graph,
        kind: row.kind === "etf" ? "etf" : "stock",
        name: row.name,
        id: row.id,
        group: "",
        subgroup: "",
      };
    } catch {
      error.value = "این فیلتر پیدا نشد؛ شاید حذف شده باشد.";
    }
  }
  if (!d) d = loadDraft();
  if (!d || !d.graph.nodes.length) {
    missing.value = true;
    return;
  }

  graph.value = d.graph;
  name.value = d.name;
  savedId.value = props.filterId ?? d.id;
  // The URL wins over the draft for scope: it is what a bookmark or a shared
  // link carries, and the designer puts the scope it ran with into it.
  const q = new URLSearchParams(window.location.search);
  kind.value = (q.get("kind") as "stock" | "etf") || d.kind;
  group.value = q.get("group") ?? d.group;
  subgroup.value = q.get("subgroup") ?? d.subgroup;
  if (kind.value !== props.catalog.kind || group.value) {
    await rescope();
  } else {
    await run();
  }
});
</script>

<template>
  <div class="dz dzr">
    <!-- ───────────────────────────────────────────────── header -->
    <div class="dz-bar dzr-bar">
      <div class="dz-bar-g">
        <a class="btn" :href="backUrl">◀ بازگشت به طراحی</a>
        <button type="button" class="btn btn-primary" :disabled="busy || missing" @click="run">
          <span v-if="busy">در حال اجرا…</span><span v-else>↻ اجرای دوباره</span>
        </button>
        <!-- The list this page shows is what the filter says TODAY. The
             backtest is what it said on the other seven hundred days, and it
             belongs next to the list rather than behind a menu: a user looking
             at forty matches is exactly the user who should be asking whether
             matches like these have been worth anything. -->
        <a class="btn" :href="btUrl" title="همین فیلتر روی تاریخچه">⏱ بک‌تست</a>
      </div>

      <div class="dz-bar-g">
        <label class="dz-sel">
          <span>بازار</span>
          <select v-model="kind" :disabled="missing" @change="rescope">
            <option value="stock">سهام</option>
            <option value="etf">صندوق‌ها</option>
          </select>
        </label>
        <label class="dz-sel">
          <span>{{ catalog.group_label }}</span>
          <select v-model="group" :disabled="missing" @change="subgroup = ''; rescope()">
            <option value="">همه</option>
            <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
          </select>
        </label>
        <label v-if="kind === 'stock' && subgroups.length" class="dz-sel">
          <span>زیرگروه</span>
          <select v-model="subgroup" :disabled="missing" @change="rescope">
            <option value="">همه</option>
            <option v-for="g in subgroups" :key="g" :value="g">{{ g }}</option>
          </select>
        </label>
      </div>

      <div class="dz-bar-g dz-bar-end">
        <button
          type="button"
          class="btn btn-sm"
          :aria-expanded="graphOpen"
          :disabled="missing"
          @click="toggleGraph()"
        >
          {{ graphOpen ? "بستن نمودار فیلتر" : "نمایش نمودار فیلتر" }}
        </button>
      </div>
    </div>

    <div v-if="name" class="dz-name muted small">
      فیلتر: <b>{{ name }}</b>
      <template v-if="!savedId"> · ذخیره‌نشده (پیش‌نویس همین مرورگر)</template>
    </div>

    <p v-if="error" class="flash error dz-msg">{{ error }}</p>

    <!-- ───────────────────────────────────────────────── nothing to run -->
    <section v-if="missing" class="panel">
      <div class="empty">
        <h3>فیلتری برای اجرا پیدا نشد</h3>
        <p class="muted">
          این صفحه نتیجهٔ فیلتری را نشان می‌دهد که در «طراحی فیلتر» ساخته‌اید. اگر با
          پیوند ذخیره‌شده به اینجا آمده‌اید، ممکن است آن فیلتر حذف شده باشد.
        </p>
        <p><a class="btn btn-primary" :href="backUrl">رفتن به طراحی فیلتر</a></p>
      </div>
    </section>

    <div v-else class="dzr-body">
      <!-- ───────────────────────────────── the other filters, one click each -->
      <aside class="dzr-rail">
        <div class="dzr-rail-head">فیلترهای دیگر</div>

        <template v-if="savedList.length">
          <div class="dzr-rail-sub">فیلترهای من</div>
          <button
            v-for="f in savedList"
            :key="f.id"
            type="button"
            class="dzr-rail-item"
            :class="{ 'is-on': savedId === f.id }"
            :disabled="busy"
            :title="`اجرای «${f.name}»`"
            @click="openSaved(f.id)"
          >
            <span class="dzr-rail-name">
              <b v-if="f.alert" class="dzr-rail-bell" title="این فیلتر هشدار دارد">🔔</b>
              {{ f.name }}
            </span>
            <span class="dzr-rail-kind">{{ f.kind === "etf" ? "صندوق" : "سهام" }}</span>
          </button>
        </template>

        <div class="dzr-rail-sub">نمونه‌ها</div>
        <button
          v-for="e in catalog.examples"
          :key="e.key"
          type="button"
          class="dzr-rail-item"
          :class="{ 'is-on': !savedId && name === e.name }"
          :disabled="busy"
          :title="e.desc"
          @click="openExample(e.key)"
        >
          <span class="dzr-rail-name">{{ e.name }}</span>
        </button>

        <p class="dzr-rail-hint muted small">
          روی هر کدام کلیک کنید تا همین‌جا اجرا شود. برای ساختن فیلتر تازه، «بازگشت
          به طراحی».
        </p>
      </aside>

      <div class="dzr-main">
      <!-- ─────────────────────────────────── the graph these rows came from -->
      <section v-show="graphOpen" class="panel dzr-graph">
        <div class="panel-head">
          <h2>
            نمودار فیلتر
            <span v-if="inspected" class="dz-count">
              مقادیر برای «{{ inspected }}»
              <template v-if="explain && explain.at > 0">
                — {{ fa(explain.at) }} کندل پیش
              </template>
            </span>
          </h2>
          <div class="head-actions">
            <a class="btn btn-sm" :href="backUrl">ویرایش این فیلتر</a>
          </div>
        </div>
        <div class="dzr-board">
          <GraphCanvas
            ref="canvas"
            :graph="graph"
            :specs="specs"
            :colors="colors"
            :categories="catalog.categories"
            :selected="[]"
            :explain="explain"
            readonly
          />
        </div>

        <div v-if="explainRows.length" class="table-scroll dzr-explain">
          <table class="grid">
            <thead>
              <tr>
                <th>جعبه</th>
                <th>عبارت</th>
                <th>مقدار</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in explainRows" :key="i">
                <td class="rtl-name">{{ r.label }}</td>
                <td class="mono dzr-expr">{{ r.expr }}</td>
                <td :class="r.tone === 'on' ? 'up-t' : r.tone === 'off' ? 'down-t' : ''">
                  {{ r.value }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <ResultPanel
        :result="result"
        :detail-base="detailBase"
        :busy="busy"
        :inspected="inspected"
        :title="name"
        @explain="askWhy"
      />
      </div>
    </div>
  </div>
</template>
