<script setup lang="ts">
/**
 * ResultPanel.vue — what the filter found.
 *
 * Reuses the platform's `table.grid` chrome so a designed filter's output looks
 * and behaves like every other list on the site: the same row height, the same
 * sortable headers, the same new-tab click through to the security page
 * (nav.ts's openDetail — a list you have just spent five minutes building must
 * not be thrown away by a click).
 *
 * VIRTUALIZED, for the same reason /screener and /performance are. A designed
 * filter is not a handful of matches: «قیمت پایانی حداکثر ۵٪ زیر سقف یک‌ساله»
 * returns 407 symbols today and the engine will hand back up to 3,000.
 * Rendering all of them is the exact mistake this platform already fixed once —
 * 779 rows of markup was 1.1 MB and 16,600 DOM nodes — so the same window
 * virtualizer over the real page scroll goes here too, with only the rows on
 * screen in the DOM.
 *
 * Unlike ScreenerGrid, whose eleven columns are a constant, this table's columns
 * are only known at run time — «ستون خروجی» lets the user add as many as they
 * like with labels of their own — so the <colgroup> that `table-layout: fixed`
 * needs is computed from the result rather than written down (see `cols`).
 *
 * The «چرا؟» button is the part that does not exist elsewhere. It asks the
 * server for every node's value on that one symbol and hands it back to the
 * page, which paints ✓ / ✕ on the chips of the diagram above the table.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useWindowVirtualizer } from "@tanstack/vue-virtual";
import { fa } from "../format";
import { handledInRow, openDetail } from "../nav";
import type { RunResult, RunRow } from "./graph";

const props = defineProps<{
  result: RunResult | null;
  detailBase: string;
  busy: boolean;
  inspected: string;
  /** The filter's name — the heading, and the downloaded file's name. */
  title?: string;
}>();

const emit = defineEmits<{ (e: "explain", ticker: string): void }>();

/* ------------------------------------------------------------------- sort */
const sortKey = ref<string>("");
const sortDir = ref<1 | -1>(-1);

function sortBy(key: string) {
  if (sortKey.value === key) sortDir.value = sortDir.value === 1 ? -1 : 1;
  else {
    sortKey.value = key;
    // Names sort A→Z, numbers big→small: the first click should land on the
    // interesting end of whichever column it is.
    sortDir.value = key === "ticker" || key === "name" || key === "group" ? 1 : -1;
  }
}

function sortClass(key: string) {
  return sortKey.value === key ? (sortDir.value === 1 ? "sorted-asc" : "sorted-desc") : "";
}

const rows = computed<RunRow[]>(() => {
  const list = [...(props.result?.rows ?? [])];
  const k = sortKey.value;
  if (!k) return list;
  const dir = sortDir.value;
  return list.sort((a, b) => {
    const av = k.startsWith("c:") ? a.vals[k.slice(2)] : (a as never)[k];
    const bv = k.startsWith("c:") ? b.vals[k.slice(2)] : (b as never)[k];
    // Missing values sink whichever way the column points — an em dash at the
    // top of "best first" is never what was asked for.
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    if (typeof av === "string") return String(av).localeCompare(String(bv), "fa") * dir;
    return ((av as number) - (bv as number)) * dir;
  });
});

/* ------------------------------------------------------------ virtualizer */
const tbodyEl = ref<HTMLElement | null>(null);
const ESTIMATED_ROW = 44;
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
  // Repeated passes: the fonts land, and the diagram panel above this table can
  // open and close. Both move the table's top edge, which is the offset the
  // whole virtualization is measured against.
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
    count: rows.value.length,
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

/** Sorting, re-running or re-scoping replaces the row set under the virtualizer. */
watch([() => rows.value.length, () => props.result, sortKey, sortDir], () => {
  virtualizer.value.measure();
  scheduleMeasure();
});

const colCount = computed(() => 5 + (props.result?.columns.length ?? 0));

/**
 * Column widths, for the <colgroup> that `table-layout: fixed` requires.
 *
 * Fixed layout is not optional for a virtualized table — only a handful of rows
 * are in the DOM at a time, so automatic layout would size the columns from that
 * subset and re-size them on every scroll. But unlike every other grid on this
 * platform these columns are not known in advance: «ستون خروجی» lets the user
 * add as many as they like, with labels of their own. So the fixed four are
 * measured and each user column is sized from the length of the name they gave
 * it, clamped so one very long label cannot squeeze the symbol off the screen.
 */
const WIDTHS = { ticker: 110, name: 250, group: 220, latest: 110, why: 78 };

