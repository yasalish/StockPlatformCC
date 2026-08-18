<script setup lang="ts">
/**
 * ScanPanel.vue — the whole body of /filters and /strategies.
 *
 * The two pages are the same thing with different section metadata, so they
 * share this component and one endpoint (/api/scan/<what>/<kind>). It owns the
 * filter bar (which no longer reloads the page), the summary cards and the list
 * of sections; each section mounts its own table when scrolled to.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import ScanSection from "./ScanSection.vue";
import type { ScanPayload, ScanRow } from "./types";
import { fa, fy } from "./format";

const props = defineProps<{
  payload: ScanPayload;
  detailBaseStock: string;
  detailBaseEtf: string;
}>();

const data = ref<ScanPayload>(props.payload);
const loading = ref(false);
const failed = ref(false);

const what = props.payload.what;                 // "filters" | "strategies"
const isFilters = what === "filters";
const isStock = computed(() => data.value.kind === "stock");
const detailBase = computed(() => (isStock.value ? props.detailBaseStock : props.detailBaseEtf));

/* ------------------------------------------------------------- filters */
const fKind = ref<"stock" | "etf">(props.payload.kind);
const fGroup = ref(props.payload.group ?? "");
const fSub = ref(props.payload.subgroup ?? "");
// Which section(s) to show, and (filters page only) which category.
const selected = ref("all");
const cat = ref("all");

function readUrl() {
  const q = new URLSearchParams(window.location.search);
  fKind.value = (q.get("kind") as "stock" | "etf") || "stock";
  fGroup.value = q.get("group") ?? "";
  fSub.value = q.get("subgroup") ?? "";
  selected.value = q.get(isFilters ? "filter" : "strategy") ?? "all";
  cat.value = q.get("cat") ?? "all";
}
readUrl();

function query() {
  const q = new URLSearchParams();
  if (fKind.value !== "stock") q.set("kind", fKind.value);
  if (fGroup.value) q.set("group", fGroup.value);
  if (isStock.value && fSub.value) q.set("subgroup", fSub.value);
  if (selected.value !== "all") q.set(isFilters ? "filter" : "strategy", selected.value);
  if (isFilters && cat.value !== "all") q.set("cat", cat.value);
  return q;
}

let inflight = 0;

