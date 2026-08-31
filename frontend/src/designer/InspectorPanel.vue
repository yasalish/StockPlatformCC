<script setup lang="ts">
/**
 * InspectorPanel.vue — the parameters of the selected chip.
 *
 * Editing happens here rather than on the chip itself. The reference product
 * puts the period straight into the caption («Ichi ۹,۲۶,۵۲») and edits it in a
 * side panel, and that split is right: the caption has to stay short enough to
 * read at 40 % zoom, while `MACD` alone has four parameters that each need a
 * label to be meaningful.
 *
 * Also the debugger. When a result row is opened, the last twelve bars of every
 * one of this node's outputs are printed underneath — the numbers behind the ✓
 * or ✕ on the chip.
 */
import { computed } from "vue";
import { chipTitle, type Explain, type GNode, type NodeSpec } from "./graph";
import { fa } from "../format";

const props = defineProps<{
  node: GNode | null;
  spec: NodeSpec | null;
  explain: Explain | null;
  count: number;
}>();

const emit = defineEmits<{
  (e: "change"): void;
  (e: "delete"): void;
  (e: "duplicate"): void;
}>();

const title = computed(() =>
  props.node && props.spec ? chipTitle(props.spec, props.node) : "",
);

const TEXTUAL = new Set(["select", "text", "textarea"]);

function set(id: string, value: string, type: string) {
  if (!props.node) return;
  props.node.params[id] = TEXTUAL.has(type) ? value : Number(value);
  emit("change");
}

/** Clamp on blur, not on keystroke: clamping while typing makes "200" pass
 *  through "2" and jump to the minimum before the user has finished. */
function clamp(p: { id: string; min?: number; max?: number; type: string; default: number | string }) {
  if (!props.node || (p.type !== "int" && p.type !== "float")) return;
  let v = Number(props.node.params[p.id]);
  if (!Number.isFinite(v)) v = Number(p.default);
  if (p.min !== undefined) v = Math.max(p.min, v);
  if (p.max !== undefined) v = Math.min(p.max, v);
  if (p.type === "int") v = Math.round(v);
  props.node.params[p.id] = v;
  emit("change");
}

const tails = computed(() => {
  if (!props.node || !props.spec || !props.explain) return [];
  return props.spec.outputs
    .map((o) => ({ port: o, val: props.explain!.ports[`${props.node!.id}:${o.id}`] }))
    .filter((r) => r.val);
});

function cell(v: number | boolean | null): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "✓" : "✕";
  const a = Math.abs(v);
  return fa(a >= 1000 ? Math.round(v) : Math.round(v * 100) / 100);
}
</script>

<template>
  <aside class="dz-inspector">
    <template v-if="node && spec">
      <div class="dz-insp-head">
        <div>
          <div class="dz-insp-title">{{ spec.label }}</div>
          <code class="dz-insp-expr">{{ title }}</code>
        </div>
        <div class="dz-insp-acts">
          <button type="button" class="btn btn-sm" title="تکثیر (Ctrl+D)" @click="emit('duplicate')">تکثیر</button>
          <button type="button" class="btn btn-sm btn-danger" title="حذف (Delete)" @click="emit('delete')">حذف</button>
        </div>
      </div>

      <p v-if="spec.help" class="dz-insp-help muted small">{{ spec.help }}</p>

      <div v-if="spec.params.length" class="dz-fields">
        <label v-for="p in spec.params" :key="p.id" class="dz-field">
          <span>{{ p.label }}</span>
          <select
            v-if="p.type === 'select'"
            :value="node.params[p.id]"
            @change="set(p.id, ($event.target as HTMLSelectElement).value, p.type)"
          >
            <option v-for="o in p.options" :key="o.v" :value="o.v">{{ o.l }}</option>
          </select>
          <textarea
            v-else-if="p.type === 'textarea'"
            class="dz-area"
            rows="3"
            :value="node.params[p.id] as string"
            maxlength="240"
            spellcheck="false"
            dir="auto"
            @input="set(p.id, ($event.target as HTMLTextAreaElement).value, p.type)"
          ></textarea>
          <input
            v-else-if="p.type === 'text'"
            type="text"
            :value="node.params[p.id]"
            maxlength="120"
            dir="auto"
            @input="set(p.id, ($event.target as HTMLInputElement).value, p.type)"
          />
          <input
            v-else
            type="number"
            :value="node.params[p.id]"
            :min="p.min"
            :max="p.max"
            :step="p.type === 'int' ? 1 : (p.step ?? 0.1)"
            @input="set(p.id, ($event.target as HTMLInputElement).value, p.type)"
            @blur="clamp(p)"
          />
        </label>
      </div>
      <p v-else class="muted small note">این جعبه تنظیمی ندارد.</p>

      <div v-if="tails.length" class="dz-tails">
        <div class="dz-tails-head">
          مقدار برای «{{ explain?.ticker }}» — {{ fa(explain?.bars) }} کندل آخر
          <span v-if="explain && explain.at > 0" class="muted small">
            · شرط {{ fa(explain.at) }} کندل پیش برقرار شده است (خانهٔ نشان‌دار)
          </span>
        </div>
        <div v-for="t in tails" :key="t.port.id" class="dz-tail">
          <span class="dz-tail-l">{{ t.port.label || "خروجی" }}</span>
          <span v-if="t.val!.kind === 'const'" class="dz-tail-v">{{ fa((t.val as any).value) }}</span>
          <span v-else-if="t.val!.kind === 'text'" class="dz-tail-v">{{ (t.val as any).value || "—" }}</span>
          <div v-else class="dz-tail-row">
            <b
              v-for="(v, i) in (t.val as any).tail"
              :key="i"
              :class="[
                typeof v === 'boolean' ? (v ? 'is-on' : 'is-off') : '',
                i === (t.val as any).tail.length - 1 - (explain?.at ?? 0) ? 'is-at' : '',
              ]"
              >{{ cell(v) }}</b
            >
          </div>
        </div>
      </div>
    </template>

    <template v-else>
      <div class="dz-insp-empty">
        <p class="muted small">
          <template v-if="count > 1">{{ fa(count) }} جعبه انتخاب شده است. برای دیدن تنظیمات، فقط یکی را انتخاب کنید.</template>
          <template v-else>روی یک جعبه کلیک کنید تا تنظیماتش اینجا باز شود.</template>
        </p>
        <ul class="dz-keys muted small">
          <li><kbd>Delete</kbd> حذف انتخاب‌شده‌ها</li>
          <li><kbd>Ctrl</kbd>+<kbd>D</kbd> تکثیر</li>
          <li><kbd>Shift</kbd>+کشیدن روی بوم انتخاب گروهی</li>
          <li><kbd>Ctrl</kbd>+<kbd>Enter</kbd> اجرای فیلتر</li>
          <li>چرخ ماوس بزرگ‌نمایی · کشیدن بوم جابه‌جایی</li>
        </ul>
      </div>
    </template>
  </aside>
</template>