const cols = computed(() => {
  const custom = (props.result?.columns ?? []).map((c) => {
    let px = 34;
    for (const ch of c.label) px += ch.charCodeAt(0) > 0x600 ? 9 : 7.6;
    return { key: c.id, width: Math.min(210, Math.max(96, Math.round(px))) };
  });
  return [
    { key: "ticker", width: WIDTHS.ticker },
    { key: "name", width: WIDTHS.name },
    { key: "group", width: WIDTHS.group },
    { key: "latest", width: WIDTHS.latest },
    ...custom,
    { key: "why", width: WIDTHS.why },
  ];
});

const minWidth = computed(() => cols.value.reduce((n, c) => n + c.width, 0));

/* ------------------------------------------------------------------ cells */
/**
 * A cell, rounded to the column's own precision.
 *
 * Not every column is a number any more: «برچسب سیگنال» fills one with «خرید»
 * or «فروش», and rounding a string produces NaN — which would have printed as
 * an em dash on every row that actually had a signal.
 */
function value(v: number | string | null | undefined, digits: number): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "string") return v;
  return fa(Math.round(v * 10 ** digits) / 10 ** digits);
}

/** Text columns are read, not compared — they line up with the names beside
 *  them rather than with the numbers. */
function cellClass(c: { type?: string }) {
  return c.type === "text" ? "rtl-name" : "num";
}

function href(id: number) {
  return `${props.detailBase}${id}`;
}

function rowClick(ev: MouseEvent, id: number) {
  if (handledInRow(ev)) return;
  openDetail(href(id));
}

/* ---------------------------------------------------------------- exports */
/** Copy the whole match list — the reason most people run a screener is to
 *  paste the tickers into a broker's watchlist. */
const copied = ref(false);
async function copyTickers() {
  const text = rows.value.map((r) => r.ticker).join("\n");
  try {
    await navigator.clipboard.writeText(text);
    copied.value = true;
    window.setTimeout(() => (copied.value = false), 1600);
  } catch {
    /* a browser that refuses the clipboard is not an error worth a dialog */
  }
}

/**
 * The table as a CSV, including the user's own «ستون خروجی» columns.
 *
 * Two details this file would be useless without on the audience's machines:
 * a UTF-8 BOM, because Excel on Windows reads a BOM-less UTF-8 CSV as the
 * system codepage and turns every Persian name into mojibake; and LATIN digits
 * in the numbers, because the Persian digits the page displays are text to a
 * spreadsheet and would arrive as unsortable strings.
 */
