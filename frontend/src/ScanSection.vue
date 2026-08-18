<script setup lang="ts">
/**
 * ScanSection.vue — one filter / strategy, with its table mounted on demand.
 *
 * The header, the description and the match count are always rendered: they are
 * a few nodes each and they are what the reader scans down the page for. The
 * table is created only once the section comes within a screen of the viewport,
 * which is what takes /filters and /strategies from 23,000 and 38,000 DOM nodes
 * to a few hundred on load. Scrolling materialises sections as they arrive and
 * they then stay — so a section already read does not flicker on the way back
 * up, and Ctrl+F finds what has been seen.
 */
import { onBeforeUnmount, onMounted, ref } from "vue";
import ScanTable from "./ScanTable.vue";
import type { ScanRow } from "./types";
import { fa } from "./format";

const props = defineProps<{
  title: string;
  desc?: string;
  source?: string;
  /** ▲ / ▼ / ◆ badge colour for the filters page; omitted for strategies. */
  badge?: { text: string; color: string } | null;
  rows: ScanRow[];
  detailBase: string;
  picks?: boolean;
  stratNames?: Record<string, string>;
}>();

const host = ref<HTMLElement | null>(null);
const shown = ref(false);
let observer: IntersectionObserver | null = null;

onMounted(() => {
  if (!props.rows.length) return;
  if (!("IntersectionObserver" in window)) {   // no support → render everything
    shown.value = true;
    return;
  }
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        shown.value = true;
        observer?.disconnect();
        observer = null;
      }
    },
    { rootMargin: "800px 0px" },               // a screen of warning, so it is
  );                                           // already there when you arrive
  if (host.value) observer.observe(host.value);
});

onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <section ref="host" class="panel">
    <div class="panel-head">
      <h2>
        <span v-if="badge" class="tag" :style="{ background: badge.color }">{{ badge.text }}</span>
        {{ title }} <span class="muted small">({{ fa(rows.length) }} نماد)</span>
      </h2>
      <a v-if="source" class="muted small" :href="source" target="_blank" rel="noopener">منبع ↗</a>
    </div>
    <p v-if="desc" class="muted small note" style="margin:0 0 12px">{{ desc }}</p>

    <ScanTable
      v-if="rows.length && shown"
      :rows="rows"
      :detail-base="detailBase"
      :picks="picks"
      :strat-names="stratNames"
    />
    <!-- A placeholder the size the table will be, so the document height is
         right from the first paint: the scrollbar must not lie, or dragging it
         to the bottom lands somewhere that keeps moving as sections mount.
         40px is the measured row height of these grids, 44px the header. -->
    <div
      v-else-if="rows.length"
      :style="{ height: rows.length * 40 + 44 + 'px' }"
      aria-hidden="true"
    ></div>
    <p v-else class="muted note">نمادی یافت نشد.</p>
  </section>
</template>
