<script setup lang="ts">
/**
 * PerfPanel.vue — the filter bar, the 🏆 winners, the compare table and the grid.
 *
 * The dropdowns no longer reload the page. They refetch /api/performance/<kind>,
 * which returns the filtered rows AND the server's own tops/compare numbers, so
 * the three sections can never disagree with each other — the failure mode a
 * client-side rewrite usually introduces. The URL still carries every filter, so
 * links stay shareable and Back still walks the filter history.
 *
 * The «از/تا» date form stays in Jinja: it drives a server-side computation (the
 * «بازهٔ دلخواه» column) and owns the Jalali date picker. This component keeps
 * that form's hidden inputs in step with the live filters so submitting it after
 * changing a dropdown does not jump back to the filters the page loaded with.
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import PerfGrid from "./PerfGrid.vue";
import type { PerfPayload } from "./types";
import { fa, fy, pill } from "./format";

const props = defineProps<{
  payload: PerfPayload;
  detailBaseStock: string;
  detailBaseEtf: string;
}>();

const data = ref<PerfPayload>(props.payload);
const loading = ref(false);
const failed = ref(false);

const kind = computed(() => data.value.kind);
const isStock = computed(() => kind.value === "stock");
const detailBase = computed(() => (isStock.value ? props.detailBaseStock : props.detailBaseEtf));

/* ------------------------------------------------------------- filters */
const fKind = ref<"stock" | "etf">(props.payload.kind);
const fMarket = ref(props.payload.market ?? "");
const fGroup = ref(props.payload.group ?? "");
const fSub = ref(props.payload.subgroup ?? "");
const cmp = ref(props.payload.cmp ?? "");
const text = ref("");

/** The date range lives in the Jinja form; it is only carried through here. */
function dates() {
  const q = new URLSearchParams(window.location.search);
  return { rfrom: q.get("rfrom") ?? "", rto: q.get("rto") ?? "" };
}

function query() {
  const q = new URLSearchParams();
  if (fKind.value !== "stock") q.set("kind", fKind.value);
  if (isStock.value && fMarket.value) q.set("market", fMarket.value);
  if (fGroup.value) q.set("group", fGroup.value);
  if (isStock.value && fSub.value) q.set("subgroup", fSub.value);
  const { rfrom, rto } = dates();
  if (rfrom) q.set("rfrom", rfrom);
  if (rto) q.set("rto", rto);
  if (cmp.value) q.set("cmp", cmp.value);
  return q;
}

let inflight = 0;