function downloadCsv() {
  const r = props.result;
  if (!r || !r.rows.length) return;
  const head = ["نماد", "نام", "گروه", "قیمت پایانی", ...r.columns.map((c) => c.label)];
  const cell = (v: string | number | null | undefined) => {
    const t = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };
  const lines = [head.map(cell).join(",")];
  for (const row of rows.value) {
    lines.push(
      [
        cell(row.ticker),
        cell(row.name),
        cell(row.group),
        cell(row.latest),
        ...r.columns.map((c) => {
          const v = row.vals[c.id];
          if (v === null || v === undefined) return cell("");
          // Latin digits for the numbers so a spreadsheet can sort them; the
          // text columns go out exactly as they read on screen.
          return cell(typeof v === "string" ? v : Math.round(v * 10 ** c.digits) / 10 ** c.digits);
        }),
      ].join(","),
    );
  }
  const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${(props.title || "filter").replace(/[\\/:*?"<>|]/g, "-")}-${r.as_of ?? ""}.csv`;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
</script>

<template>
  <section class="panel dz-results">
    <div class="panel-head">
      <!-- The page already says «نتیجهٔ فیلتر» in its <h1>; repeating it here
           wastes the one line that could name WHICH filter these rows are. -->
      <h2>
        {{ title || "نتیجهٔ فیلتر" }}
        <span v-if="result" class="dz-count">
          {{ fa(result.count) }} نماد از {{ fa(result.scanned) }}
        </span>
      </h2>
      <div class="head-actions">
        <span v-if="result" class="muted small">
          تاریخ {{ result.as_of }} · {{ fa(result.bars) }} کندل ·
          {{ fa(Math.round(result.server_ms)) }} میلی‌ثانیه
        </span>
        <button v-if="result && result.rows.length" type="button" class="btn btn-sm" @click="copyTickers">
          {{ copied ? "کپی شد ✓" : "کپی نمادها" }}
        </button>
        <button v-if="result && result.rows.length" type="button" class="btn btn-sm" @click="downloadCsv">
          خروجی CSV
        </button>
      </div>
    </div>

    <p v-if="busy" class="muted note dz-pad">در حال اجرای فیلتر روی همهٔ نمادها…</p>

    <template v-else-if="result">
      <!-- The read window hit its ceiling: a monthly 200-period average wants
           more than four thousand sessions and gets 1300, so every value it
           produced is still warming up. Without this the page shows an empty
           table that is indistinguishable from a strict filter. -->
      <p v-if="result.clipped" class="flash warn dz-pad">
        ⚠ این گراف به تاریخچه‌ای بیشتر از حداکثر بازهٔ خواندنی نیاز دارد — دورهٔ
        اندیکاتورها را کم کنید یا تایم‌فریم کوتاه‌تری بگذارید، وگرنه بخشی از
        محاسبه‌ها هنوز در حال گرم شدن است.
      </p>
      <p v-if="!result.rows.length" class="muted note dz-pad">
        <template v-if="result.errors">
          ⚠ هیچ نمادی پیدا نشد و {{ fa(result.errors) }} نماد اصلاً بررسی نشد — این
          نشانهٔ خطا در یکی از جعبه‌هاست، نه سخت‌گیری فیلتر.
        </template>
        <template v-else>
          هیچ نمادی با این شرط‌ها پیدا نشد. شرط‌ها را کمی ساده‌تر کنید، یا در نود «خروجی
          فیلتر» مقدار «در N کندل اخیر» را بالا ببرید.
        </template>
      </p>
      <template v-else>
        <p v-if="result.truncated" class="muted small note dz-pad">
          فهرست به {{ fa(result.rows.length) }} ردیف نخست کوتاه شده است.
        </p>
        <!-- A symbol the engine could not evaluate is dropped silently from the
             scan, which looks exactly like a strict filter. If any were, say so:
             the count is a defect report, not a detail. -->
        <p v-if="result.errors" class="flash warn dz-pad">
          ⚠ {{ fa(result.errors) }} نماد به‌دلیل خطای محاسبه بررسی نشد. نتیجه ممکن است
          ناقص باشد.
        </p>
        <div class="table-scroll">
          <table
            class="grid sortable grid-virtual dzr-table"
            :style="{ minWidth: minWidth + 'px' }"
          >
            <colgroup>
              <col v-for="c in cols" :key="c.key" :style="{ width: c.width + 'px' }" />
            </colgroup>
            <thead>
              <tr>
                <th class="sortable" :class="sortClass('ticker')" @click="sortBy('ticker')">نماد</th>
                <th class="sortable" :class="sortClass('name')" @click="sortBy('name')">نام</th>
                <th class="sortable" :class="sortClass('group')" @click="sortBy('group')">گروه</th>
                <th class="numh sortable" :class="sortClass('latest')" @click="sortBy('latest')">
                  قیمت پایانی
                </th>
                <th
                  v-for="c in result.columns"
                  :key="c.id"
                  class="sortable"
                  :class="[sortClass(`c:${c.id}`), c.type === 'text' ? '' : 'numh']"
                  @click="sortBy(`c:${c.id}`)"
                >
                  {{ c.label }}
                </th>
                <th class="dz-why-h"></th>
              </tr>
            </thead>
            <tbody ref="tbodyEl">
              <tr v-if="padTop > 0" class="vpad" aria-hidden="true">
                <td :colspan="colCount" :style="{ height: padTop + 'px', padding: 0, border: 0 }"></td>
              </tr>

              <tr
                v-for="vr in virtualRows"
                :key="rows[vr.index].ticker"
                :ref="(el) => virtualizer.measureElement(el as Element)"
                :data-index="vr.index"
                :data-ticker="rows[vr.index].ticker"
                class="clickable"
                :class="{ 'is-open': inspected === rows[vr.index].ticker }"
                @click="rowClick($event, rows[vr.index].id)"
              >
                <td class="mono">
                  <a class="row-link" :href="href(rows[vr.index].id)" target="_blank" rel="noopener">
                    {{ rows[vr.index].ticker }}
                  </a>
                </td>
                <td class="rtl-name">{{ rows[vr.index].name }}</td>
                <td class="rtl-name">{{ rows[vr.index].group || "—" }}</td>
                <td class="num">{{ fa(rows[vr.index].latest) }}</td>
                <td v-for="c in result.columns" :key="c.id" :class="cellClass(c)">
                  {{ value(rows[vr.index].vals[c.id], c.digits) }}
                </td>
                <td class="dz-why-c">
                  <button
                    type="button"
                    class="btn btn-sm btn-ghost"
                    title="مقدار هر جعبه برای این نماد"
                    @click.stop="emit('explain', rows[vr.index].ticker)"
                  >
                    چرا؟
                  </button>
                </td>
              </tr>

              <tr v-if="padBottom > 0" class="vpad" aria-hidden="true">
                <td :colspan="colCount" :style="{ height: padBottom + 'px', padding: 0, border: 0 }"></td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>

    <p v-else class="muted note dz-pad">
      فیلتر را بسازید و دکمهٔ «اجرا» را بزنید تا نتیجه اینجا بیاید.
    </p>
  </section>
</template>
