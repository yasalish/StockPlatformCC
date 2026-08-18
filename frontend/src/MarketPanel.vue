<script setup lang="ts">
/**
 * MarketPanel.vue — the main market table and its filter bar.
 *
 * This is the section that used to reload the whole page on every dropdown
 * change. The filters are now client-side over one fetch, but the URL still
 * carries them, so a link is still shareable and the browser Back button still
 * moves between filter states — which is the part a naive client-side rewrite
 * usually loses.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import MarketGrid from "./MarketGrid.vue";
import type { ColumnSpec, MarketPayload, Row } from "./types";
import { fa, fy } from "./format";
import { W } from "./widths";

const props = defineProps<{
  payload: MarketPayload;
  title: string;
  exportUrl: string;
  detailBase: string;
}>();

const kind = props.payload.kind;
const isStock = kind === "stock";

/* ------------------------------------------------------------- filters */
// Same query-parameter names the Flask route reads, so an existing bookmark
// still lands on the same view and the export link keeps working unchanged.
const P1 = isStock ? "market" : "type";
const P2 = "group";
const P3 = "subgroup";

const f1 = ref("");
const f2 = ref("");
const f3 = ref("");
const text = ref("");

const label1 = isStock ? "بازار" : "نوع صندوق";
const label2 = "گروه";
const label3 = "زیرگروه";

function readUrl() {
  const q = new URLSearchParams(window.location.search);
  f1.value = q.get(P1) ?? "";
  f2.value = isStock ? (q.get(P2) ?? "") : "";
  f3.value = isStock ? (q.get(P3) ?? "") : "";
}
readUrl();

/** Options come from the rows themselves — no round trip to cascade them. */
function distinct(field: keyof Row, within?: (r: Row) => boolean): string[] {
  const set = new Set<string>();
  for (const r of props.payload.rows) {
    if (within && !within(r)) continue;
    const v = r[field];
    if (typeof v === "string" && v) set.add(v);
  }
  return Array.from(set).sort((a, b) => a.localeCompare(b, "fa"));
}

const options1 = computed(() => distinct(isStock ? "market" : "type"));
const options2 = computed(() => (isStock ? distinct("sector") : []));
// The «زیرگروه» list narrows to the chosen «گروه», exactly as
// db.stock_sub_sectors(sector) did — but derived locally.
const options3 = computed(() =>
  isStock ? distinct("sub_sector", (r) => !f2.value || r.sector === f2.value) : [],
);

// A sub-group that no longer belongs to the chosen group is dropped, mirroring
// the route's "stale subgroup after changing group" guard.
watch(f2, () => {
  if (f3.value && !options3.value.includes(f3.value)) f3.value = "";
});

const filtered = computed<Row[]>(() =>
  props.payload.rows.filter(
    (r) =>
      (!f1.value || (isStock ? r.market : r.type) === f1.value) &&
      (!f2.value || r.sector === f2.value) &&
      (!f3.value || r.sub_sector === f3.value),
  ),
);

/* ------------------------------------------------------------- URL sync */
function currentQuery() {
  const q = new URLSearchParams(window.location.search);
  const set = (k: string, v: string) => (v ? q.set(k, v) : q.delete(k));
  set(P1, f1.value);
  if (isStock) {
    set(P2, f2.value);
    set(P3, f3.value);
  }
  return q;
}

let restoring = false;
watch([f1, f2, f3], () => {
  if (restoring) return;
  const q = currentQuery();
  const qs = q.toString();
  // pushState, so Back steps through filter states the way a page reload used
  // to. The text filter is deliberately NOT in the URL — it never was, and one
  // history entry per keystroke would make Back useless.
  window.history.pushState({ bn: true }, "", qs ? `?${qs}` : window.location.pathname);
});

function onPop() {
  restoring = true;
  readUrl();
  // Let the watcher above see the new values without pushing them back.
  requestAnimationFrame(() => (restoring = false));
}
onMounted(() => window.addEventListener("popstate", onPop));
onUnmounted(() => window.removeEventListener("popstate", onPop));

