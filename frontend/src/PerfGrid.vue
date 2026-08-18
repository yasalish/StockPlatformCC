<script setup lang="ts">
/**
 * PerfGrid.vue — the wide «بازدهٔ دوره‌ای» table, virtualized.
 *
 * WHY THIS EXISTS
 * The Jinja version rendered 782 rows × 24 columns: 2.2 MB of HTML and 37,000
 * DOM nodes, which cost the browser ~1.3 s of parsing and layout on every single
 * navigation — far more than the server spent producing it. Only the ~20 rows on
 * screen are ever needed, so this renders those.
 *
 * It is a sibling of MarketGrid.vue rather than a generalisation of it: this
 * table has a two-row header (a period name spanning its سقف/کف pair, then one
 * clickable sub-header per half) and a pinned first column, neither of which
 * MarketGrid has. Widening MarketGrid to cover both would put the market page —
 * which works and is verified by verify_order08.py — at risk for no gain.
 *
 * The header, classes and cell markup match static/css/style.css exactly
 * (.perf-grid, .grp, .grp-start, .numh, .sub, .pill), so the page looks the same
 * and the stylesheet needs nothing new.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useWindowVirtualizer } from "@tanstack/vue-virtual";
import type { PerfCol, Row } from "./types";
import { fa, pill } from "./format";

const props = defineProps<{
  rows: Row[];
  cols: PerfCol[];
  kind: "stock" | "etf";
  groupLabel: string;
  etfTypeColors: Record<string, string>;
  watched: Set<string>;
  filterText: string;
  /** Ticker to float to the top — the symbol being compared, as `.pinned`. */
  pinned?: string | null;
  detailBase: string;
}>();

const emit = defineEmits<{ (e: "watch-toggled", key: string, on: boolean): void }>();

/* ------------------------------------------------------------- columns */
type Lead = { id: string; label: string; sort: "str" | "num"; kind: "symbol" | "group" | "price" };
const LEAD: Lead[] = [
  { id: "ticker", label: "نماد", sort: "str", kind: "symbol" },
  { id: "group", label: "", sort: "str", kind: "group" },
  { id: "latest", label: "قیمت پایانی", sort: "num", kind: "price" },
];

/** Field name on a row: «سقف» is <key>_ceil, «کف» is <key>_floor. */
function fieldOf(col: PerfCol, half: "ceil" | "floor") {
  return `${col.key}_${half}`;
}

/**
 * Column widths, computed from the data rather than measured from the DOM.
 *
 * A virtualized table cannot let the browser size its columns: only ~20 rows
 * exist, so the widths would come from those and shift on every scroll. The
 * un-virtualized page produced 91-138 px for these columns depending on how long
 * the widest number in each is («از ابتدا» reaches four figures), so the same
 * rule is applied here — format the largest magnitude in the column once and
 * size to it. Cheap (one format per column, not one per cell) and stable,
 * because it depends on the data and not on what is scrolled into view.
 */
const CHAR = 8;        // px per Persian digit at the grid's 13.5px font
const PAD = 46;        // cell padding (12+12) + pill padding (9+9) + a little air
function widthFor(field: string, min: number) {
  let widest = 0;
  for (const r of props.rows) {
    const v = r[field];
    if (typeof v === "number") {
      const a = Math.abs(v);
      if (a > widest) widest = a;
    }
  }
  const text = pill(widest).text;
  return Math.max(min, Math.min(180, PAD + text.length * CHAR));
}

const W = { symbol: 107, group: 240, price: 89, chev: 29 };

interface Col {
  id: string;
  label: string;
  sort: "str" | "num";
  width: number;
  /** first column of a period pair — gets the 2px separator */
  start?: boolean;
  cell: { type: "symbol" | "group" | "price" | "chev" } | { type: "pill"; field: string };
}