async function reload() {
  const q = query();
  q.delete("kind");
  // The section and category choices are display-only: the payload holds every
  // section already, so they never need a round trip.
  q.delete("filter");
  q.delete("strategy");
  q.delete("cat");
  const seq = ++inflight;
  loading.value = true;
  failed.value = false;
  try {
    const res = await fetch(`/api/scan/${what}/${fKind.value}?${q.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const next = (await res.json()) as ScanPayload;
    if (seq !== inflight) return;
    data.value = next;
    fGroup.value = next.group ?? "";
    fSub.value = next.subgroup ?? "";
  } catch (err) {
    if (seq === inflight) failed.value = true;
    console.error(`[bn] ${what} data failed to load:`, err);
  } finally {
    if (seq === inflight) loading.value = false;
  }
}

let restoring = false;

function pushUrl() {
  const qs = query().toString();
  window.history.pushState({ bn: true }, "", qs ? `?${qs}` : window.location.pathname);
}

// kind / group / sub-group need new data; section and category do not.
watch([fKind, fGroup, fSub], async (now, before) => {
  if (restoring) return;
  if (now[0] !== before[0]) {
    fGroup.value = "";
    fSub.value = "";
    selected.value = "all";
  }
  pushUrl();
  await reload();
});

watch([selected, cat], () => {
  if (!restoring) pushUrl();
});

function onPop() {
  restoring = true;
  const before = `${fKind.value}|${fGroup.value}|${fSub.value}`;
  readUrl();
  if (`${fKind.value}|${fGroup.value}|${fSub.value}` !== before) void reload();
  requestAnimationFrame(() => (restoring = false));
}

onMounted(() => window.addEventListener("popstate", onPop));
onUnmounted(() => window.removeEventListener("popstate", onPop));

/* -------------------------------------------------------------- rows */
const symbols = computed(() => data.value.symbols);

function rowsOf(ids: number[]): ScanRow[] {
  const s = symbols.value;
  return ids.map((id) => s[String(id)]).filter(Boolean);
}

const visibleSections = computed(() =>
  data.value.sections.filter(
    (s) =>
      (selected.value === "all" || selected.value === s.key) &&
      (!isFilters || cat.value === "all" || cat.value === s.cat),
  ),
);

const pickRows = computed<ScanRow[]>(() =>
  (data.value.picks ?? []).map((p) => ({ ...symbols.value[String(p.id)], score: p.score, signals: p.signals })),
);

function badgeFor(dir?: string) {
  if (!dir) return null;
  const color = dir === "up" ? "#1a9d63" : dir === "down" ? "#c0392b" : "#7f8c8d";
  return { text: dir === "up" ? "▲" : dir === "down" ? "▼" : "◆", color };
}

const sectionLabel = isFilters ? "فیلتر" : "استراتژی";
</script>

<template>
  <div class="stat-row">
    <div class="stat-card">
      <div class="stat-num">{{ fy(data.as_of) }}</div>
      <div class="stat-label">تاریخ مبنا</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">{{ fa(data.scanned) }}</div>
      <div class="stat-label">نماد پویش‌شده</div>
    </div>
    <div class="stat-card">
      <div class="stat-num up-t">{{ fa(data.count) }}</div>
      <div class="stat-label">نماد دارای دستِ‌کم یک {{ sectionLabel }}</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">{{ fa(data.sections.length) }}</div>
      <div class="stat-label">تعداد {{ sectionLabel }}‌ها</div>
    </div>
  </div>

  <form class="filterbar" @submit.prevent>
    <label>نوع:</label>
    <select v-model="fKind">
      <option value="stock">سهام</option>
      <option value="etf">صندوق‌ها</option>
    </select>

    <label>{{ data.group_label }}:</label>
    <select v-model="fGroup">
      <option value="">همه</option>
      <option v-for="g in data.groups" :key="g" :value="g">{{ g }}</option>
    </select>

    <template v-if="isStock && data.subgroups.length">
      <label>زیرگروه:</label>
      <select v-model="fSub">
        <option value="">همه</option>
        <option v-for="s in data.subgroups" :key="s" :value="s">{{ s }}</option>
      </select>
    </template>

    <template v-if="isFilters && data.categories">
      <label>دسته:</label>
      <select v-model="cat">
        <option value="all">همه</option>
        <option v-for="c in data.categories" :key="c.key" :value="c.key">{{ c.name }}</option>
      </select>
    </template>

    <label>{{ sectionLabel }}:</label>
    <select v-model="selected">
      <option value="all">همه</option>
      <option v-for="s in data.sections" :key="s.key" :value="s.key">{{ s.name }}</option>
    </select>

    <span v-if="loading" class="muted small">در حال بارگذاری…</span>
    <span v-else-if="failed" class="muted small">داده‌ها بارگذاری نشد — دوباره تلاش کنید.</span>
  </form>

  <!-- ⭐ multi-signal picks (strategies page, only when nothing is narrowed) -->
  <section v-if="!isFilters && selected === 'all' && pickRows.length" class="panel">
    <div class="panel-head">
      <h2 class="up-t">⭐ بهترین گزینه‌ها — هم‌گرایی چند استراتژی</h2>
      <span class="muted small">نمادهایی که هم‌زمان دستِ‌کم دو استراتژی روی آن‌ها سیگنال خرید می‌دهند</span>
    </div>
    <ScanTable
      :rows="pickRows"
      :detail-base="detailBase"
      picks
      :strat-names="data.strat_names"
    />
  </section>

  <ScanSection
    v-for="s in visibleSections"
    :key="s.key"
    :title="s.name"
    :desc="s.desc"
    :source="s.source"
    :badge="badgeFor(s.dir)"
    :rows="rowsOf(s.ids)"
    :detail-base="detailBase"
  />

  <p v-if="!visibleSections.length" class="muted note">موردی برای نمایش نیست.</p>
</template>
