<script setup lang="ts">
/**
 * MarketGrid.vue — the sortable, filterable, virtualized table.
 *
 * Renders byte-for-byte the same markup the Jinja loop produced (same classes,
 * same partials inlined: _star.html and _pill.html), so static/css/style.css
 * needs no changes and the RTL layout is identical. The differences are all
 * behavioural: sorting and filtering never touch the server, and only the rows
 * actually on screen exist in the DOM.
 *
 * VIRTUALIZATION USES THE WINDOW, NOT AN INNER SCROLLER.
 * The obvious approach — a fixed-height div with overflow-y:auto — would put a
 * second scrollbar inside the page and change how the table reads, which the
 * order forbids ("RTL layout must match the current page exactly"). A window
 * virtualizer keeps the page scrolling exactly as it does today; the table just
 * stops materialising the rows nobody is looking at. Spacer rows above and
 * below hold the scroll height, so <table> semantics and the existing
 * border-collapse styling survive.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useWindowVirtualizer } from "@tanstack/vue-virtual";
import type { ColumnSpec, Row } from "./types";
import { fa, fy, pill } from "./format";
import { handledInRow, openDetail } from "./nav";

const props = defineProps<{
  rows: Row[];
  columns: ColumnSpec[];
  kind: "stock" | "etf";
  etfTypeColors: Record<string, string>;
  watched: Set<string>;
  /** Substring match on the ticker — the same rule BN.initTable used. */
  filterText: string;
  /** Tickers pinned to the top (the compared symbols on the calculator table). */
  pinned?: string[];
  detailBase: string;
  /** The table's base date, so a symbol that has not traded since can say so. */
  asOf?: string | null;
}>();

const emit = defineEmits<{ (e: "watch-toggled", key: string, on: boolean): void }>();

/* ---------------------------------------------------------------- sorting */
// Reproduces BN.initTable exactly: each header remembers its own direction and
// the FIRST click sorts ascending (dir 0 → 1). Odd for a gains column, but it
// is what the page does today and this conversion must not change behaviour.
const sortCol = ref<string | null>(null);
const sortDir = ref<1 | -1>(1);
const dirMemory = new Map<string, 1 | -1>();

function toggleSort(col: ColumnSpec) {
  if (!col.sort) return;
  const prev = dirMemory.get(col.id) ?? 0;
  const next: 1 | -1 = prev === 1 ? -1 : 1;
  dirMemory.set(col.id, next);
  sortCol.value = col.id;
  sortDir.value = next;
}

function sortClass(col: ColumnSpec) {
  if (sortCol.value !== col.id) return "";
  return sortDir.value === 1 ? "sorted-asc" : "sorted-desc";
}

/* ------------------------------------------------------------------ staleness
 * A halted (متوقف) symbol's periods are all ۰٪ — correctly, because the window
 * is the MARKET's last n sessions and it did not trade in them. But ۰٪ looks the
 * same as a symbol that traded flat, so the row says which it is. `ldate` is the
 * symbol's own last session; when it is behind the table's base date the symbol
 * has not traded since. */
function staleSince(row: Row): string | null {
  const d = row.ldate;
  if (!d || !props.asOf || d === props.asOf) return null;
  return d;
}

/** Tooltip for a halted symbol; null (so no `title` at all) for a live one. */
function staleTip(row: Row): string | undefined {
  const d = staleSince(row);
  return d === null
    ? undefined
    : `آخرین معاملهٔ این نماد ${fy(d)} بوده. بازدهی هر دوره روی روزهای معاملاتی `
      + `بازار حساب می‌شود، پس دوره‌هایی که تمامشان در بازهٔ توقف بوده‌اند ۰٪ هستند.`;
}

/** The value BN.initTable would have read out of the cell's data-v / text. */
function sortValue(row: Row, col: ColumnSpec): number | string {
  switch (col.cell.type) {
    case "price":
      // Jinja wrote data-v="{{ r.latest or 0 }}".
      return (row.latest as number) || 0;
    case "pill": {
      // Jinja wrote data-v="{{ r[key] if not none else -99999 }}" — missing
      // values sort below every real one, in both directions.
      const v = row[col.cell.field];
      return v === null || v === undefined ? -99999 : (v as number);
    }
    case "symbol":
      return row.ticker;
    case "name":
      return row.name ?? "";
    case "tag":
      return row.type ?? "";
    case "muted":
      return (row[col.cell.field] as string) ?? "";
    default:
      return "";
  }
}