async function reload() {
  const q = query();
  q.delete("kind");                       // the kind is in the path
  const seq = ++inflight;
  loading.value = true;
  failed.value = false;
  try {
    const res = await fetch(`/api/performance/${fKind.value}?${q.toString()}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const next = (await res.json()) as PerfPayload;
    if (seq !== inflight) return;         // a newer click already won
    data.value = next;
    // The server drops filters that no longer apply (a group that does not
    // exist for this kind, a sub-group outside the chosen group). Adopt its
    // answer so the selects show what is actually being displayed.
    fMarket.value = next.market ?? "";
    fGroup.value = next.group ?? "";
    fSub.value = next.subgroup ?? "";
  } catch (err) {
    if (seq === inflight) failed.value = true;
    console.error("[bn] performance data failed to load:", err);
  } finally {
    if (seq === inflight) loading.value = false;
  }
}

/* ------------------------------------------------------------- URL sync */
let restoring = false;

function pushUrl() {
  const qs = query().toString();
  window.history.pushState({ bn: true }, "", qs ? `?${qs}` : window.location.pathname);
  syncDateForm();
}

/** Keep the Jinja date form pointing at the live filters. */
function syncDateForm() {
  const form = document.getElementById("perf-date-form") as HTMLFormElement | null;
  if (!form) return;
  const set = (name: string, value: string) => {
    const el = form.querySelector<HTMLInputElement>(`input[name="${name}"]`);
    if (el) el.value = value;
  };
  set("kind", fKind.value);
  set("market", isStock.value ? fMarket.value : "");
  set("group", fGroup.value);
  set("subgroup", isStock.value ? fSub.value : "");
  set("cmp", cmp.value);
}

watch([fKind, fMarket, fGroup, fSub], async (now, before) => {
  if (restoring) return;
  // Switching kind is a different dataset: clear filters that cannot survive it,
  // exactly as the route's "stale group after switching kind" guard does.
  if (now[0] !== before[0]) {
    fMarket.value = "";
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
  fMarket.value = q.get("market") ?? "";
  fGroup.value = q.get("group") ?? "";
  fSub.value = q.get("subgroup") ?? "";
  cmp.value = q.get("cmp") ?? "";
  void reload();
  requestAnimationFrame(() => (restoring = false));
}

onMounted(() => {
  window.addEventListener("popstate", onPop);
  syncDateForm();
});
onUnmounted(() => window.removeEventListener("popstate", onPop));

/* ------------------------------------------------------------- compare */
const cmpInput = ref(props.payload.cmp ?? "");

async function submitCompare() {
  cmp.value = cmpInput.value.trim();
  pushUrl();
  await reload();
}

async function clearCompare() {
  cmpInput.value = "";
  cmp.value = "";
  pushUrl();
  await reload();
}

/* ------------------------------------------------------------ watchlist */
const watched = ref(new Set(props.payload.watched));
function onWatchToggled(key: string, on: boolean) {
  const next = new Set(watched.value);
  if (on) next.add(key);
  else next.delete(key);
  watched.value = next;
}
// H-1. Refetched payloads no longer carry `watched` — the stars come from
// /api/watchlist/keys once, at boot. Guarded rather than removed: without the
// check this assigned `new Set(undefined)` on every filter change and every
// star silently vanished until reload.
watch(data, (d) => {
  if (d.watched) watched.value = new Set(d.watched);
});

const gridRef = ref<InstanceType<typeof PerfGrid> | null>(null);
const shownCount = computed(() => {
  const q = text.value.trim();
  return q ? data.value.rows.filter((r) => (r.ticker || "").includes(q)).length : data.value.rows.length;
});
const scopeLabel = computed(() => (data.value.group ? `«${data.value.group}»` : ""));
</script>

<template>
  <form class="filterbar" style="margin-top:14px" @submit.prevent>
    <label>نوع:</label>
    <select v-model="fKind">
      <option value="stock">سهام</option>
      <option value="etf">صندوق‌ها</option>
    </select>

    <template v-if="isStock && data.markets.length">
      <label>بازار:</label>
      <select v-model="fMarket">
        <option value="">همه</option>
        <option v-for="m in data.markets" :key="m" :value="m">{{ m }}</option>
      </select>
    </template>

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

    <span v-if="loading" class="muted small">در حال بارگذاری…</span>
    <span v-else-if="failed" class="muted small">داده‌ها بارگذاری نشد — دوباره تلاش کنید.</span>

    <input
      id="tablefilter"
      v-model="text"
      type="text"
      placeholder="فیلتر نماد در جدول…"
      autocomplete="off"
    />
  </form>

  <!-- 🏆 top performer per window (within the current filter scope) -->
  <template v-if="data.tops.length">
    <div class="panel-head" style="margin-top:6px"><h2>🏆 برترین بازده هر دوره</h2></div>
    <div class="perf-tops">
      <div v-for="t in data.tops" :key="t.key" class="stat-card">
        <div class="stat-label">{{ t.label }}</div>
        <div class="stat-num">
          <span v-if="!pill(t.gain).missing" class="pill" :class="pill(t.gain).cls">{{ pill(t.gain).text }}</span>
          <span v-else class="muted">—</span>
        </div>
        <div class="small muted" style="margin-top:6px">{{ t.ticker || '—' }}</div>
      </div>
    </div>
  </template>

  <!-- compare-a-ticker: your pick vs each window's best -->
  <section class="panel calc-panel" style="margin-top:20px">
    <div class="panel-head">
      <h2>مقایسهٔ یک نماد با برترین‌ها</h2>
      <span class="muted small">یک نماد وارد کنید تا بازدهٔ آن در هر دوره کنار برترین نماد همان دوره قرار گیرد.</span>
    </div>
    <form class="filterbar" @submit.prevent="submitCompare">
      <label>نماد:</label>
      <input v-model="cmpInput" type="text" dir="rtl" autocomplete="off" placeholder="مثلاً فولاد" />
      <button type="submit" class="btn">📊 مقایسه</button>
      <button v-if="data.cmp" type="button" class="btn ghost" @click="clearCompare">پاک‌کردن</button>
    </form>

    <p v-if="data.cmp && !data.compare" class="flash error small" style="margin:10px 0 0">
      نماد «{{ data.cmp }}» یافت نشد.
    </p>

    <template v-if="data.compare">
      <div class="cmp-head">
        <h3>مقایسهٔ «{{ data.compare.ticker }}» با برترین‌های {{ scopeLabel || 'کل بازار' }}</h3>
        <span class="muted small">{{ data.compare.name }} · قیمت پایانی {{ fa(data.compare.latest) }}</span>
      </div>
      <div class="table-scroll">
        <table class="grid">
          <thead>
            <tr>
              <th>دوره</th>
              <th class="numh">بازدهٔ نماد شما</th>
              <th>برترین نماد</th>
              <th class="numh">بازدهٔ برتر</th>
              <th class="numh">اختلاف</th>
              <th>وضعیت</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in data.comparison" :key="c.label">
              <td>{{ c.label }}</td>
              <td class="num">
                <span v-if="!pill(c.yours).missing" class="pill" :class="pill(c.yours).cls">{{ pill(c.yours).text }}</span>
                <span v-else class="muted">—</span>
              </td>
              <td class="sym">{{ c.top_ticker || '—' }}</td>
              <td class="num">
                <span v-if="!pill(c.top).missing" class="pill" :class="pill(c.top).cls">{{ pill(c.top).text }}</span>
                <span v-else class="muted">—</span>
              </td>
              <td class="num">
                <span v-if="!pill(c.diff).missing" class="pill" :class="pill(c.diff).cls">{{ pill(c.diff).text }}</span>
                <span v-else class="muted">—</span>
              </td>
              <td class="small">
                <span v-if="c.diff === null" class="muted">—</span>
                <span v-else-if="c.top_ticker === data.compare.ticker" class="up-title">👑 صدرنشین</span>
                <span v-else-if="c.diff >= 0" class="up-title">✅ جلوتر</span>
                <span v-else class="down-title">❌ عقب‌تر</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>

  <!-- the wide multi-period table -->
  <div class="panel-head" style="margin-top:20px">
    <h2>بازدهٔ همهٔ نمادها {{ scopeLabel }}</h2>
    <span class="muted">تاریخ مبنا: {{ fy(data.as_of) }} · {{ fa(shownCount) }} نماد</span>
  </div>

  <p class="muted small" style="margin:0 0 8px">
    زیر هر دوره دو ستون است — به ترتیب: <b>سقف · کف</b>. برای مرتب‌سازی روی عنوان هر ستون
    (نماد، گروه، قیمت پایانی، و «سقف» یا «کف» هر دوره) کلیک کنید؛ کلیک دوباره جهت را برعکس می‌کند
    و نمادهای بدون داده همیشه در انتها می‌مانند.
  </p>

  <PerfGrid
    ref="gridRef"
    :rows="data.rows"
    :cols="data.cols"
    :kind="kind"
    :group-label="data.group_label"
    :etf-type-colors="data.etf_type_colors"
    :watched="watched"
    :filter-text="text"
    :pinned="data.compare ? data.compare.ticker : null"
    :detail-base="detailBase"
    :as-of="data.as_of"
    @watch-toggled="onWatchToggled"
  />
</template>