/* --------------------------------------------------------------- export */
// The Excel export stays a server route (reports.py builds the workbook); the
// link just has to carry whatever the user has filtered to.
const exportHref = computed(() => {
  const q = new URLSearchParams();
  if (f1.value) q.set(isStock ? "market" : "type", f1.value);
  if (isStock && f2.value) q.set("group", f2.value);
  if (isStock && f3.value) q.set("subgroup", f3.value);
  const qs = q.toString();
  return qs ? `${props.exportUrl}?${qs}` : props.exportUrl;
});

/* -------------------------------------------------------------- columns */
const columns = computed<ColumnSpec[]>(() => {
  const cols: ColumnSpec[] = [
    { id: "ticker", label: "نماد", sort: "str", width: W.symbol, cell: { type: "symbol" } },
    { id: "name", label: "نام", sort: "str", width: W.name, cell: { type: "name" } },
    {
      id: "f1",
      label: label1,
      sort: "str",
      width: W.market,
      cell: isStock ? { type: "muted", field: "market" } : { type: "tag" },
    },
  ];
  if (isStock) {
    cols.push({ id: "sector", label: label2, sort: "str", width: W.sector, cell: { type: "muted", field: "sector" } });
    cols.push({ id: "sub", label: label3, sort: "str", width: W.subSector, cell: { type: "muted", field: "sub_sector" } });
  }
  cols.push({ id: "latest", label: "قیمت پایانی", sort: "num", width: W.price, cell: { type: "price" } });
  for (const p of props.payload.periods) {
    cols.push({ id: p.key, label: p.label, sort: "num", numh: true, width: W.period, cell: { type: "pill", field: p.key } });
  }
  cols.push({ id: "chev", label: "", width: W.chev, cell: { type: "chev" } });
  return cols;
});

/* ------------------------------------------------------------ watchlist */
const watched = ref(new Set(props.payload.watched));
function onWatchToggled(key: string, on: boolean) {
  const next = new Set(watched.value);
  if (on) next.add(key);
  else next.delete(key);
  watched.value = next;
}

const gridRef = ref<InstanceType<typeof MarketGrid> | null>(null);
const shownCount = computed(() => {
  const q = text.value.trim();
  return q ? filtered.value.filter((r) => (r.ticker || "").includes(q)).length : filtered.value.length;
});
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h1>{{ title }}</h1>
      <div class="head-actions">
        <span class="muted">تاریخ مبنا: {{ fy(payload.as_of) }} · {{ fa(shownCount) }} نماد</span>
        <a class="btn" :href="exportHref">⬇ خروجی اکسل</a>
      </div>
    </div>

    <form class="filterbar" @submit.prevent>
      <label>{{ label1 }}:</label>
      <select v-model="f1">
        <option value="">همه</option>
        <option v-for="o in options1" :key="o" :value="o">{{ o }}</option>
      </select>

      <template v-if="isStock">
        <label>{{ label2 }}:</label>
        <select v-model="f2">
          <option value="">همه</option>
          <option v-for="o in options2" :key="o" :value="o">{{ o }}</option>
        </select>

        <template v-if="options3.length">
          <label>{{ label3 }}:</label>
          <select v-model="f3">
            <option value="">همه</option>
            <option v-for="o in options3" :key="o" :value="o">{{ o }}</option>
          </select>
        </template>
      </template>

      <input id="tablefilter" v-model="text" type="text" placeholder="فیلتر نماد در جدول…" autocomplete="off" />
    </form>

    <MarketGrid
      ref="gridRef"
      :rows="filtered"
      :columns="columns"
      :kind="kind"
      :etf-type-colors="payload.etf_type_colors"
      :watched="watched"
      :filter-text="text"
      :detail-base="detailBase"
      @watch-toggled="onWatchToggled"
    />
  </section>
</template>