/* ------------------------------------------------- filtering + ordering */
const visibleRows = computed<Row[]>(() => {
  const q = props.filterText.trim();
  let out = q ? props.rows.filter((r) => (r.ticker || "").includes(q)) : props.rows.slice();

  if (sortCol.value) {
    const col = props.columns.find((c) => c.id === sortCol.value);
    if (col) {
      const dir = sortDir.value;
      out.sort((a, b) => {
        const va = sortValue(a, col);
        const vb = sortValue(b, col);
        if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
        // localeCompare with the "fa" collation, as the vanilla version did.
        return String(va).localeCompare(String(vb), "fa") * dir;
      });
    }
  }

  // Pinned symbols float to the top, matching _period_panel()'s `display` list.
  const pins = props.pinned;
  if (pins && pins.length) {
    const set = new Set(pins);
    const top = out.filter((r) => set.has(r.ticker));
    if (top.length) out = [...top, ...out.filter((r) => !set.has(r.ticker))];
  }
  return out;
});

/* -------------------------------------------------------- virtualization */
const tbodyEl = ref<HTMLElement | null>(null);
// Measured from the un-virtualized page: rows are 48–96px tall depending on how
// the Persian name and industry-group text wrap, with a median near 62. The
// virtualizer measures each row for real once it is rendered, but a bad
// estimate makes the scrollbar wrong until then and the document visibly grows
// as you scroll — 37px (padding plus one line box) was far too optimistic.
const ESTIMATED_ROW = 62;

/**
 * How far down the DOCUMENT this table's body starts.
 *
 * A window virtualizer maps window.scrollY onto a row index, so it has to know
 * where the rows begin. offsetTop is the wrong measurement and quietly so: it
 * is relative to the nearest positioned ancestor, which on this page reports 43
 * while the main table actually starts about 47,000px down — below the
 * calculator table, which is itself 742 rows tall. With 43 the second table
 * decided which rows to show from a scroll position that had nothing to do with
 * it, and displayed the wrong slice for the entire page.
 *
 * It also cannot be measured once. The table above this one grows as ITS rows
 * are measured for real, which moves this one down, so the margin is
 * re-measured on scroll and resize (rAF-throttled, one rect read per frame).
 */
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

onMounted(() => {
  measureMargin();
  // And again after the browser has laid the page out: the table above this
  // one is still being measured on the first frame, so its height — and
  // therefore this table's offset — is not final yet.
  requestAnimationFrame(() => {
    measureMargin();
    requestAnimationFrame(measureMargin);
  });
  window.addEventListener("scroll", scheduleMeasure, { passive: true });
  window.addEventListener("resize", scheduleMeasure);
});