const columns = computed<Col[]>(() => {
  const out: Col[] = [
    { id: "ticker", label: "نماد", sort: "str", width: W.symbol, cell: { type: "symbol" } },
    { id: "group", label: props.groupLabel, sort: "str", width: W.group, cell: { type: "group" } },
    { id: "latest", label: "قیمت پایانی", sort: "num", width: W.price, cell: { type: "price" } },
  ];
  for (const c of props.cols) {
    const ceil = fieldOf(c, "ceil");
    const floor = fieldOf(c, "floor");
    out.push({ id: ceil, label: "سقف", sort: "num", width: widthFor(ceil, 91), start: true, cell: { type: "pill", field: ceil } });
    out.push({ id: floor, label: "کف", sort: "num", width: widthFor(floor, 91), cell: { type: "pill", field: floor } });
  }
  out.push({ id: "chev", label: "", sort: "str", width: W.chev, cell: { type: "chev" } });
  return out;
});

const minWidth = computed(() => columns.value.reduce((n, c) => n + c.width, 0));

/* ------------------------------------------------------------- sorting */
// Same rule as BN.initTable in static/js/app.js: each header remembers its own
// direction, the first click sorts ascending, and rows with no value for the
// column sink to the BOTTOM in both directions instead of leading the ascending
// sort as a fake −99999٪.
const sortCol = ref<string | null>(null);
const sortDir = ref<1 | -1>(1);
const dirMemory = new Map<string, 1 | -1>();

function toggleSort(col: Col) {
  if (col.id === "chev") return;
  const prev = dirMemory.get(col.id) ?? 0;
  const next: 1 | -1 = prev === 1 ? -1 : 1;
  dirMemory.set(col.id, next);
  sortCol.value = col.id;
  sortDir.value = next;
}

function sortClass(col: Col) {
  if (sortCol.value !== col.id) return "";
  return sortDir.value === 1 ? "sorted-asc" : "sorted-desc";
}

function groupText(row: Row): string {
  return ((props.kind === "etf" ? row.type : row.sector) as string) || "";
}

const visibleRows = computed<Row[]>(() => {
  const q = props.filterText.trim();
  let out = q ? props.rows.filter((r) => (r.ticker || "").includes(q)) : props.rows.slice();

  const id = sortCol.value;
  if (id) {
    const col = columns.value.find((c) => c.id === id);
    if (col) {
      const dir = sortDir.value;
      if (col.sort === "num") {
        const value = (r: Row): number | null => {
          if (col.cell.type === "price") return (r.latest as number) ?? null;
          const v = r[(col.cell as { field: string }).field];
          return typeof v === "number" ? v : null;
        };
        out.sort((a, b) => {
          const va = value(a);
          const vb = value(b);
          if (va === null || vb === null) return va === vb ? 0 : va === null ? 1 : -1;
          return (va - vb) * dir;
        });
      } else {
        const text = (r: Row) => (col.id === "ticker" ? r.ticker : groupText(r));
        out.sort((a, b) => String(text(a)).localeCompare(String(text(b)), "fa") * dir);
      }
    }
  }

  // The compared symbol floats to the top, as the Jinja `.pinned` row did.
  const pin = props.pinned;
  if (pin) {
    const top = out.filter((r) => r.ticker === pin);
    if (top.length) out = [...top, ...out.filter((r) => r.ticker !== pin)];
  }
  return out;
});

/* ------------------------------------------------------- virtualization */
const tbodyEl = ref<HTMLElement | null>(null);
// Measured on the un-virtualized page: 47.8px median (one line box + padding;
// this table has no wrapping name column, so rows are uniform).
const ESTIMATED_ROW = 48;

const scrollMargin = ref(0);
let rafId = 0;

function measureMargin() {
  const el = tbodyEl.value;
  if (!el) return;
  const next = Math.round(el.getBoundingClientRect().top + window.scrollY);
  if (next !== scrollMargin.value) scrollMargin.value = next;
}

function scheduleMeasure() {
  if (rafId) return;
  rafId = requestAnimationFrame(() => {
    rafId = 0;
    measureMargin();
  });
}

/* ------------------------------------------- the mirrored top scrollbar */
// performance.html used to carry a hand-written copy of this in a <script>. It
// belongs with the table it mirrors: the island owns the table now, and the
// element it scrolled did not exist when that script ran.
const scroller = ref<HTMLElement | null>(null);
const topbar = ref<HTMLElement | null>(null);
const showTop = ref(false);
let lock = false;

