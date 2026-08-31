<script setup lang="ts">
/**
 * NodeChip.vue — one box on the canvas.
 *
 * The shape is the reference product's: a coloured pill carrying the expression
 * («close-1», «a > b», «SMA ۲۰۰ final»), the category name in grey underneath,
 * input dots down the left edge and output dots down the right. The category
 * caption is not decoration — it is the only thing that distinguishes an `a > b`
 * comparison from an `a > b` you meant to make a crossing, and reading a graph
 * of thirty chips without it is guesswork.
 */
import { computed } from "vue";
import { chipTitle, clipCaption, portY, valueAt, type Explain, type GNode, type NodeSpec, type Size } from "./graph";
import { fa } from "../format";

const props = defineProps<{
  node: GNode;
  spec: NodeSpec;
  size: Size;
  color: string;
  selected: boolean;
  explain: Explain | null;
  wiring: boolean;
  readonly?: boolean;
}>();

const emit = defineEmits<{
  (e: "grab", ev: PointerEvent): void;
  (e: "out-down", payload: { ev: PointerEvent; port: string }): void;
  (e: "in-down", payload: { ev: PointerEvent; port: string }): void;
  (e: "in-up", payload: { ev: PointerEvent; port: string }): void;
  (e: "open"): void;
}>();

const full = computed(() => chipTitle(props.spec, props.node));
const title = computed(() => clipCaption(full.value));

/** «توضیحات» is not a block that computes something badly — it is a note, and
 *  drawing it as a coloured pill with a category caption under it would put a
 *  box on the canvas that looks exactly like the ones that do the work. */
const isNote = computed(() => props.spec.type === "note");

/** The value this chip produced for the inspected symbol, if one is loaded. */
function portValue(port: string) {
  return props.explain?.ports?.[`${props.node.id}:${port}`] ?? null;
}

/** The badge shown on the chip while «چرا این نماد آمد؟» is open. */
const verdict = computed(() => {
  const out = props.spec.outputs[0];
  if (!out || !props.explain) return null;
  const v = portValue(out.id);
  if (!v) return null;
  const at = props.explain.at ?? 0;
  if (v.kind === "bool") {
    const b = valueAt(v.tail, at);
    return {
      tone: b === true ? "on" : b === false ? "off" : "na",
      text: b === true ? "✓" : b === false ? "✕" : "—",
    };
  }
  if (v.kind === "const") return { tone: "num", text: fa(v.value) };
  if (v.kind === "text") return { tone: "num", text: v.value || "—" };
  const n = valueAt(v.tail, at) ?? [...v.tail].reverse().find((x) => x !== null);
  return { tone: "num", text: n === undefined || n === null ? "—" : fa(round(n)) };
});

/** Four significant-ish digits — a chip is not a table cell. */
function round(v: number): number {
  const a = Math.abs(v);
  if (a >= 1000) return Math.round(v);
  if (a >= 1) return Math.round(v * 100) / 100;
  return Math.round(v * 10000) / 10000;
}
</script>

<template>
  <div
    class="dz-node"
    :class="{ 'is-sel': selected, 'is-wiring': wiring, 'is-ro': readonly, 'is-note': isNote }"
    :style="{ left: `${node.x}px`, top: `${node.y}px`, width: `${size.w}px`, height: `${size.h}px` }"
    @pointerdown="readonly || emit('grab', $event)"
    @dblclick.stop="readonly || emit('open')"
  >
    <div
      class="dz-chip"
      :class="{ 'is-note': isNote }"
      :style="{ background: color }"
      :title="isNote ? full : spec.help || spec.label"
    >
      <span class="dz-chip-t">{{ title }}</span>
    </div>

    <span v-if="!isNote" class="dz-cat">{{ spec.label }}</span>

    <span v-if="verdict" class="dz-verdict" :class="`is-${verdict.tone}`">{{ verdict.text }}</span>

    <!-- inputs -->
    <span
      v-for="(p, i) in spec.inputs"
      :key="`i-${p.id}`"
      class="dz-port dz-in"
      :class="{ 'is-multi': p.multi }"
      :data-in-port="p.id"
      :data-node="node.id"
      :style="{ top: `${portY(size.h, i, spec.inputs.length)}px` }"
      :title="p.label ? `ورودی ${p.label}` : 'ورودی'"
      @pointerdown="readonly || emit('in-down', { ev: $event, port: p.id })"
      @pointerup="readonly || emit('in-up', { ev: $event, port: p.id })"
    >
      <i class="dz-dot"></i>
      <b v-if="p.label" class="dz-plabel">{{ p.label }}</b>
    </span>

    <!-- outputs -->
    <span
      v-for="(p, i) in spec.outputs"
      :key="`o-${p.id}`"
      class="dz-port dz-out"
      :style="{ top: `${portY(size.h, i, spec.outputs.length)}px` }"
      :title="p.label ? `خروجی ${p.label}` : 'خروجی'"
      @pointerdown="readonly || emit('out-down', { ev: $event, port: p.id })"
    >
      <i class="dz-dot"></i>
      <b v-if="p.label" class="dz-plabel">{{ p.label }}</b>
    </span>
  </div>
</template>
