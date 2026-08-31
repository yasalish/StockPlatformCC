<script setup lang="ts">
/**
 * PalettePanel.vue — the box of parts, on the right (this page is RTL).
 *
 * TWO LEVELS, not one. With seventy blocks a flat «اندیکاتور» shelf is a wall
 * of thirty names, and what a user is actually looking for — «یک میانگین
 * متحرک», «چیزی برای حجم» — is a shelf, not a search term. Each node carries a
 * `sub` from the catalogue (filter_engine.NODE_TYPES) and the second level is
 * built from it, so adding a block never means editing this file.
 *
 * Colour is inherited from the CATEGORY, not the sub-group: the palette and the
 * canvas have to use one visual language, and a yellow chip on the board must
 * come from the yellow shelf whichever sub-shelf it sat on.
 *
 * The search box matches the Persian label AND the node type, because the
 * people who will use this know «RSI» and «MACD» by their Latin names and
 * «مقایسه» by its Persian one, often in the same sentence. While searching, the
 * sub-levels are flattened — you are hunting a name, not browsing a shelf.
 *
 * A part reaches the board two ways: dragged, or clicked. Click-to-add exists
 * because dragging is the one interaction that does not work on a touch screen.
 */
import { computed, ref } from "vue";
import type { Category, NodeSpec } from "./graph";

const props = defineProps<{ nodes: NodeSpec[]; categories: Category[] }>();
const emit = defineEmits<{ (e: "add", type: string): void }>();

const q = ref("");
const closedCats = ref<Set<string>>(new Set());
const closedSubs = ref<Set<string>>(new Set());

const needle = computed(() => q.value.trim().toLowerCase());
const searching = computed(() => needle.value.length > 0);

function matches(n: NodeSpec) {
  const s = needle.value;
  if (!s) return true;
  return (
    n.label.toLowerCase().includes(s) ||
    n.type.includes(s) ||
    n.title.toLowerCase().includes(s) ||
    (n.sub ?? "").toLowerCase().includes(s)
  );
}

interface Shelf {
  cat: Category;
  count: number;
  subs: { key: string; label: string; items: NodeSpec[] }[];
}

const shelves = computed<Shelf[]>(() =>
  props.categories
    .map((cat) => {
      const mine = props.nodes.filter((n) => n.cat === cat.key && matches(n));
      const subs: Shelf["subs"] = [];
      for (const n of mine) {
        const label = n.sub || cat.label;
        let bucket = subs.find((b) => b.label === label);
        if (!bucket) {
          bucket = { key: `${cat.key}/${label}`, label, items: [] };
          subs.push(bucket);
        }
        bucket.items.push(n);
      }
      return { cat, count: mine.length, subs };
    })
    .filter((s) => s.count > 0),
);

/** A single sub-shelf whose name just repeats the category is noise. */
function showSubs(s: Shelf) {
  return !searching.value && !(s.subs.length === 1 && s.subs[0].label === s.cat.label);
}

/** Vue unwraps a ref inside the template, so these take the key and touch the
 *  ref themselves rather than being handed one. */
function toggleCat(key: string) {
  const next = new Set(closedCats.value);
  next.has(key) ? next.delete(key) : next.add(key);
  closedCats.value = next;
}

function toggleSub(key: string) {
  const next = new Set(closedSubs.value);
  next.has(key) ? next.delete(key) : next.add(key);
  closedSubs.value = next;
}

function onDragStart(ev: DragEvent, type: string) {
  ev.dataTransfer?.setData("text/bn-node", type);
  if (ev.dataTransfer) ev.dataTransfer.effectAllowed = "copy";
}
</script>

<template>
  <aside class="dz-palette">
    <div class="dz-search">
      <input v-model="q" type="search" placeholder="جستجوی جعبه… (RSI، حجم، کندل)" aria-label="جستجوی جعبه" />
    </div>

    <div class="dz-shelves">
      <section v-for="s in shelves" :key="s.cat.key" class="dz-shelf">
        <button type="button" class="dz-shelf-head" @click="toggleCat(s.cat.key)">
          <i class="dz-swatch" :style="{ background: s.cat.color }"></i>
          <span>{{ s.cat.label }}</span>
          <em>{{ s.count }}</em>
          <span class="dz-caret" :class="{ 'is-closed': closedCats.has(s.cat.key) }">▾</span>
        </button>

        <div v-if="!closedCats.has(s.cat.key)" class="dz-shelf-body">
          <template v-for="sub in s.subs" :key="sub.key">
            <button
              v-if="showSubs(s)"
              type="button"
              class="dz-subhead"
              @click="toggleSub(sub.key)"
            >
              <span class="dz-caret" :class="{ 'is-closed': closedSubs.has(sub.key) }">▾</span>
              <span>{{ sub.label }}</span>
              <em>{{ sub.items.length }}</em>
            </button>
            <div v-if="!showSubs(s) || !closedSubs.has(sub.key)" class="dz-subbody">
              <button
                v-for="n in sub.items"
                :key="n.type"
                type="button"
                class="dz-part"
                draggable="true"
                :title="n.help || n.label"
                @dragstart="onDragStart($event, n.type)"
                @click="emit('add', n.type)"
              >
                <i class="dz-swatch" :style="{ background: s.cat.color }"></i>
                <span class="dz-part-l">{{ n.label }}</span>
              </button>
            </div>
          </template>
        </div>
      </section>

      <p v-if="!shelves.length" class="muted small note">جعبه‌ای با این نام پیدا نشد.</p>
    </div>

    <p class="dz-hint muted small">
      جعبه را بکشید روی بوم، یا رویش کلیک کنید تا وسط بوم اضافه شود. برای اتصال، از
      نقطهٔ سمت راست یک جعبه بکشید و روی نقطهٔ سمت چپ جعبهٔ بعدی رها کنید.
    </p>
  </aside>
</template>