function syncTop() {
  const s = scroller.value;
  const t = topbar.value;
  if (!s || !t) return;
  const spacer = t.firstElementChild as HTMLElement | null;
  if (spacer) spacer.style.width = minWidth.value + "px";
  showTop.value = minWidth.value > s.clientWidth;
}

function onTopScroll() {
  if (lock || !scroller.value || !topbar.value) return;
  lock = true;
  scroller.value.scrollLeft = topbar.value.scrollLeft;
  lock = false;
}

function onBodyScroll() {
  if (lock || !scroller.value || !topbar.value) return;
  lock = true;
  topbar.value.scrollLeft = scroller.value.scrollLeft;
  lock = false;
}

onMounted(() => {
  measureMargin();
  requestAnimationFrame(() => {
    measureMargin();
    requestAnimationFrame(measureMargin);
  });
  syncTop();
  window.addEventListener("scroll", scheduleMeasure, { passive: true });
  window.addEventListener("resize", scheduleMeasure);
  window.addEventListener("resize", syncTop);
});

onUnmounted(() => {
  if (rafId) cancelAnimationFrame(rafId);
  window.removeEventListener("scroll", scheduleMeasure);
  window.removeEventListener("resize", scheduleMeasure);
  window.removeEventListener("resize", syncTop);
});

const virtualizer = useWindowVirtualizer(
  computed(() => ({
    count: visibleRows.value.length,
    estimateSize: () => ESTIMATED_ROW,
    overscan: 8,
    scrollMargin: scrollMargin.value,
  })),
);

const virtualRows = computed(() => virtualizer.value.getVirtualItems());
const totalSize = computed(() => virtualizer.value.getTotalSize());
const padTop = computed(() =>
  virtualRows.value.length ? virtualRows.value[0].start - scrollMargin.value : 0,
);
const padBottom = computed(() =>
  virtualRows.value.length
    ? totalSize.value - virtualRows.value[virtualRows.value.length - 1].end
    : 0,
);

watch(
  () => [visibleRows.value.length, minWidth.value],
  () => {
    virtualizer.value.measure();
    scheduleMeasure();
    syncTop();
  },
);

/* -------------------------------------------------------------- watchlist */
function getBN(): { toggleWatch(el: HTMLElement): Promise<void> } | undefined {
  try {
    // `const BN = …` in app.js is a lexical global, not a window property.
    // eslint-disable-next-line no-undef
    return typeof BN !== "undefined" ? (BN as never) : undefined;
  } catch {
    return undefined;
  }
}

async function onStar(event: MouseEvent, row: Row) {
  event.stopPropagation();
  const btn = event.currentTarget as HTMLElement;
  const bn = getBN();
  if (!bn) return;
  await bn.toggleWatch(btn);
  emit("watch-toggled", `${props.kind}:${row.ticker}`, btn.classList.contains("on"));
}

function go(event: MouseEvent, row: Row) {
  if ((event.target as HTMLElement).closest(".watch-star")) return;
  window.location.href = `${props.detailBase}${row.id}`;
}

function tagColor(t: string | null) {
  return (t && props.etfTypeColors[t]) || "#868fa3";
}

function cellPill(row: Row, field: string) {
  return pill(row[field] as number | null);
}

defineExpose({ visibleCount: computed(() => visibleRows.value.length) });
</script>

