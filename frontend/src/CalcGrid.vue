<script setup lang="ts">
/**
 * CalcGrid.vue — the «بازدهٔ دوره‌ای» table inside the calculator panel.
 *
 * Same component underneath as the main table; only the columns differ (the
 * finer CALC_PERIODS set, and no sub-group column).
 *
 * WHAT STAYS ON THE SERVER, AND WHY. The panel's other controls — «نماد نخست»,
 * «نماد دوم» and the category select — still submit the Jinja form, because
 * they drive a real server-side computation: _period_panel() works out the top
 * performer per period within the chosen category, and the head-to-head winner
 * column. Those two comparison tables are eleven rows each and are left in
 * Jinja. This component only replaces the long list underneath them, which is
 * the part that was rendering 742 rows into the DOM.
 *
 * The category and the pinned tickers are therefore passed in from the
 * server-rendered page, and applied here so the list matches what
 * _period_panel() would have produced.
 */
import { computed, ref } from "vue";
import MarketGrid from "./MarketGrid.vue";
import type { ColumnSpec, MarketPayload, Row } from "./types";
import { fa } from "./format";
import { W } from "./widths";

const props = defineProps<{
  payload: MarketPayload;
  /** The «گروه»/«نوع صندوق» chosen in the panel's form (blank = whole market). */
  cat: string;
  /** Compared tickers, floated to the top exactly as the server did. */
  pinned: string[];
  detailBase: string;
}>();

const kind = props.payload.kind;
const isStock = kind === "stock";
const text = ref("");

// _period_panel(): pool = [r for r in rows if r[catkey] == cat] if cat else rows
const pool = computed<Row[]>(() => {
  if (!props.cat) return props.payload.calc_rows;
  const key = isStock ? "sector" : "type";
  return props.payload.calc_rows.filter((r) => r[key] === props.cat);
});

const shownCount = computed(() => {
  const q = text.value.trim();
  return q ? pool.value.filter((r) => (r.ticker || "").includes(q)).length : pool.value.length;
});

const columns = computed<ColumnSpec[]>(() => {
  const cols: ColumnSpec[] = [
    { id: "ticker", label: "نماد", sort: "str", width: W.symbol, cell: { type: "symbol" } },
    { id: "name", label: "نام", sort: "str", width: W.name, cell: { type: "name" } },
    {
      id: "cat",
      label: isStock ? "بازار" : "نوع صندوق",
      sort: "str",
      width: W.market,
      cell: isStock ? { type: "muted", field: "market" } : { type: "tag" },
    },
  ];
  if (isStock) {
    cols.push({ id: "sector", label: "گروه", sort: "str", width: W.sector, cell: { type: "muted", field: "sector" } });
  }
  cols.push({ id: "latest", label: "قیمت پایانی", sort: "num", width: W.price, cell: { type: "price" } });
  for (const p of props.payload.calc_periods) {
    cols.push({ id: p.key, label: p.label, sort: "num", numh: true, width: W.period, cell: { type: "pill", field: p.key } });
  }
  cols.push({ id: "chev", label: "", width: W.chev, cell: { type: "chev" } });
  return cols;
});

const watched = ref(new Set(props.payload.watched));
function onWatchToggled(key: string, on: boolean) {
  const next = new Set(watched.value);
  if (on) next.add(key);
  else next.delete(key);
  watched.value = next;
}

const heading = computed(
  () => `بازدهٔ دوره‌ای ${props.cat ? `«${props.cat}»` : "همهٔ نمادها"} (${fa(shownCount.value)})`,
);
</script>

<template>
  <div class="cmp-head"><h3>{{ heading }}</h3></div>
  <form class="filterbar" @submit.prevent>
    <input id="calcfilter" v-model="text" type="text" placeholder="فیلتر نماد در جدول…" autocomplete="off" />
  </form>

  <MarketGrid
    :rows="pool"
    :columns="columns"
    :kind="kind"
    :etf-type-colors="payload.etf_type_colors"
    :watched="watched"
    :filter-text="text"
    :pinned="pinned"
    :detail-base="detailBase"
    @watch-toggled="onWatchToggled"
  />
</template>
