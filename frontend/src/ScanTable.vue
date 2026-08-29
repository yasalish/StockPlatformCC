<script setup lang="ts">
/**
 * ScanTable.vue — one section's table of matching symbols.
 *
 * Not virtualized, unlike PerfGrid: these tables are 3-700 rows and there are
 * many of them, so the cost is not any single table but rendering all sixteen
 * before the reader has scrolled to the second one. ScanSection.vue mounts this
 * only when the section comes near the viewport; once mounted, every row is
 * present so sorting and Ctrl+F behave exactly as they did.
 *
 * Markup matches the Jinja `match_row` macro class for class, so
 * static/css/style.css needs nothing new.
 */
import { computed, ref } from "vue";
import type { ScanRow } from "./types";
import { fa } from "./format";
import { handledInRow, openDetail } from "./nav";

const props = defineProps<{
  rows: ScanRow[];
  detailBase: string;
  /** strategies' ⭐ picks table adds two columns. */
  picks?: boolean;
  stratNames?: Record<string, string>;
}>();

type SortId = "ticker" | "name" | "group" | "latest" | "rsi" | "score";
const sortId = ref<SortId | null>(null);
const sortDir = ref<1 | -1>(1);
const memory = new Map<SortId, 1 | -1>();

function toggle(id: SortId) {
  const next: 1 | -1 = (memory.get(id) ?? 0) === 1 ? -1 : 1;
  memory.set(id, next);
  sortId.value = id;
  sortDir.value = next;
}

function cls(id: SortId) {
  return sortId.value === id ? (sortDir.value === 1 ? "sorted-asc" : "sorted-desc") : "";
}

const NUMERIC: SortId[] = ["latest", "rsi", "score"];

const sorted = computed(() => {
  const id = sortId.value;
  if (!id) return props.rows;
  const dir = sortDir.value;
  const out = props.rows.slice();
  if (NUMERIC.includes(id)) {
    out.sort((a, b) => {
      // Missing values sink to the bottom in BOTH directions — the same rule
      // BN.initTable follows since the sorting fix in static/js/app.js.
      const va = a[id] as number | null | undefined;
      const vb = b[id] as number | null | undefined;
      const na = va === null || va === undefined;
      const nb = vb === null || vb === undefined;
      if (na || nb) return na && nb ? 0 : na ? 1 : -1;
      return ((va as number) - (vb as number)) * dir;
    });
  } else {
    out.sort((a, b) =>
      String(a[id] ?? "").localeCompare(String(b[id] ?? ""), "fa") * dir);
  }
  return out;
});

function rsiClass(v: number | null | undefined) {
  if (v === null || v === undefined) return "";
  return v < 30 ? "down-t" : v > 70 ? "up-t" : "";
}

function rowHref(row: ScanRow) {
  return `${props.detailBase}${row.id}`;
}

function go(event: MouseEvent, row: ScanRow) {
  if (handledInRow(event)) return;
  openDetail(rowHref(row));
}
</script>

<template>
  <div class="table-scroll">
    <table class="grid sortable">
      <thead>
        <tr>
          <th data-sort="str" :class="cls('ticker')" @click="toggle('ticker')">نماد</th>
          <th data-sort="str" :class="cls('name')" @click="toggle('name')">نام</th>
          <th data-sort="str" :class="cls('group')" @click="toggle('group')">گروه/نوع</th>
          <th data-sort="num" :class="cls('latest')" @click="toggle('latest')">قیمت پایانی</th>
          <th data-sort="num" class="numh" :class="cls('rsi')" @click="toggle('rsi')">RSI</th>
          <th v-if="picks" data-sort="num" class="numh" :class="cls('score')" @click="toggle('score')">تعداد سیگنال</th>
          <th v-if="picks">استراتژی‌ها</th>
          <th v-else></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="r in sorted"
          :key="r.id"
          class="clickable"
          :data-ticker="r.ticker"
          @click="go($event, r)"
        >
          <td class="sym">
            <a class="row-link" :href="rowHref(r)" target="_blank" rel="noopener"
               @click.stop>{{ r.ticker }}</a>
          </td>
          <td class="rtl-name">
            <a class="row-link" :href="rowHref(r)" target="_blank" rel="noopener"
               @click.stop>{{ r.name }}</a>
          </td>
          <td class="small"><span class="muted">{{ r.group || '—' }}</span></td>
          <td class="num" :data-v="r.latest || 0">{{ fa(r.latest) }}</td>
          <td class="num" :data-v="r.rsi ?? -1">
            <span v-if="r.rsi !== null && r.rsi !== undefined" :class="rsiClass(r.rsi)">
              {{ fa(Math.round(r.rsi)) }}
            </span>
            <template v-else>—</template>
          </td>
          <td v-if="picks" class="num" :data-v="r.score"><b class="up-t">{{ fa(r.score ?? null) }}</b></td>
          <td v-if="picks" class="small">
            <span
              v-for="k in r.signals || []"
              :key="k"
              class="tag"
              style="background:#1a9d63"
            >{{ (stratNames || {})[k] || k }}</span>
          </td>
          <td v-else class="chev">›</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