<template>
  <div v-show="showTop" ref="topbar" class="table-scroll-top" aria-hidden="true" @scroll="onTopScroll">
    <div></div>
  </div>
  <div ref="scroller" class="table-scroll" @scroll="onBodyScroll">
    <table class="grid sortable perf-grid grid-virtual" :style="{ minWidth: minWidth + 'px' }">
      <colgroup>
        <col v-for="col in columns" :key="col.id" :style="{ width: col.width + 'px' }" />
      </colgroup>
      <thead>
        <tr>
          <th
            v-for="lead in LEAD"
            :key="lead.id"
            rowspan="2"
            :class="sortClass(columns.find((c) => c.id === lead.id)!)"
            :data-sort="lead.sort"
            @click="toggleSort(columns.find((c) => c.id === lead.id)!)"
          >{{ lead.kind === 'group' ? groupLabel : lead.label }}</th>

          <th v-for="c in cols" :key="c.key" colspan="2" class="numh grp grp-start">{{ c.label }}</th>
          <th rowspan="2"></th>
        </tr>
        <!-- each half of a period gets its own header, so سقف AND کف are sortable -->
        <tr>
          <template v-for="c in cols" :key="c.key">
            <th
              class="numh sub grp-start"
              data-sort="num"
              :class="sortClass(columns.find((x) => x.id === c.key + '_ceil')!)"
              :title="c.label + ' — فاصلهٔ قیمت پایانی تا بیشترین قیمت بازه'"
              @click="toggleSort(columns.find((x) => x.id === c.key + '_ceil')!)"
            >سقف</th>
            <th
              class="numh sub"
              data-sort="num"
              :class="sortClass(columns.find((x) => x.id === c.key + '_floor')!)"
              :title="c.label + ' — فاصلهٔ قیمت پایانی تا کمترین قیمت بازه'"
              @click="toggleSort(columns.find((x) => x.id === c.key + '_floor')!)"
            >کف</th>
          </template>
        </tr>
      </thead>
      <tbody ref="tbodyEl">
        <tr v-if="padTop > 0" class="vpad" aria-hidden="true">
          <td :colspan="columns.length" :style="{ height: padTop + 'px', padding: 0, border: 0 }"></td>
        </tr>

        <tr
          v-for="vr in virtualRows"
          :key="visibleRows[vr.index].ticker"
          :ref="(el) => virtualizer.measureElement(el as Element)"
          :data-index="vr.index"
          class="clickable"
          :class="{ pinned: pinned && visibleRows[vr.index].ticker === pinned }"
          :data-ticker="visibleRows[vr.index].ticker"
          @click="go($event, visibleRows[vr.index])"
        >
          <td class="sym">
            <button
              type="button"
              class="watch-star"
              :class="{ on: watched.has(kind + ':' + visibleRows[vr.index].ticker) }"
              :data-kind="kind"
              :data-ticker="visibleRows[vr.index].ticker"
              :data-id="visibleRows[vr.index].id"
              title="افزودن/حذف از دیده‌بان"
              aria-label="دیده‌بان"
              @click="onStar($event, visibleRows[vr.index])"
            >★</button>{{ visibleRows[vr.index].ticker }}
          </td>

          <td class="small">
            <span
              v-if="kind === 'etf'"
              class="tag"
              :style="{ background: tagColor(visibleRows[vr.index].type as string | null) }"
            >{{ visibleRows[vr.index].type }}</span>
            <span v-else class="muted">{{ visibleRows[vr.index].sector || '—' }}</span>
          </td>

          <td class="num" :data-v="visibleRows[vr.index].latest || 0">
            {{ fa(visibleRows[vr.index].latest as number | null) }}
          </td>

          <template v-for="c in cols" :key="c.key">
            <td class="num grp-start" :data-v="(visibleRows[vr.index][c.key + '_ceil'] ?? -99999) as number">
              <span
                v-if="!cellPill(visibleRows[vr.index], c.key + '_ceil').missing"
                class="pill"
                :class="cellPill(visibleRows[vr.index], c.key + '_ceil').cls"
              >{{ cellPill(visibleRows[vr.index], c.key + '_ceil').text }}</span>
              <span v-else class="muted">—</span>
            </td>
            <td class="num" :data-v="(visibleRows[vr.index][c.key + '_floor'] ?? -99999) as number">
              <span
                v-if="!cellPill(visibleRows[vr.index], c.key + '_floor').missing"
                class="pill"
                :class="cellPill(visibleRows[vr.index], c.key + '_floor').cls"
              >{{ cellPill(visibleRows[vr.index], c.key + '_floor').text }}</span>
              <span v-else class="muted">—</span>
            </td>
          </template>

          <td class="chev">›</td>
        </tr>

        <tr v-if="padBottom > 0" class="vpad" aria-hidden="true">
          <td :colspan="columns.length" :style="{ height: padBottom + 'px', padding: 0, border: 0 }"></td>
        </tr>
      </tbody>
    </table>
  </div>
  <p v-if="!visibleRows.length" class="muted note">داده‌ای برای نمایش یافت نشد.</p>
</template>
