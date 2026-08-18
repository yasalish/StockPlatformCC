<script setup lang="ts">
/**
 * ScreenerPanel.vue — the «غربالگر هوشمند» filter bar, summary cards and grid.
 *
 * Same arrangement as PerfPanel: the dropdowns refetch /api/screener/<kind>
 * instead of reloading the page, and the URL keeps carrying them so links and
 * Back still work. The verdict band and the score are the server's, not
 * re-derived here.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import ScreenerGrid from "./ScreenerGrid.vue";
import type { ScreenerPayload } from "./types";
import { fa, fy } from "./format";

const props = defineProps<{
  payload: ScreenerPayload;
  detailBaseStock: string;
  detailBaseEtf: string;
}>();

const data = ref<ScreenerPayload>(props.payload);
const loading = ref(false);
const failed = ref(false);

const isStock = computed(() => data.value.kind === "stock");
const detailBase = computed(() => (isStock.value ? props.detailBaseStock : props.detailBaseEtf));

const fKind = ref<"stock" | "etf">(props.payload.kind);
const fGroup = ref(props.payload.group ?? "");
const fSub = ref(props.payload.subgroup ?? "");
const fVerdict = ref(props.payload.verdict ?? "");
const text = ref("");

function query() {
  const q = new URLSearchParams();
  if (fKind.value !== "stock") q.set("kind", fKind.value);
  if (fGroup.value) q.set("group", fGroup.value);
  if (isStock.value && fSub.value) q.set("subgroup", fSub.value);
  if (fVerdict.value) q.set("verdict", fVerdict.value);
  return q;
}

let inflight = 0;

async function reload() {
  const q = query();
  q.delete("kind");
  const seq = ++inflight;
  loading.value = true;
  failed.value = false;
  try {
    const res = await fetch(`/api/screener/${fKind.value}?${q.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const next = (await res.json()) as ScreenerPayload;
    if (seq !== inflight) return;
    data.value = next;
    fGroup.value = next.group ?? "";
    fSub.value = next.subgroup ?? "";
    fVerdict.value = next.verdict ?? "";
  } catch (err) {
    if (seq === inflight) failed.value = true;
    console.error("[bn] screener data failed to load:", err);
  } finally {
    if (seq === inflight) loading.value = false;
  }
}

let restoring = false;

function pushUrl() {
  const qs = query().toString();
  window.history.pushState({ bn: true }, "", qs ? `?${qs}` : window.location.pathname);
}

watch([fKind, fGroup, fSub, fVerdict], async (now, before) => {
  if (restoring) return;
  if (now[0] !== before[0]) {
    fGroup.value = "";
    fSub.value = "";
  }
  pushUrl();
  await reload();
});

function onPop() {
  restoring = true;
  const q = new URLSearchParams(window.location.search);
  fKind.value = (q.get("kind") as "stock" | "etf") || "stock";
  fGroup.value = q.get("group") ?? "";
  fSub.value = q.get("subgroup") ?? "";
  fVerdict.value = q.get("verdict") ?? "";
  void reload();
  requestAnimationFrame(() => (restoring = false));
}

onMounted(() => window.addEventListener("popstate", onPop));
onUnmounted(() => window.removeEventListener("popstate", onPop));

const watched = ref(new Set(props.payload.watched));
function onWatchToggled(key: string, on: boolean) {
  const next = new Set(watched.value);
  if (on) next.add(key);
  else next.delete(key);
  watched.value = next;
}
watch(data, (d) => (watched.value = new Set(d.watched)));

const buySignals = computed(() => data.value.rows.filter((r) => r.verdict?.tone === "pos").length);
const shown = computed(() => {
  const q = text.value.trim();
  return q ? data.value.rows.filter((r) => (r.ticker || "").includes(q)).length : data.value.rows.length;
});
</script>

<template>
  <div class="stat-row">
    <div class="stat-card"><div class="stat-num">{{ fy(data.as_of) }}</div><div class="stat-label">تاریخ مبنا</div></div>
    <div class="stat-card"><div class="stat-num">{{ fa(data.scanned) }}</div><div class="stat-label">نماد پویش‌شده</div></div>
    <div class="stat-card"><div class="stat-num up-t">{{ fa(shown) }}</div><div class="stat-label">نماد در فهرست</div></div>
    <div class="stat-card"><div class="stat-num">{{ fa(buySignals) }}</div><div class="stat-label">سیگنال خرید</div></div>
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

    <label>سیگنال:</label>
    <select v-model="fVerdict">
      <option value="">همه</option>
      <option v-for="b in data.bands" :key="b.key" :value="b.key">{{ b.label }}</option>
    </select>

    <span v-if="loading" class="muted small">در حال بارگذاری…</span>
    <span v-else-if="failed" class="muted small">داده‌ها بارگذاری نشد — دوباره تلاش کنید.</span>

    <input id="screenfilter" v-model="text" type="text" placeholder="فیلتر نماد در جدول…" autocomplete="off" />
  </form>

  <section class="panel">
    <ScreenerGrid
      :rows="data.rows"
      :kind="data.kind"
      :group-label="data.group_label"
      :etf-type-colors="data.etf_type_colors"
      :watched="watched"
      :filter-text="text"
      :detail-base="detailBase"
      @watch-toggled="onWatchToggled"
    />
  </section>
</template>
