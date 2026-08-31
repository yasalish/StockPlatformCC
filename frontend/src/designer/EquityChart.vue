<script setup lang="ts">
/**
 * EquityChart.vue — the filter's curve against the market's, in inline SVG.
 *
 * No chart library. The only other drawing on this site is GraphCanvas, which
 * is hand-written SVG for the same reason: two curves over a shared x-axis is
 * about forty lines of path building, and a charting dependency would be larger
 * than the entire designer bundle for an audience on Iranian mobile data.
 *
 * The y-axis is LOGARITHMIC, which for an equity curve is not a stylistic
 * choice. On a linear axis a filter that doubled early and then went sideways
 * looks like it kept climbing, because the same percentage move is drawn taller
 * the higher up it happens; the eye reads slope as performance and gets it
 * wrong. On a log axis equal percentage moves are equal distances, so the two
 * curves can actually be compared by their steepness — which is the one thing
 * anyone looks at this picture to do.
 */
import { computed } from "vue";
import { fa } from "../format";

const props = defineProps<{
  curve: number[];
  bench: number[];
  dates: string[];
}>();

const W = 720;
const H = 240;
const PAD_L = 46;
const PAD_R = 12;
const PAD_T = 14;
const PAD_B = 26;

const bounds = computed(() => {
  const all = [...props.curve, ...props.bench].filter((v) => v > 0);
  if (!all.length) return { lo: 0, hi: 1 };
  const lo = Math.log(Math.min(...all));
  const hi = Math.log(Math.max(...all));
  // A dead-flat curve would divide by zero and draw a line through the axis.
  return hi - lo < 1e-6 ? { lo: lo - 0.05, hi: hi + 0.05 } : { lo, hi };
});

function y(v: number): number {
  const { lo, hi } = bounds.value;
  const t = (Math.log(Math.max(v, 1e-9)) - lo) / (hi - lo);
  return PAD_T + (1 - t) * (H - PAD_T - PAD_B);
}

function x(i: number, n: number): number {
  if (n <= 1) return PAD_L;
  return PAD_L + (i / (n - 1)) * (W - PAD_L - PAD_R);
}

function path(series: number[]): string {
  if (!series.length) return "";
  return series
    .map((v, i) => `${i ? "L" : "M"}${x(i, series.length).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
}

const stratPath = computed(() => path(props.curve));
const benchPath = computed(() => path(props.bench));

/** Gridlines at round multiples — ۱x, ۱.۵x, ۲x… — rather than at equal
 *  pixel steps, so the labels are numbers a reader recognises. */
const ticks = computed(() => {
  const { lo, hi } = bounds.value;
  const min = Math.exp(lo);
  const max = Math.exp(hi);
  const steps = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 5, 7.5, 10, 15, 20, 30, 50];
  const out = steps.filter((s) => s >= min * 0.98 && s <= max * 1.02);
  // Nothing landed in range (a very narrow window): fall back to the ends.
  if (out.length < 2) return [min, max];
  return out;
});

/** A handful of date labels along the bottom — first, last, and two inside. */
const xLabels = computed(() => {
  const n = props.dates.length;
  if (n < 2) return [];
  const at = [0, Math.floor(n / 3), Math.floor((2 * n) / 3), n - 1];
  return [...new Set(at)].map((i) => ({
    i,
    x: x(i, n),
    // «۱۴۰۴-۰۲» — the day is noise at this width.
    label: fa((props.dates[i] ?? "").slice(0, 7)),
  }));
});

const last = computed(() => props.curve[props.curve.length - 1] ?? 1);
const lastBench = computed(() => props.bench[props.bench.length - 1] ?? 1);
</script>

<template>
  <figure class="dz-bt-chart">
    <svg :viewBox="`0 0 ${W} ${H}`" role="img" preserveAspectRatio="xMidYMid meet"
         aria-label="منحنی سرمایه فیلتر در برابر بازار">
      <g class="dz-bt-grid">
        <template v-for="t in ticks" :key="t">
          <line :x1="PAD_L" :x2="W - PAD_R" :y1="y(t)" :y2="y(t)" />
          <text :x="PAD_L - 6" :y="y(t) + 3.5" text-anchor="end">{{ fa(t) }}×</text>
        </template>
      </g>

      <g class="dz-bt-xlab">
        <text v-for="l in xLabels" :key="l.i" :x="l.x" :y="H - 8" text-anchor="middle">
          {{ l.label }}
        </text>
      </g>

      <path class="dz-bt-bench" :d="benchPath" fill="none" />
      <path class="dz-bt-strat" :d="stratPath" fill="none" />
    </svg>

    <figcaption class="dz-bt-legend">
      <span class="dz-bt-key is-strat">فیلتر <b>{{ fa(Math.round(last * 100) / 100) }}×</b></span>
      <span class="dz-bt-key is-bench">بازار <b>{{ fa(Math.round(lastBench * 100) / 100) }}×</b></span>
      <span class="muted small">محور عمودی لگاریتمی است</span>
    </figcaption>
  </figure>
</template>
