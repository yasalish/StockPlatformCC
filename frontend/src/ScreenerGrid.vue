<script setup lang="ts">
/**
 * ScreenerGrid.vue — the ranked «غربالگر هوشمند» table, virtualized.
 *
 * One table of every symbol (779 rows × 11 columns as HTML: 1.1 MB and 16,600
 * DOM nodes), so it takes the same treatment as PerfGrid: a window virtualizer
 * over the real page scroll, fixed column widths from <colgroup>, and only the
 * rows on screen in the DOM. The cells reproduce screener.html exactly —
 * score-badge, vbadge, mini-bar and the RSI tone classes.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useWindowVirtualizer } from "@tanstack/vue-virtual";
import type { ScreenerRow } from "./types";
import { fa } from "./format";
import { handledInRow, openDetail } from "./nav";

const props = defineProps<{
  rows: ScreenerRow[];
  kind: "stock" | "etf";
  groupLabel: string;
  etfTypeColors: Record<string, string>;
  watched: Set<string>;
  filterText: string;
  detailBase: string;
}>();

const emit = defineEmits<{ (e: "watch-toggled", key: string, on: boolean): void }>();

type ColId =
  | "rank" | "ticker" | "name" | "group" | "latest" | "score"
  | "verdict" | "trend" | "momentum" | "rsi" | "chev";

interface Col {
  id: ColId;
  label: string;
  /** Measured on the server-rendered page at 1600px, as widths.ts explains. */
  width: number;
  sort: "str" | "num" | null;
  numh?: boolean;
}

const COLS: Col[] = [
  { id: "rank", label: "#", width: 51, sort: null },
  { id: "ticker", label: "نماد", width: 99, sort: "str" },
  { id: "name", label: "نام", width: 198, sort: "str" },
  { id: "group", label: "", width: 179, sort: "str" },
  { id: "latest", label: "قیمت پایانی", width: 88, sort: "num", numh: true },
  { id: "score", label: "امتیاز", width: 79, sort: "num", numh: true },
  { id: "verdict", label: "سیگنال", width: 86, sort: "num" },
  { id: "trend", label: "روند", width: 88, sort: "num", numh: true },
  { id: "momentum", label: "مومنتوم", width: 88, sort: "num", numh: true },
  { id: "rsi", label: "RSI", width: 51, sort: "num", numh: true },
  { id: "chev", label: "", width: 29, sort: null },
];

const sortCol = ref<ColId | null>(null);
const sortDir = ref<1 | -1>(1);
const memory = new Map<ColId, 1 | -1>();

function toggle(id: ColId, sort: string | null) {
  if (!sort) return;
  const next: 1 | -1 = (memory.get(id) ?? 0) === 1 ? -1 : 1;
  memory.set(id, next);
  sortCol.value = id;
  sortDir.value = next;
}

function sortClass(id: ColId) {
  return sortCol.value === id ? (sortDir.value === 1 ? "sorted-asc" : "sorted-desc") : "";
}

function numberOf(r: ScreenerRow, id: ColId): number | null {
  if (id === "verdict") return r.verdict?.score ?? null;
  const v = r[id as "latest" | "score" | "trend" | "momentum" | "rsi"];
  return typeof v === "number" ? v : null;
}

function textOf(r: ScreenerRow, id: ColId): string {
  if (id === "group") return r.group ?? "";
  if (id === "name") return r.name ?? "";
  return r.ticker ?? "";
}

const visibleRows = computed<ScreenerRow[]>(() => {
  const q = props.filterText.trim();
  const out = q ? props.rows.filter((r) => (r.ticker || "").includes(q)) : props.rows.slice();
  const id = sortCol.value;
  if (!id) return out;
  const col = COLS.find((c) => c.id === id)!;
  const dir = sortDir.value;
  if (col.sort === "num") {
    out.sort((a, b) => {
      const va = numberOf(a, id);
      const vb = numberOf(b, id);
      // Missing values last in both directions, as app.js now does.
      if (va === null || vb === null) return va === vb ? 0 : va === null ? 1 : -1;
      return (va - vb) * dir;
    });
  } else {
    out.sort((a, b) => textOf(a, id).localeCompare(textOf(b, id), "fa") * dir);
  }
  return out;
});

/** The «#» column is the row's rank in the CURRENT order, as the Jinja loop was. */
function rankOf(i: number) {
  return i + 1;
}

/* -------------------------------------------------------- virtualization */
const tbodyEl = ref<HTMLElement | null>(null);
const ESTIMATED_ROW = 48;              // measured: 47.8px
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
const totalSize = computed(() => virtualizer.value.getTotalSize());
const minWidth = computed(() => COLS.reduce((n, c) => n + c.width, 0));
const padTop = computed(() =>
  virtualRows.value.length ? virtualRows.value[0].start - scrollMargin.value : 0);
const padBottom = computed(() =>
  virtualRows.value.length
    ? totalSize.value - virtualRows.value[virtualRows.value.length - 1].end
    : 0);

watch(() => visibleRows.value.length, () => {
  virtualizer.value.measure();
  scheduleMeasure();
});