onUnmounted(() => {
  if (rafId) cancelAnimationFrame(rafId);
  window.removeEventListener("scroll", scheduleMeasure);
  window.removeEventListener("resize", scheduleMeasure);
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

// With table-layout:fixed the table would shrink to its container; min-width
// keeps it at its natural size and lets .table-scroll scroll horizontally,
// which is exactly what the un-virtualized table did.
const minWidth = computed(() => props.columns.reduce((n, c) => n + c.width, 0));
const totalSize = computed(() => virtualizer.value.getTotalSize());
const padTop = computed(() =>
  virtualRows.value.length ? virtualRows.value[0].start - scrollMargin.value : 0,
);
const padBottom = computed(() =>
  virtualRows.value.length
    ? totalSize.value - virtualRows.value[virtualRows.value.length - 1].end
    : 0,
);

// Re-measure when the row set changes size, or the scrollbar keeps the old
// height after a filter narrows the list.
watch(
  () => visibleRows.value.length,
  () => {
    virtualizer.value.measure();
    // A filter changes this table's height, which moves anything below it.
    scheduleMeasure();
  },
);

/* -------------------------------------------------------------- watchlist */
/**
 * app.js declares `const BN = (function(){…})()` in a classic script. A `const`
 * at top level is a LEXICAL global: other scripts and modules can see the
 * binding `BN`, but it is NOT a property of `window` — so `window.BN` is
 * undefined and the star silently did nothing. `typeof` on a possibly-undeclared
 * identifier is the one safe way to probe for it without a ReferenceError.
 */
function getBN(): { toggleWatch(el: HTMLElement): Promise<void> } | undefined {
  try {
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
  // Reuse the app's own handler rather than reimplementing it: it owns the
  // POST, the nav badge counter and the "every star for this symbol" sync that
  // keeps the two tables on this page in step. Awaiting it lets the island read
  // back the resulting state instead of guessing.
  await bn.toggleWatch(btn);
  emit("watch-toggled", `${props.kind}:${row.ticker}`, btn.classList.contains("on"));
}

function rowHref(row: Row) {
  return `${props.detailBase}${row.id}`;
}

function go(event: MouseEvent, row: Row) {
  // The star and the two symbol links handle their own clicks; everything else
  // in the row is fair game and opens the same detail page in a new tab.
  if (handledInRow(event)) return;
  openDetail(rowHref(row));
}

function tagColor(t: string | null) {
  return (t && props.etfTypeColors[t]) || "#868fa3";
}

defineExpose({ visibleCount: computed(() => visibleRows.value.length) });
</script>

<template>
  <div class="table-scroll">
      <!-- M-6. Window virtualization is hostile to assistive technology: only
           ~60 of ~780 rows exist in the DOM at any moment, so without these a
           screen reader announces a 60-row table and a user has no idea the
           other 720 are there.

           aria-rowcount is the TRUE total (header rows included) and
           aria-rowindex is each row's position in that total, so the reported
           size and position stay right however few rows are mounted.

           NOT role="grid", which the review suggested. These are real <table>
           elements with real <thead>/<tbody>, and role="grid" would REPLACE
           those native semantics with a widget contract this component does not
           honour — arrow-key cell navigation, a managed focus point. Native
           table semantics plus accurate counts is the better trade; the counts
           were the part that was missing. -->
    <table class="grid sortable grid-virtual" :style="{ minWidth: minWidth + 'px' }"
           :aria-rowcount="visibleRows.length + 1">
      <colgroup>
        <col v-for="col in columns" :key="col.id" :style="{ width: col.width + 'px' }" />
      </colgroup>
      <thead>
        <tr aria-rowindex="1">
          <th
            v-for="col in columns"
            :key="col.id"
            :class="[col.numh ? 'numh' : '', sortClass(col)]"
            :data-sort="col.sort"
            @click="toggleSort(col)"
          >
            {{ col.label }}
          </th>
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
          :aria-rowindex="vr.index + 2"
          class="clickable"
          :class="{ pinned: pinned && pinned.includes(visibleRows[vr.index].ticker) }"
          :data-ticker="visibleRows[vr.index].ticker"
          @click="go($event, visibleRows[vr.index])"
        >
          <template v-for="col in columns" :key="col.id">
            <td
              v-if="col.cell.type === 'symbol'"
              class="sym"
              :class="{ stale: staleSince(visibleRows[vr.index]) }"
              :title="staleTip(visibleRows[vr.index])"
            >
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
              >★</button><a
                class="row-link"
                :href="rowHref(visibleRows[vr.index])"
                target="_blank"
                rel="noopener"
                @click.stop
              >{{ visibleRows[vr.index].ticker }}</a>
            </td>

            <td v-else-if="col.cell.type === 'name'" class="rtl-name">
              <a
                class="row-link"
                :href="rowHref(visibleRows[vr.index])"
                target="_blank"
                rel="noopener"
                @click.stop
              >{{ visibleRows[vr.index].name }}</a>
            </td>

            <td v-else-if="col.cell.type === 'tag'" class="small">
              <span class="tag" :style="{ background: tagColor(visibleRows[vr.index].type) }">
                {{ visibleRows[vr.index].type }}
              </span>
            </td>

            <td v-else-if="col.cell.type === 'muted'" class="small">
              <span class="muted">{{ visibleRows[vr.index][col.cell.field] || '—' }}</span>
            </td>

            <td
              v-else-if="col.cell.type === 'price'"
              class="num"
              :data-v="visibleRows[vr.index].latest || 0"
            >{{ fa(visibleRows[vr.index].latest as number | null) }}</td>

            <td
              v-else-if="col.cell.type === 'pill'"
              class="num"
              :data-v="(visibleRows[vr.index][col.cell.field] ?? -99999) as number"
            >
              <span
                v-if="!pill(visibleRows[vr.index][col.cell.field] as number | null).missing"
                class="pill"
                :class="pill(visibleRows[vr.index][col.cell.field] as number | null).cls"
              >{{ pill(visibleRows[vr.index][col.cell.field] as number | null).text }}</span>
              <span v-else class="muted">—</span>
            </td>

            <td v-else class="chev">›</td>
          </template>
        </tr>

        <tr v-if="padBottom > 0" class="vpad" aria-hidden="true">
          <td :colspan="columns.length" :style="{ height: padBottom + 'px', padding: 0, border: 0 }"></td>
        </tr>
      </tbody>
    </table>
  </div>
  <p v-if="!visibleRows.length" class="muted note">داده‌ای برای نمایش یافت نشد.</p>
</template>