/* -------------------------------------------------------------- watchlist */
function getBN(): { toggleWatch(el: HTMLElement): Promise<void> } | undefined {
  try {
    // eslint-disable-next-line no-undef
    return typeof BN !== "undefined" ? (BN as never) : undefined;
  } catch {
    return undefined;
  }
}

async function onStar(event: MouseEvent, row: ScreenerRow) {
  event.stopPropagation();
  const btn = event.currentTarget as HTMLElement;
  const bn = getBN();
  if (!bn) return;
  await bn.toggleWatch(btn);
  emit("watch-toggled", `${props.kind}:${row.ticker}`, btn.classList.contains("on"));
}

function rowHref(row: ScreenerRow) {
  return `${props.detailBase}${row.id}`;
}

function go(event: MouseEvent, row: ScreenerRow) {
  if (handledInRow(event)) return;
  openDetail(rowHref(row));
}

function barTone(v: number) {
  return v >= 60 ? "t-pos" : v >= 45 ? "t-mid" : "t-neg";
}

function rsiTone(v: number | null) {
  if (v === null) return "";
  return v < 30 ? "down-t" : v > 70 ? "up-t" : "";
}

function tagColor(t: string | null) {
  return (t && props.etfTypeColors[t]) || "#868fa3";
}

defineExpose({ visibleCount: computed(() => visibleRows.value.length) });
</script>

<template>
  <div class="table-scroll">
    <table class="grid sortable grid-virtual" :style="{ minWidth: minWidth + 'px' }">
      <colgroup>
        <col v-for="c in COLS" :key="c.id" :style="{ width: c.width + 'px' }" />
      </colgroup>
      <thead>
        <tr>
          <th
            v-for="c in COLS"
            :key="c.id"
            :class="[c.numh ? 'numh' : '', sortClass(c.id)]"
            :data-sort="c.sort || undefined"
            @click="toggle(c.id, c.sort)"
          >{{ c.id === 'group' ? groupLabel : c.label }}</th>
        </tr>
      </thead>
      <tbody ref="tbodyEl">
        <tr v-if="padTop > 0" class="vpad" aria-hidden="true">
          <td :colspan="COLS.length" :style="{ height: padTop + 'px', padding: 0, border: 0 }"></td>
        </tr>

        <tr
          v-for="vr in virtualRows"
          :key="visibleRows[vr.index].ticker"
          :ref="(el) => virtualizer.measureElement(el as Element)"
          :data-index="vr.index"
          class="clickable"
          :data-ticker="visibleRows[vr.index].ticker"
          @click="go($event, visibleRows[vr.index])"
        >
          <td class="muted">{{ fa(rankOf(vr.index)) }}</td>
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
            >★</button><a
              class="row-link"
              :href="rowHref(visibleRows[vr.index])"
              target="_blank"
              rel="noopener"
              @click.stop
            >{{ visibleRows[vr.index].ticker }}</a>
          </td>
          <td class="rtl-name">
            <a
              class="row-link"
              :href="rowHref(visibleRows[vr.index])"
              target="_blank"
              rel="noopener"
              @click.stop
            >{{ visibleRows[vr.index].name }}</a>
          </td>
          <td class="small">
            <span
              v-if="kind === 'etf'"
              class="tag"
              :style="{ background: tagColor(visibleRows[vr.index].group) }"
            >{{ visibleRows[vr.index].group }}</span>
            <span v-else class="muted">{{ visibleRows[vr.index].group || '—' }}</span>
          </td>
          <td class="num" :data-v="visibleRows[vr.index].latest || 0">
            {{ fa(visibleRows[vr.index].latest) }}
          </td>
          <td class="num" :data-v="visibleRows[vr.index].score">
            <span class="score-badge" :class="visibleRows[vr.index].verdict.tone">
              {{ fa(visibleRows[vr.index].score) }}
            </span>
          </td>
          <td :data-v="visibleRows[vr.index].verdict.score">
            <span class="vbadge" :class="visibleRows[vr.index].verdict.tone">
              {{ visibleRows[vr.index].verdict.label }}
            </span>
          </td>
          <td
            v-for="key in ['trend', 'momentum']"
            :key="key"
            class="num"
            :data-v="(visibleRows[vr.index] as any)[key] ?? -1"
          >
            <span v-if="(visibleRows[vr.index] as any)[key] !== null" class="mini-bar">
              <i
                :class="barTone((visibleRows[vr.index] as any)[key])"
                :style="{ width: (visibleRows[vr.index] as any)[key] + '%' }"
              ></i>
            </span>
            <template v-else>—</template>
          </td>
          <td class="num" :data-v="visibleRows[vr.index].rsi ?? -1">
            <span
              v-if="visibleRows[vr.index].rsi !== null"
              :class="rsiTone(visibleRows[vr.index].rsi)"
            >{{ fa(Math.round(visibleRows[vr.index].rsi as number)) }}</span>
            <template v-else>—</template>
          </td>
          <td class="chev">›</td>
        </tr>

        <tr v-if="padBottom > 0" class="vpad" aria-hidden="true">
          <td :colspan="COLS.length" :style="{ height: padBottom + 'px', padding: 0, border: 0 }"></td>
        </tr>
      </tbody>
    </table>
  </div>
  <p v-if="!visibleRows.length" class="muted note">نمادی با این فیلترها یافت نشد.</p>
</template>
