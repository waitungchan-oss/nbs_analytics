<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  downloadDashboardReport,
  downloadForecastReport,
  downloadQualityReport,
  getDashboardAnalytics,
  getDashboardContext,
  getDashboardFacts,
  getDashboardSummary,
  getDataQuality,
  getForecastInsights,
  getHealth,
  getStabilityHistory,
  uploadMonthlyData
} from './lib/api'
import SvgBarChart from './components/SvgBarChart.vue'
import SvgDonutChart from './components/SvgDonutChart.vue'
import SvgLineChart from './components/SvgLineChart.vue'

const loading = ref(true)
const refreshing = ref(false)
const errorMessage = ref('')
const health = ref(null)
const context = ref(null)
const facts = ref(null)
const factsError = ref('')
const summary = ref(null)
const analytics = ref(null)
const dataQuality = ref(null)
const forecastInsights = ref(null)
const stabilityHistory = ref([])
const uploadMainFile = ref(null)
const uploadTourFile = ref(null)
const uploadOtherFiles = ref([])
const uploadBusy = ref(false)
const uploadError = ref('')
const uploadResult = ref(null)
const uploadFormKey = ref(0)
const reportBusy = ref('')
const activeSection = ref('overview')
const showFullRankings = ref(false)

const BASELINE_MONTH = '2026-05'
const BASELINE_TOTAL = 'HKD 12,057,968'
const BASELINE_MAX_DATE = '2026-06-22'

const filters = ref({
  years: [],
  months: [],
  dateRange: [],
  branch: '全部分社',
  salesGroup: '全部銷售組'
})
const dateStart = ref('')
const dateEnd = ref('')

const periodText = computed(() => {
  if (!context.value) return 'Loading...'
  const minDate = context.value.minDate || '—'
  const maxDate = context.value.maxDate || '—'
  return `${minDate} 至 ${maxDate}`
})

const navItems = computed(() => [
  { id: 'overview', label: 'Overview' },
  { id: 'diagnosis', label: 'Drift Diagnosis' },
  { id: 'analytics', label: 'Year / Month Analysis' },
  { id: 'ranking', label: 'Branch / Specialist Ranking' },
  { id: 'mix', label: 'Product Mix' },
  { id: 'quality', label: 'Data Quality' },
  { id: 'forecast', label: 'Official Forecast' },
  { id: 'upload', label: 'Vue Upload' },
  { id: 'history', label: 'Acceptance History' },
  { id: 'api', label: 'API Status' }
])

const revenueTotals = computed(() => summary.value?.revenueTotals || null)
const factsSourceStatus = computed(() => facts.value?.status || (factsError.value ? 'unavailable' : 'pending'))
const dataFreshness = computed(() => summary.value?.dataFreshness || null)
const stabilityBaseline = computed(() => summary.value?.stabilityBaseline || null)
const latestAcceptance = computed(() => stabilityHistory.value[0] || null)
const latestDiagnosisAcceptance = computed(
  () => stabilityHistory.value.find(record => record?.driftDiagnosis?.status) || null
)
const latestDiagnosis = computed(() => latestDiagnosisAcceptance.value?.driftDiagnosis || null)
const latestDiagnosisSourceLabel = computed(() => {
  if (!latestDiagnosisAcceptance.value) return 'Pending'
  return `Record #${latestDiagnosisAcceptance.value.id} · ${formatTimestamp(latestDiagnosisAcceptance.value.createdAt)}`
})
const latestDiagnosisDrivers = computed(() => latestDiagnosis.value?.topDrivers || [])
const latestDiagnosisOrderDiffs = computed(() => latestDiagnosis.value?.sourceOrderDiffs || [])
const recentAcceptanceRows = computed(() => stabilityHistory.value.slice(0, 3))
const uploadStatusTone = computed(() => {
  const status = uploadResult.value?.status
  if (status === 'success') return 'ok'
  if (status === 'blocked') return 'critical'
  if (status === 'error') return 'critical'
  return 'degraded'
})
const acceptanceStatusCards = computed(() => [
  {
    label: 'Latest Upload',
    value: historyStatusText(latestAcceptance.value?.uploadStatus),
    note: latestAcceptance.value?.uploadMessage || 'Read-only upload audit',
    tone: latestAcceptance.value?.uploadStatus || 'muted'
  },
  {
    label: 'Latest Preflight',
    value: diagnosisStatusText(latestAcceptance.value?.driftDiagnosis?.status),
    note: latestAcceptance.value?.latestDiagnosisSourceLabel || latestDiagnosisSourceLabel.value,
    tone: diagnosisTone(latestAcceptance.value?.driftDiagnosis?.status)
  },
  {
    label: 'Latest Rollback',
    value: latestAcceptance.value?.rollbackStatus || 'not_required',
    note: latestAcceptance.value?.rollbackError || 'No rollback required',
    tone: latestAcceptance.value?.rollbackStatus || 'muted'
  },
  {
    label: 'History',
    value: `${stabilityHistory.value.length} records`,
    note: latestAcceptance.value
      ? `Last ${formatTimestamp(latestAcceptance.value.createdAt)}`
      : 'Awaiting first snapshot',
    tone: latestAcceptance.value ? 'ok' : 'muted'
  }
])
const branchRows = computed(() => {
  const rows = analytics.value?.branchRanking || []
  return showFullRankings.value ? rows : rows.slice(0, 10)
})
const specialistRows = computed(() => {
  const rows = analytics.value?.specialistRanking || []
  return showFullRankings.value ? rows : rows.slice(0, 10)
})
const annualRows = computed(() => analytics.value?.annualSummary || [])
const monthlyRows = computed(() => analytics.value?.monthlyTrend || [])
const monthlyMax = computed(() =>
  Math.max(1, ...monthlyRows.value.map(row => Number(row.combinedRevenue || 0)))
)
const monthlyChartRows = computed(() =>
  monthlyRows.value.map(row => ({
    label: row.month,
    branchRevenue: Number(row.branchRevenue || 0),
    specialistRevenue: Number(row.specialistRevenue || 0),
    combinedRevenue: Number(row.combinedRevenue || 0)
  }))
)
const branchRankingChartRows = computed(() =>
  branchRows.value.map(row => ({
    label: row.branch,
    value: Number(row.totalRevenue || 0)
  }))
)
const specialistRankingChartRows = computed(() =>
  specialistRows.value.map(row => ({
    label: row.specialist,
    value: Number(row.totalRevenue || 0)
  }))
)
const productMixChartRows = computed(() =>
  ['旅行團', '郵輪', '票務'].map(key => ({
    label: `${key}營收`,
    value: productMixRows.value.reduce((sum, row) => sum + Number(row?.[key] || 0), 0)
  }))
)
const forecastChartRows = computed(() =>
  (forecastInsights.value?.daily || []).slice(0, 7).map(row => ({
    label: row.date,
    consensus: Number(row.consensus || 0),
    lower: Number(row.lower || 0),
    upper: Number(row.upper || 0)
  }))
)
const branchProducts = computed(() => analytics.value?.productDrilldown?.branch || [])
const specialistProducts = computed(() => analytics.value?.productDrilldown?.specialist || [])
const productMixRows = computed(() => summary.value?.productMix || [])

const productMixColumns = computed(() => {
  const firstRow = productMixRows.value[0]
  return firstRow ? Object.keys(firstRow) : []
})

const productMixPreviewRows = computed(() => productMixRows.value.slice(0, 8))

const productMixStats = computed(() => {
  const rows = productMixRows.value
  const numericFields = ['旅行團', '郵輪', '票務']
  const nonZeroRowCount = rows.filter(row =>
    numericFields.some(key => Number(row?.[key]) > 0)
  ).length
  const latestDate = rows
    .map(row => row?.日期 || row?.date || row?.統一日期 || '')
    .filter(Boolean)
    .sort()
    .slice(-1)[0]
  return [
    {
      label: '預覽列數',
      value: `${rows.length}`,
      note: '目前只顯示前 8 筆，避免首頁過長'
    },
    {
      label: '欄位數',
      value: `${productMixColumns.value.length}`,
      note: '保留原始欄位，讓明細仍可查核'
    },
    {
      label: '最近日期',
      value: latestDate || '—',
      note: '由目前預覽資料推估'
    },
    {
      label: '非零列數',
      value: `${nonZeroRowCount}`,
      note: '預覽區內至少有一項金額不為 0 的列'
    }
  ]
})

const baselineCards = computed(() => [
  {
    label: '2026-05 分社 + 專職總營收',
    value: summary.value?.revenueTotals?.formattedCombinedRevenue || BASELINE_TOTAL,
    note: 'Phase 2A 前置驗收基線，不含掛賬核銷與 TT 退款轉團款',
    status:
      summary.value?.revenueTotals?.formattedCombinedRevenue === BASELINE_TOTAL
        ? 'Matched'
        : 'Check'
  },
  {
    label: '分社營收',
    value: moneyText(revenueTotals.value?.branchRevenue),
    note: 'Branch channel official scope',
    status: 'Read-only'
  },
  {
    label: '專職銷售組營收',
    value: moneyText(revenueTotals.value?.specialistRevenue),
    note: 'Specialist channel official scope',
    status: 'Read-only'
  },
  {
    label: '最新收款日期',
    value: summary.value?.dataFreshness?.maxDate || BASELINE_MAX_DATE,
    note: `Freshness baseline: ${BASELINE_MAX_DATE}`,
    status:
      summary.value?.dataFreshness?.maxDate === BASELINE_MAX_DATE ? 'Matched' : 'Updated'
  }
])

const freshnessRows = computed(() => [
  ['最早收款日期', dataFreshness.value?.minDate || '—'],
  ['最新收款日期', dataFreshness.value?.maxDate || '—'],
  ['原始明細筆數', formatNumber(dataFreshness.value?.rawRows)],
  ['正式口徑筆數', formatNumber(dataFreshness.value?.analysisRows)],
  ['排除明細筆數', formatNumber(dataFreshness.value?.excludedRows)]
])

const coreValidation = computed(() => stabilityBaseline.value?.coreValidation || null)
const freshnessUpdate = computed(() => stabilityBaseline.value?.freshnessUpdate || null)
const coreValidationChecks = computed(() => coreValidation.value?.checks || [])
const freshnessUpdateChecks = computed(() => freshnessUpdate.value?.checks || [])

const filterSummaryItems = computed(() => [
  {
    label: '年份',
    value: filters.value.years.length ? filters.value.years.join(', ') : '全部年份'
  },
  {
    label: '月份',
    value: filters.value.months.length ? filters.value.months.join(', ') : '全部月份'
  },
  {
    label: '日期',
    value: dateStart.value && dateEnd.value ? `${dateStart.value} 至 ${dateEnd.value}` : '完整日期範圍'
  },
  {
    label: '分社',
    value: filters.value.branch
  },
  {
    label: '銷售組',
    value: filters.value.salesGroup
  }
])

const stabilityStatusText = computed(() => {
  if (!stabilityBaseline.value) return 'Pending'
  return stabilityBaseline.value.status === 'matched' ? 'Matched' : 'Drift'
})

const stabilityDeltaText = computed(() => {
  if (!stabilityBaseline.value) return '—'
  const amount = stabilityBaseline.value.deltaAmount || 0
  const sign = amount > 0 ? '+' : ''
  return `${sign}${moneyText(amount)} / ${sign}${Number(stabilityBaseline.value.deltaPct || 0).toFixed(4)}%`
})

function formatNumber(value) {
  if (value === null || value === undefined || value === '') return '—'
  return Number(value).toLocaleString('en-US', { maximumFractionDigits: 0 })
}

function formatPercent(value) {
  if (value === null || value === undefined || value === '') return '—'
  return `${Number(value).toFixed(2)}%`
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function formatTimestamp(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-HK', { hour12: false })
}

function historyStatusText(status) {
  const labels = {
    accepted: '已接受',
    rejected_rolled_back: '已拒絕並回滾',
    rollback_failed: '回滾失敗',
    baseline: '基線快照'
  }
  return labels[status] || status || 'unknown'
}

function diagnosisStatusText(status) {
  if (!status) return 'Pending'
  const labels = {
    no_drift: 'No Drift',
    drift: 'Drift',
    unavailable: 'Unavailable'
  }
  return labels[status] || status || 'unknown'
}

function preflightStatusText(status) {
  if (!status) return 'Pending'
  const labels = {
    matched: 'Matched',
    drift: 'Drift',
    blocked: 'Blocked'
  }
  return labels[status] || status || 'unknown'
}

function diagnosisTone(status) {
  if (status === 'no_drift') return 'ok'
  if (status === 'drift') return 'critical'
  return 'degraded'
}

function moneyText(value) {
  return `HKD ${formatNumber(value)}`
}

function toggleYear(year) {
  const selected = new Set(filters.value.years)
  if (selected.has(year)) selected.delete(year)
  else selected.add(year)
  filters.value.years = [...selected].sort((a, b) => Number(a) - Number(b))
}

function toggleMonth(month) {
  const selected = new Set(filters.value.months)
  if (selected.has(month)) selected.delete(month)
  else selected.add(month)
  filters.value.months = [...selected].sort()
}

function handleMainFileChange(event) {
  uploadMainFile.value = event.target.files?.[0] || null
}

function handleTourFileChange(event) {
  uploadTourFile.value = event.target.files?.[0] || null
}

function handleOtherFilesChange(event) {
  uploadOtherFiles.value = Array.from(event.target.files || [])
}

function resetUploadForm() {
  uploadMainFile.value = null
  uploadTourFile.value = null
  uploadOtherFiles.value = []
  uploadError.value = ''
  uploadResult.value = null
  uploadFormKey.value += 1
}

function downloadBlob(blob, filename) {
  const url = window.URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.URL.revokeObjectURL(url)
}

async function exportReport(kind) {
  try {
    reportBusy.value = kind
    if (kind === 'dashboard') {
      const { blob, filename } = await downloadDashboardReport({
        ...filters.value,
        dateRange: dateStart.value && dateEnd.value ? [dateStart.value, dateEnd.value] : []
      })
      downloadBlob(blob, filename)
      return
    }
    if (kind === 'quality') {
      const { blob, filename } = await downloadQualityReport()
      downloadBlob(blob, filename)
      return
    }
    if (kind === 'forecast') {
      const { blob, filename } = await downloadForecastReport()
      downloadBlob(blob, filename)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    reportBusy.value = ''
  }
}

async function submitVueUpload() {
  if (!uploadMainFile.value) {
    uploadError.value = '請先選擇主表檔案。'
    return
  }
  try {
    uploadBusy.value = true
    uploadError.value = ''
    uploadResult.value = null
    const formData = new FormData()
    formData.append('main_file', uploadMainFile.value)
    if (uploadTourFile.value) formData.append('tour_file', uploadTourFile.value)
    for (const file of uploadOtherFiles.value) {
      formData.append('other_files', file)
    }
    uploadResult.value = await uploadMonthlyData(formData)
    await loadAll(false)
  } catch (error) {
    uploadError.value = error instanceof Error ? error.message : String(error)
  } finally {
    uploadBusy.value = false
  }
}

function setDefaultFilters(ctx) {
  const hasBaselineMonth = (ctx.months || []).includes(BASELINE_MONTH)
  const defaultStart = hasBaselineMonth ? `${BASELINE_MONTH}-01` : ctx.minDate || ''
  const defaultEnd = hasBaselineMonth ? `${BASELINE_MONTH}-31` : ctx.maxDate || ''
  dateStart.value = defaultStart
  dateEnd.value = defaultEnd
  filters.value = {
    years: hasBaselineMonth ? [2026] : [...(ctx.years || [])],
    months: hasBaselineMonth ? [BASELINE_MONTH] : [...(ctx.months || [])],
    dateRange: defaultStart && defaultEnd ? [defaultStart, defaultEnd] : [],
    branch: '全部分社',
    salesGroup: '全部銷售組'
  }
}

async function loadAll(initial = false) {
  try {
    if (initial) loading.value = true
    else refreshing.value = true
    errorMessage.value = ''
    const [healthPayload, contextPayload, historyPayload, qualityPayload, forecastPayload, factsResult] = await Promise.all([
      getHealth(),
      getDashboardContext(),
      getStabilityHistory(20),
      getDataQuality(),
      getForecastInsights(),
      getDashboardFacts()
        .then(payload => ({ payload }))
        .catch(error => ({ error }))
    ])
    health.value = healthPayload
    context.value = contextPayload
    stabilityHistory.value = historyPayload.items || []
    dataQuality.value = qualityPayload
    forecastInsights.value = forecastPayload
    if (factsResult.error) {
      facts.value = null
      factsError.value = factsResult.error instanceof Error ? factsResult.error.message : String(factsResult.error)
    } else {
      facts.value = factsResult.payload
      factsError.value = ''
    }
    if (initial) setDefaultFilters(contextPayload)
    ;[summary.value, analytics.value] = await Promise.all([
      getDashboardSummary(filters.value),
      getDashboardAnalytics(filters.value)
    ])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

function scrollToSection(sectionId) {
  activeSection.value = sectionId
  const el = document.getElementById(sectionId)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function applyFilters() {
  try {
    refreshing.value = true
    errorMessage.value = ''
    const dateRange =
      dateStart.value && dateEnd.value ? [dateStart.value, dateEnd.value] : []
    const applied = {
      ...filters.value,
      dateRange
    }
    ;[summary.value, analytics.value] = await Promise.all([
      getDashboardSummary(applied),
      getDashboardAnalytics(applied)
    ])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : String(error)
  } finally {
    refreshing.value = false
  }
}

function resetFilters() {
  if (!context.value) return
  setDefaultFilters(context.value)
}

onMounted(() => {
  loadAll(true)
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand-block">
        <div class="brand-kicker">NBS Analytics</div>
        <div class="brand-title">Cockpit</div>
        <div class="brand-subtitle">Vue read-only front end</div>
      </div>

      <nav class="nav-block">
        <div class="nav-label">Navigation</div>
        <button
          v-for="item in navItems"
          :key="item.id"
          class="nav-item"
          :class="{ active: activeSection === item.id }"
          @click="scrollToSection(item.id)"
        >
          {{ item.label }}
        </button>
      </nav>

      <section class="control-panel">
        <div class="panel-label">Control Center</div>

        <div class="field">
          <span>Years</span>
          <div class="chip-grid year-grid">
            <button
              v-for="year in context?.years || []"
              :key="year"
              type="button"
              class="filter-chip"
              :class="{ selected: filters.years.includes(year) }"
              @click="toggleYear(year)"
            >
              {{ year }}
            </button>
          </div>
        </div>

        <div class="field">
          <span>Months</span>
          <div class="chip-grid month-grid">
            <button
              v-for="month in context?.months || []"
              :key="month"
              type="button"
              class="filter-chip"
              :class="{ selected: filters.months.includes(month) }"
              @click="toggleMonth(month)"
            >
              {{ month }}
            </button>
          </div>
        </div>

        <label class="field">
          <span>Branch</span>
          <select v-model="filters.branch">
            <option>全部分社</option>
            <option v-for="branch in context?.branches || []" :key="branch" :value="branch">{{ branch }}</option>
          </select>
        </label>

        <label class="field">
          <span>Sales Group</span>
          <select v-model="filters.salesGroup">
            <option>全部銷售組</option>
            <option v-for="group in context?.salesGroups || []" :key="group" :value="group">{{ group }}</option>
          </select>
        </label>

        <label class="field">
          <span>Date range start</span>
          <input v-model="dateStart" type="date" />
        </label>

        <label class="field">
          <span>Date range end</span>
          <input v-model="dateEnd" type="date" />
        </label>

        <div class="button-row">
          <button class="action-button secondary" @click="resetFilters" :disabled="refreshing || loading">Reset</button>
          <button class="action-button primary" @click="applyFilters" :disabled="refreshing || loading">Apply</button>
        </div>
      </section>
    </aside>

    <main class="main-pane">
      <header class="topbar">
        <div>
          <div class="topbar-kicker">Enterprise Operation Cockpit</div>
          <h1>NBS Analytics</h1>
          <p>{{ periodText }}</p>
        </div>
        <div class="status-cluster">
          <span
            class="status-chip"
            :class="{
              ok: health?.status === 'ok',
              degraded: health?.status === 'degraded',
              critical: health?.status === 'critical'
            }"
          >
            {{ health?.status || 'loading' }}
          </span>
          <span class="status-chip muted">{{ summary?.revenueScope || 'scope pending' }}</span>
        </div>
      </header>

      <div v-if="errorMessage" class="error-banner">
        {{ errorMessage }}
      </div>

      <section class="filter-summary-panel panel">
        <div class="filter-summary-head">
          <div>
            <div class="section-kicker">Phase 2D Filter Summary</div>
            <h2>目前驗收視角</h2>
          </div>
          <span class="status-chip muted">read-only cockpit</span>
        </div>
        <div class="filter-summary-grid">
          <div v-for="item in filterSummaryItems" :key="item.label" class="filter-summary-item">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
      </section>

      <section id="overview" class="section">
        <div class="section-head">
          <div>
            <div class="section-kicker">Overview</div>
            <h2>Phase 2A read-only cockpit</h2>
          </div>
          <div class="section-meta">
            <span v-if="refreshing" class="spinner">Refreshing</span>
            <span v-else class="muted">Connected to Python API</span>
          </div>
        </div>

        <div v-if="loading" class="loading-grid">
          <div class="loading-card" />
          <div class="loading-card" />
          <div class="loading-card" />
          <div class="loading-card" />
        </div>

        <div v-else class="baseline-grid">
          <article
            v-for="card in baselineCards"
            :key="card.label"
            class="baseline-card"
            :class="{ matched: card.status === 'Matched' }"
          >
            <div class="baseline-head">
              <span class="kpi-label">{{ card.label }}</span>
              <span class="baseline-status">{{ card.status }}</span>
            </div>
            <div class="baseline-value">{{ card.value }}</div>
            <div class="kpi-note">{{ card.note }}</div>
          </article>
        </div>

        <section v-if="!loading" class="panel stability-panel">
          <div class="stability-header">
            <div>
              <div class="section-kicker">Phase 2B Stability Monitor</div>
              <h3>口徑回歸驗收狀態</h3>
            </div>
            <span
              class="stability-badge"
              :class="{ matched: stabilityBaseline?.status === 'matched', drift: stabilityBaseline?.status === 'drift' }"
            >
              {{ stabilityStatusText }}
            </span>
          </div>

          <div class="stability-summary-grid">
            <div class="stability-metric">
              <span>Baseline</span>
              <strong>{{ stabilityBaseline?.formattedExpectedTotal || BASELINE_TOTAL }}</strong>
            </div>
            <div class="stability-metric">
              <span>Actual</span>
              <strong>{{ stabilityBaseline?.formattedActualTotal || '—' }}</strong>
            </div>
            <div class="stability-metric">
              <span>Delta</span>
              <strong>{{ stabilityDeltaText }}</strong>
            </div>
            <div class="stability-metric">
              <span>Checks</span>
              <strong>
                {{ coreValidation?.summary?.matchedChecks ?? 0 }} /
                {{ coreValidation?.summary?.totalChecks ?? 0 }} matched
              </strong>
            </div>
          </div>

          <div class="stability-group-head">
            <div>
              <strong>核心口徑驗收</strong>
              <span>營收基線與正式排除規則</span>
            </div>
            <span class="stability-badge" :class="{ matched: coreValidation?.status === 'matched', drift: coreValidation?.status === 'drift' }">
              {{ coreValidation?.status || 'pending' }}
            </span>
          </div>
          <div class="stability-checks">
            <div
              v-for="check in coreValidationChecks"
              :key="check.key"
              class="stability-check"
              :class="{ matched: check.status === 'matched', drift: check.status === 'drift' }"
            >
              <span class="check-status">{{ check.status }}</span>
              <span class="check-label">{{ check.label }}</span>
              <span class="check-values">Expected: {{ check.expected }} · Actual: {{ check.actual }}</span>
            </div>
          </div>

          <div class="stability-group-head freshness">
            <div>
              <strong>資料更新狀態</strong>
              <span>最新日期與資料筆數變化不阻擋核心驗收</span>
            </div>
            <span class="stability-badge updated">{{ freshnessUpdate?.status || 'pending' }}</span>
          </div>
          <div class="stability-checks">
            <div
              v-for="check in freshnessUpdateChecks"
              :key="check.key"
              class="stability-check"
              :class="{ matched: check.status === 'matched', updated: check.status === 'updated' }"
            >
              <span class="check-status">{{ check.status }}</span>
              <span class="check-label">{{ check.label }}</span>
              <span class="check-values">Baseline: {{ check.expected }} · Current: {{ check.actual }}</span>
            </div>
          </div>
        </section>

        <div v-if="!loading" class="kpi-grid compact-grid">
          <article v-for="card in summary?.kpis || []" :key="card.label" class="kpi-card">
            <div class="kpi-label">{{ card.label }}</div>
            <div class="kpi-value">{{ card.value }}</div>
            <div class="kpi-delta">{{ card.delta }}</div>
            <div class="kpi-note">{{ card.note }}</div>
          </article>
        </div>
      </section>

      <section id="diagnosis" class="section">
        <div class="section-head">
          <div>
            <div class="section-kicker">Phase 2N Read-only Cockpit</div>
            <h2>最新口徑診斷與追蹤證據</h2>
          </div>
          <span class="history-status" :class="diagnosisTone(latestDiagnosis?.status)">
            {{ diagnosisStatusText(latestDiagnosis?.status) }}
          </span>
        </div>

        <div v-if="latestDiagnosis" class="diagnosis-summary-grid">
          <article class="panel stat-panel">
            <div class="panel-title">Latest Acceptance</div>
            <div class="panel-value">{{ latestAcceptance?.coreStatus || '—' }}</div>
            <div class="panel-note">
              {{ latestAcceptance?.uploadStatus || '—' }} ·
              {{ latestAcceptance?.rollbackStatus || 'not_required' }}
            </div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Diagnosis Status</div>
            <div class="panel-value">{{ diagnosisStatusText(latestDiagnosis?.status) }}</div>
            <div class="panel-note">{{ latestDiagnosisSourceLabel }}</div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Expected / Actual</div>
            <div class="panel-value">{{ moneyText(latestDiagnosis?.expectedTotal) }} / {{ moneyText(latestDiagnosis?.actualTotal) }}</div>
            <div class="panel-note">Delta {{ moneyText(latestDiagnosis?.deltaAmount) }}</div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Drivers</div>
            <div class="panel-value">{{ latestDiagnosisDrivers.length }}</div>
            <div class="panel-note">Top drivers capped at 50 rows</div>
          </article>
        </div>

        <div v-if="latestDiagnosis" class="panel diagnosis-panel">
          <div class="panel-table-title">Latest Drift Diagnosis</div>
          <div class="diagnosis-summary">
            <div class="diagnosis-summary-text">
              {{ latestDiagnosis.summaryMessage || '—' }}
            </div>
            <div class="diagnosis-summary-meta">
              <span>Baseline {{ latestDiagnosis.baselineMonth || BASELINE_MONTH }}</span>
              <span>{{ latestDiagnosis.rowLimit || 50 }} row cap</span>
              <span>{{ latestDiagnosisOrderDiffs.length }} source-order diffs</span>
            </div>
            <div class="diagnosis-summary-note">Evidence bundle: drift_diagnosis.xlsx / drift_diagnosis.json</div>
          </div>
          <div class="table-scroll">
            <table class="data-table compact diagnosis-table">
              <thead>
                <tr>
                  <th>來源單據號</th>
                  <th>收款單號</th>
                  <th class="num">金額</th>
                  <th>收款類型</th>
                  <th>收款方式</th>
                  <th>主表銷售點</th>
                  <th>副表銷售點</th>
                  <th>原因</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in latestDiagnosisDrivers" :key="`${row.sourceOrderNo}-${row.receiptNo || row.amount}`">
                  <td>{{ row.sourceOrderNo || '—' }}</td>
                  <td>{{ row.receiptNo || '—' }}</td>
                  <td class="num strong">{{ moneyText(row.amount) }}</td>
                  <td>{{ row.paymentType || '—' }}</td>
                  <td>{{ row.paymentMethod || '—' }}</td>
                  <td>{{ row.mainSalesPoint || '—' }}</td>
                  <td>{{ row.subSalesPoint || '—' }}</td>
                  <td class="diagnosis-reason">{{ row.reason || '—' }}</td>
                </tr>
                <tr v-if="!latestDiagnosisDrivers.length">
                  <td colspan="8" class="empty-row">No diagnosis drivers available</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div v-else class="panel diagnosis-panel">
          <div class="panel-table-title">Latest Drift Diagnosis</div>
          <div class="diagnosis-summary">
            <div class="diagnosis-summary-text">尚未建立可讀診斷紀錄；完成一次上傳預演或重建後，這裡會顯示最新 drift / no drift 診斷。</div>
            <div class="diagnosis-summary-note">{{ latestDiagnosisSourceLabel }}</div>
            <div class="diagnosis-summary-note">Evidence bundle: drift_diagnosis.xlsx / drift_diagnosis.json</div>
          </div>
        </div>
      </section>

      <section id="analytics" class="section">
        <div class="section-head">
          <div>
            <div class="section-kicker">Phase 2K-1 Analytics</div>
            <h2>Year and month channel alignment</h2>
          </div>
          <span
            class="history-status"
            :class="analytics?.reconciliation?.status"
          >
            Reconciliation {{ analytics?.reconciliation?.status || 'pending' }}
          </span>
        </div>

        <div class="analytics-layout">
          <div class="panel analytics-panel">
            <div class="panel-table-title">Annual Channel Summary</div>
            <div class="annual-card-grid">
              <article v-for="row in annualRows" :key="row.year" class="annual-card">
                <div class="annual-year">{{ row.year }}</div>
                <div class="annual-total">{{ moneyText(row.combinedRevenue) }}</div>
                <div class="channel-split">
                  <span>分社 {{ formatPercent(row.branchSharePct) }}</span>
                  <span>專職 {{ formatPercent(row.specialistSharePct) }}</span>
                </div>
                <div class="split-track" aria-hidden="true">
                  <span class="branch" :style="{ width: `${row.branchSharePct}%` }" />
                  <span class="specialist" :style="{ width: `${row.specialistSharePct}%` }" />
                </div>
              </article>
              <div v-if="!annualRows.length" class="empty-row">No annual data</div>
            </div>
          </div>

          <div class="panel analytics-panel">
            <div class="panel-table-title">Monthly Revenue Trend</div>
            <div class="monthly-trend">
              <div v-for="row in monthlyRows" :key="row.month" class="trend-row">
                <span class="trend-label">{{ row.month }}</span>
                <div class="trend-track">
                  <span
                    class="trend-bar"
                    :style="{ width: `${Math.max(2, Number(row.combinedRevenue || 0) / monthlyMax * 100)}%` }"
                  />
                </div>
                <strong>{{ moneyText(row.combinedRevenue) }}</strong>
              </div>
              <div v-if="!monthlyRows.length" class="empty-row">No monthly data</div>
            </div>
          </div>
        </div>

        <div class="chart-grid">
          <article class="panel chart-panel">
            <div class="panel-table-title">Monthly Revenue Trend · 近月走勢</div>
            <SvgLineChart
              title="Monthly Combined Revenue"
              subtitle="Branch + Specialist consolidated trend"
              :items="monthlyChartRows"
              x-key="label"
              :series="[
                { key: 'combinedRevenue', label: 'Combined', color: '#38bdf8' },
                { key: 'branchRevenue', label: 'Branch', color: '#22c55e' },
                { key: 'specialistRevenue', label: 'Specialist', color: '#a78bfa' }
              ]"
            />
          </article>

          <article class="panel chart-panel">
            <div class="panel-table-title">Product Mix · 團型 / 郵輪 / 票務</div>
            <SvgDonutChart
              title="Product Mix"
              subtitle="Share of total revenue by product"
              :items="productMixChartRows"
              label-key="label"
              value-key="value"
              center-label="總額"
            />
          </article>
        </div>

        <div class="product-composition-grid">
          <div class="panel analytics-panel">
            <div class="panel-table-title">Product Composition · Branch</div>
            <div v-for="row in branchProducts" :key="row.product" class="composition-row">
              <div class="composition-head">
                <span>{{ row.product }}</span>
                <strong>{{ moneyText(row.revenue) }} · {{ formatPercent(row.sharePct) }}</strong>
              </div>
              <div class="composition-track">
                <span :style="{ width: `${row.sharePct}%` }" />
              </div>
            </div>
          </div>
          <div class="panel analytics-panel">
            <div class="panel-table-title">Product Composition · Specialist</div>
            <div v-for="row in specialistProducts" :key="row.product" class="composition-row">
              <div class="composition-head">
                <span>{{ row.product }}</span>
                <strong>{{ moneyText(row.revenue) }} · {{ formatPercent(row.sharePct) }}</strong>
              </div>
              <div class="composition-track specialist">
                <span :style="{ width: `${row.sharePct}%` }" />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="ranking" class="section">
        <div class="section-head">
          <div>
            <div class="section-kicker">Ranking</div>
            <h2>Branch and specialist revenue ranking</h2>
          </div>
          <button class="action-button secondary ranking-toggle" @click="showFullRankings = !showFullRankings">
            {{ showFullRankings ? 'Show Top 10' : 'Show Full Ranking' }}
          </button>
        </div>

        <div class="chart-grid ranking-charts">
          <article class="panel chart-panel">
            <div class="panel-table-title">Branch Ranking · Top {{ branchRows.length }}</div>
            <SvgBarChart
              title="Branch Revenue Ranking"
              subtitle="Top branches by consolidated revenue"
              :items="branchRankingChartRows.slice(0, 10)"
              label-key="label"
              value-key="value"
              color="#38bdf8"
            />
          </article>
          <article class="panel chart-panel">
            <div class="panel-table-title">Specialist Ranking · Top {{ specialistRows.length }}</div>
            <SvgBarChart
              title="Specialist Revenue Ranking"
              subtitle="Top sales groups by consolidated revenue"
              :items="specialistRankingChartRows.slice(0, 10)"
              label-key="label"
              value-key="value"
              color="#22c55e"
            />
          </article>
        </div>

        <div class="ranking-layout">
        <div class="panel table-panel">
          <div class="panel-table-title">Full Branch Ranking · 顯示 {{ branchRows.length }} 筆</div>
          <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Branch</th>
                <th class="num">Tour</th>
                <th class="num">Cruise</th>
                <th class="num">Ticket</th>
                <th class="num">Total</th>
                <th class="num">Share</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in branchRows" :key="`${row.rank}-${row.branch}`">
                <td>{{ row.rank }}</td>
                <td>{{ row.branch }}</td>
                <td class="num">{{ formatNumber(row.tourRevenue) }}</td>
                <td class="num">{{ formatNumber(row.cruiseRevenue) }}</td>
                <td class="num">{{ formatNumber(row.ticketRevenue) }}</td>
                <td class="num strong">{{ formatNumber(row.totalRevenue) }}</td>
                <td class="num">{{ formatPercent(row.sharePct) }}</td>
              </tr>
              <tr v-if="!branchRows.length">
                <td colspan="7" class="empty-row">No ranking data</td>
              </tr>
            </tbody>
          </table>
          </div>
        </div>

        <div class="panel table-panel">
          <div class="panel-table-title">Full Specialist Ranking · 顯示 {{ specialistRows.length }} 筆</div>
          <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Sales Group</th>
                <th class="num">Tour</th>
                <th class="num">Cruise</th>
                <th class="num">Ticket</th>
                <th class="num">Total</th>
                <th class="num">Share</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in specialistRows" :key="`${row.rank}-${row.specialist}`">
                <td>{{ row.rank }}</td>
                <td>{{ row.specialist }}</td>
                <td class="num">{{ formatNumber(row.tourRevenue) }}</td>
                <td class="num">{{ formatNumber(row.cruiseRevenue) }}</td>
                <td class="num">{{ formatNumber(row.ticketRevenue) }}</td>
                <td class="num strong">{{ formatNumber(row.totalRevenue) }}</td>
                <td class="num">{{ formatPercent(row.sharePct) }}</td>
              </tr>
              <tr v-if="!specialistRows.length">
                <td colspan="7" class="empty-row">No specialist data</td>
              </tr>
            </tbody>
          </table>
          </div>
        </div>
        </div>
      </section>

      <section id="mix" class="section">
        <div class="section-head">
          <div>
            <div class="section-kicker">Product Mix</div>
            <h2>Quick read summary with preview rows</h2>
          </div>
          <div class="section-meta">Read-only output</div>
        </div>

        <div class="mix-summary-grid">
          <article v-for="stat in productMixStats" :key="stat.label" class="mix-summary-card">
            <div class="mix-summary-label">{{ stat.label }}</div>
            <div class="mix-summary-value">{{ stat.value }}</div>
            <div class="mix-summary-note">{{ stat.note }}</div>
          </article>
        </div>

        <div class="panel mix-panel">
          <div class="mix-panel-head">
            <span>Showing first {{ productMixPreviewRows.length }} of {{ productMixRows.length }} rows</span>
            <span class="muted">{{ summary?.revenueScope || 'read-only' }}</span>
          </div>
          <div class="table-scroll">
          <table class="data-table compact">
            <thead>
              <tr>
                <th v-for="key in productMixColumns" :key="key">
                  {{ key }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in productMixPreviewRows" :key="index">
                <td v-for="key in productMixColumns" :key="key">
                  {{ row?.[key] ?? '—' }}
                </td>
              </tr>
              <tr v-if="!productMixRows.length">
                <td class="empty-row" :colspan="productMixColumns.length || 1">No product mix data</td>
              </tr>
            </tbody>
          </table>
          </div>
        </div>
      </section>

      <section id="quality" class="section">
        <div class="section-head">
          <div><div class="section-kicker">Data Quality Scorecard</div><h2>正式數據供應鏈健康度</h2></div>
          <span class="history-status" :class="dataQuality?.overallHealth === '優秀' ? 'ok' : 'degraded'">{{ dataQuality?.overallHealth || 'pending' }}</span>
        </div>
        <div class="quality-hero-grid">
          <article class="panel quality-score"><div class="panel-title">Overall Score</div><div class="quality-score-value">{{ dataQuality?.overallScore ?? '—' }}</div><div class="panel-note">{{ dataQuality?.scope }}</div></article>
          <article class="panel stat-panel"><div class="panel-title">Latest Date</div><div class="panel-value">{{ dataQuality?.latestDate || '—' }}</div><div class="panel-note">缺失日期 {{ dataQuality?.missingDays ?? '—' }} 天</div></article>
          <article class="panel stat-panel"><div class="panel-title">Entity Resolution</div><div class="panel-value">{{ formatNumber(dataQuality?.unmatchedRows) }}</div><div class="panel-note">未匹配副表筆數</div></article>
          <article class="panel stat-panel"><div class="panel-title">Official Rows</div><div class="panel-value">{{ formatNumber(dataQuality?.officialRows) }}</div><div class="panel-note">Raw {{ formatNumber(dataQuality?.rawRows) }}</div></article>
        </div>
        <div class="panel table-panel">
          <div class="panel-table-title">Quality Dimensions</div>
          <div class="table-scroll"><table class="data-table compact">
            <thead><tr><th>維度</th><th class="num">分數</th><th>健康</th><th>關鍵指標</th></tr></thead>
            <tbody><tr v-for="row in dataQuality?.dimensions || []" :key="row.dimension">
              <td>{{ row.dimension }}</td><td class="num strong">{{ row.score }}</td>
              <td><span class="history-status" :class="row.health === '優秀' ? 'ok' : 'degraded'">{{ row.health }}</span></td><td>{{ row.metric }}</td>
            </tr></tbody>
          </table></div>
        </div>
      </section>

      <section id="forecast" class="section">
        <div class="section-head">
          <div><div class="section-kicker">Official Forecast</div><h2>Daily / 7-Day / Month-End</h2></div>
          <span class="history-status" :class="forecastInsights?.status === 'ready' ? 'ok' : 'degraded'">{{ forecastInsights?.status || 'loading' }}</span>
        </div>
        <div class="report-actions">
          <button class="action-button secondary" @click="exportReport('dashboard')" :disabled="!!reportBusy">
            {{ reportBusy === 'dashboard' ? 'Exporting...' : 'Download Dashboard Workbook' }}
          </button>
          <button class="action-button secondary" @click="exportReport('quality')" :disabled="!!reportBusy">
            {{ reportBusy === 'quality' ? 'Exporting...' : 'Download Quality Scorecard' }}
          </button>
          <button class="action-button secondary" @click="exportReport('forecast')" :disabled="!!reportBusy">
            {{ reportBusy === 'forecast' ? 'Exporting...' : 'Download Forecast Report' }}
          </button>
        </div>
        <div v-if="forecastInsights?.status !== 'ready'" class="health-issues">{{ forecastInsights?.message || 'Forecast cache is not ready.' }}</div>
        <template v-else>
          <div class="forecast-summary-grid">
            <article class="panel stat-panel"><div class="panel-title">Cache Source</div><div class="panel-value">Ready</div><div class="panel-note">{{ formatTimestamp(forecastInsights?.cache?.modifiedAt) }}</div></article>
            <article class="panel stat-panel"><div class="panel-title">7-Day Macro</div><div class="panel-value">{{ moneyText(forecastInsights?.sevenDay?.consensus) }}</div><div class="panel-note">{{ forecastInsights?.sevenDay?.windowStart }} 至 {{ forecastInsights?.sevenDay?.windowEnd }}</div></article>
            <article class="panel stat-panel"><div class="panel-title">Month-End Macro</div><div class="panel-value">{{ moneyText(forecastInsights?.monthEnd?.consensus) }}</div><div class="panel-note">{{ forecastInsights?.monthEnd?.month }} · 剩餘 {{ forecastInsights?.monthEnd?.remainingDays }} 天</div></article>
            <article class="panel stat-panel"><div class="panel-title">Macro Health</div><div class="panel-value">{{ forecastInsights?.health?.sevenDay?.health || '未評估' }}</div><div class="panel-note">7D WAPE {{ forecastInsights?.health?.sevenDay?.wape ?? '—' }}%</div></article>
          </div>
          <div class="panel chart-panel">
            <div class="panel-table-title">Forecast Curve · 未來 7 天預測圖</div>
            <SvgLineChart
              title="Daily Forecast"
              subtitle="Consensus with lower and upper bands"
              :items="forecastChartRows"
              x-key="label"
              :series="[
                { key: 'consensus', label: 'Consensus', color: '#38bdf8' },
                { key: 'lower', label: 'Lower', color: '#22c55e' },
                { key: 'upper', label: 'Upper', color: '#f97316' }
              ]"
            />
          </div>
          <div class="panel table-panel">
            <div class="panel-table-title">Daily 未來 7 天預測摘要</div>
            <div class="table-scroll"><table class="data-table compact forecast-table">
              <thead><tr><th>Date</th><th>策略</th><th class="num">ARIMA</th><th class="num">Prophet</th><th class="num">LightGBM</th><th class="num">Consensus</th><th class="num">Lower</th><th class="num">Upper</th></tr></thead>
              <tbody><tr v-for="row in (forecastInsights?.daily || []).slice(0, 7)" :key="row.date">
                <td>{{ row.date }}</td><td>{{ row.strategy }}</td><td class="num">{{ formatNumber(row.arima) }}</td><td class="num">{{ formatNumber(row.prophet) }}</td><td class="num">{{ formatNumber(row.lightgbm) }}</td><td class="num strong">{{ formatNumber(row.consensus) }}</td><td class="num">{{ formatNumber(row.lower) }}</td><td class="num">{{ formatNumber(row.upper) }}</td>
              </tr></tbody>
            </table></div>
          </div>
        </template>
      </section>

      <section id="upload" class="section">
        <div class="section-head">
          <div>
            <div class="section-kicker">Phase 2P Formal Upload</div>
            <h2>Vue Upload</h2>
          </div>
          <span class="history-status" :class="uploadStatusTone">{{ uploadResult?.status || 'pending' }}</span>
        </div>

        <div class="panel upload-panel">
          <div class="panel-table-title">Upload Intake</div>
          <div class="panel-note upload-note">至少需要主表，並提供旅行團副表或其他副表其一。</div>
          <div class="upload-grid" :key="uploadFormKey">
            <label class="upload-field">
              <span>主表</span>
              <input type="file" accept=".xlsx,.xls" @change="handleMainFileChange" />
              <strong>{{ uploadMainFile?.name || '尚未選擇' }}</strong>
            </label>
            <label class="upload-field">
              <span>旅行團副表</span>
              <input type="file" accept=".xlsx,.xls" @change="handleTourFileChange" />
              <strong>{{ uploadTourFile?.name || '可選' }}</strong>
            </label>
            <label class="upload-field">
              <span>其他副表</span>
              <input type="file" accept=".xlsx,.xls" multiple @change="handleOtherFilesChange" />
              <strong>{{ uploadOtherFiles.length ? `${uploadOtherFiles.length} files` : '可多選' }}</strong>
            </label>
          </div>

          <div class="button-row upload-actions">
            <button class="action-button primary" :disabled="uploadBusy" @click="submitVueUpload">
              {{ uploadBusy ? '執行中...' : '上傳並驗證' }}
            </button>
            <button class="action-button secondary" :disabled="uploadBusy" @click="resetUploadForm">
              重設
            </button>
          </div>

          <div v-if="uploadError" class="health-issues">
            <strong>Upload Error</strong>
            <div>{{ uploadError }}</div>
          </div>

          <div v-if="uploadResult" class="acceptance-status-grid upload-summary-grid">
            <article class="panel stat-panel acceptance-stat-panel">
              <div class="panel-title">Message</div>
              <div class="panel-value">{{ uploadResult.message }}</div>
              <div class="panel-note">{{ uploadResult.sourceFiles?.join(', ') || '—' }}</div>
            </article>
            <article class="panel stat-panel acceptance-stat-panel">
              <div class="panel-title">Preflight</div>
              <div class="panel-value">{{ preflightStatusText(uploadResult.preflightReport?.status) }}</div>
              <div class="panel-note">{{ uploadResult.preflightReport?.message || '—' }}</div>
            </article>
            <article class="panel stat-panel acceptance-stat-panel">
              <div class="panel-title">Rollback</div>
              <div class="panel-value">{{ uploadResult.rollbackResult?.rollbackStatus || 'not_required' }}</div>
              <div class="panel-note">{{ uploadResult.rollbackResult?.rollbackError || '—' }}</div>
            </article>
            <article class="panel stat-panel acceptance-stat-panel">
              <div class="panel-title">History</div>
              <div class="panel-value">{{ uploadResult.historyRecordId ? `#${uploadResult.historyRecordId}` : '—' }}</div>
              <div class="panel-note">{{ uploadResult.writeCommitted ? '寫入已完成' : '未寫入' }}</div>
            </article>
          </div>

          <div v-if="uploadResult?.preflightReport" class="table-scroll acceptance-history-scroll">
            <table class="data-table compact acceptance-history-table">
              <thead>
                <tr>
                  <th>Preflight</th>
                  <th>Actual</th>
                  <th>Delta</th>
                  <th>Rows</th>
                  <th>Drift Diagnosis</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{{ uploadResult.preflightReport.formattedExpectedTotal || '—' }}</td>
                  <td>{{ uploadResult.preflightReport.formattedActualTotal || '—' }}</td>
                  <td>{{ moneyText(uploadResult.preflightReport.deltaAmount) }}</td>
                  <td>
                    {{ uploadResult.preflightReport.writeRows || 0 }} /
                    {{ uploadResult.preflightReport.filteredExcludedRows || 0 }}
                  </td>
                  <td>{{ uploadResult.preflightReport.driftDiagnosis?.summaryMessage || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section id="history" class="section">
        <div class="section-head">
          <div>
            <div class="section-kicker">Phase 2G Acceptance History</div>
            <h2>上傳口徑驗收歷史</h2>
          </div>
          <div class="section-meta">最近 {{ stabilityHistory.length }} 筆</div>
        </div>

        <div class="panel table-panel history-panel">
          <div class="table-scroll">
            <table class="data-table compact history-table">
              <thead>
                <tr>
                  <th>時間</th>
                  <th>Record</th>
                  <th>來源檔案</th>
                  <th>處理結果</th>
                  <th>核心口徑</th>
                  <th>診斷</th>
                  <th>資料更新</th>
                  <th>最新日期</th>
                  <th>Actual</th>
                  <th>Delta</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="record in stabilityHistory" :key="record.id">
                  <td>{{ formatTimestamp(record.createdAt) }}</td>
                  <td>#{{ record.id }}</td>
                  <td class="history-files">{{ record.sourceFiles?.join(', ') || '—' }}</td>
                  <td>
                    <span class="history-status" :class="record.uploadStatus">{{ historyStatusText(record.uploadStatus) }}</span>
                    <div class="history-subtext">{{ record.rollbackStatus || 'not_required' }}</div>
                  </td>
                  <td>
                    <span class="history-status" :class="record.coreStatus">{{ record.coreStatus }}</span>
                    <div class="history-subtext">{{ record.matchedChecks }}/{{ record.totalChecks }} matched</div>
                  </td>
                  <td>
                    <span class="history-status" :class="diagnosisTone(record.driftDiagnosis?.status)">
                      {{ diagnosisStatusText(record.driftDiagnosis?.status) }}
                    </span>
                    <div class="history-subtext">{{ record.driftDiagnosis?.summaryMessage || '—' }}</div>
                  </td>
                  <td>
                    <span class="history-status" :class="record.freshnessStatus">{{ record.freshnessStatus }}</span>
                    <div class="history-subtext">{{ record.freshnessUpdateCount }} updates</div>
                  </td>
                  <td>{{ record.latestDataDate || '—' }}</td>
                  <td class="num strong">{{ record.formattedActualTotal || '—' }}</td>
                  <td class="num">{{ moneyText(record.deltaAmount) }}</td>
                </tr>
                <tr v-if="!stabilityHistory.length">
                  <td colspan="10" class="empty-row">尚未保存驗收歷史；下一次完成上傳重建後會自動建立紀錄。</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section id="api" class="section">
        <div class="section-head">
          <div>
            <div class="section-kicker">API Status</div>
            <h2>Health and scope audit</h2>
          </div>
          <div class="section-meta">{{ health?.service || 'nbs-analytics-api' }}</div>
        </div>

        <div class="panel table-panel freshness-panel">
          <div class="panel-table-title">Acceptance Status Panel</div>
          <div class="acceptance-status-grid">
            <article
              v-for="card in acceptanceStatusCards"
              :key="card.label"
              class="panel stat-panel acceptance-stat-panel"
            >
              <div class="panel-title">{{ card.label }}</div>
              <div class="panel-value">{{ card.value }}</div>
              <div class="panel-note">{{ card.note }}</div>
              <div class="capacity-state" :class="{ warning: card.tone === 'critical' }">
                {{ card.tone === 'ok' ? '可讀' : card.tone === 'critical' ? '需關注' : '透明化' }}
              </div>
            </article>
          </div>
          <div class="table-scroll acceptance-history-scroll">
            <table class="data-table compact acceptance-history-table">
              <thead>
                <tr>
                  <th>Record</th>
                  <th>Upload</th>
                  <th>Preflight</th>
                  <th>Rollback</th>
                  <th>History Note</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="record in recentAcceptanceRows" :key="record.id">
                  <td>
                    #{{ record.id }}
                    <div class="history-subtext">{{ formatTimestamp(record.createdAt) }}</div>
                  </td>
                  <td>
                    <span class="history-status" :class="record.uploadStatus">{{ historyStatusText(record.uploadStatus) }}</span>
                    <div class="history-subtext">{{ record.uploadMessage || '—' }}</div>
                  </td>
                  <td>
                    <span class="history-status" :class="diagnosisTone(record.driftDiagnosis?.status)">
                      {{ diagnosisStatusText(record.driftDiagnosis?.status) }}
                    </span>
                    <div class="history-subtext">{{ record.latestDiagnosisSourceLabel || '—' }}</div>
                  </td>
                  <td>
                    <span class="history-status" :class="record.rollbackStatus || 'muted'">
                      {{ record.rollbackStatus || 'not_required' }}
                    </span>
                    <div class="history-subtext">{{ record.rollbackError || '—' }}</div>
                  </td>
                  <td class="history-files">{{ record.sourceFiles?.join(', ') || '—' }}</td>
                </tr>
                <tr v-if="!recentAcceptanceRows.length">
                  <td colspan="5" class="empty-row">尚未建立驗收快照；完成一次上傳預演或重建後，這裡會顯示最近狀態。</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="status-grid">
          <article class="panel stat-panel">
            <div class="panel-title">SQLite Integrity</div>
            <div class="panel-value">{{ health?.db?.integrity || 'pending' }}</div>
            <div class="panel-note">{{ health?.db?.integrityOk ? 'Database verified' : 'Requires attention' }}</div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Latest Acceptance</div>
            <div class="panel-value">{{ health?.latestAcceptance?.uploadStatus || 'No history' }}</div>
            <div class="panel-note">
              Gate {{ health?.latestAcceptance?.coreStatus || '—' }} ·
              Rollback {{ health?.latestAcceptance?.rollbackStatus || 'not_required' }} ·
              Drift {{ diagnosisStatusText(health?.latestAcceptance?.driftDiagnosis?.status) }} ·
              {{ health?.latestAcceptance?.latestDiagnosisSourceLabel || latestDiagnosisSourceLabel }}
            </div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Backup Storage</div>
            <div class="panel-value">{{ health?.storage?.backups?.count ?? 0 }} backups</div>
            <div class="panel-note">
              {{ formatBytes(health?.storage?.backups?.totalBytes) }} ·
              {{ health?.storage?.quarantines?.count ?? 0 }} quarantines
            </div>
            <div
              class="capacity-state"
              :class="{ warning: health?.storage?.backups?.capacityWarning }"
            >
              {{ health?.storage?.backups?.capacityWarning ? '超過 3 GB 警戒線' : '3 GB 容量範圍內' }}
            </div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Runtime Cache</div>
            <div class="panel-value">{{ health?.runtimeCache?.fileCount ?? 0 }} files</div>
            <div class="panel-note">{{ formatBytes(health?.runtimeCache?.totalBytes) }}</div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Health</div>
            <div class="panel-value">{{ health?.status || 'loading' }}</div>
            <div class="panel-note">{{ health?.db?.path || 'API not ready' }}</div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Scope</div>
            <div class="panel-value">{{ summary?.revenueScope || 'pending' }}</div>
            <div class="panel-note">Data is read-only from Python backend</div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Audit</div>
            <div class="panel-value">{{ summary?.scopeAudit?.excluded_order_count ?? '—' }}</div>
            <div class="panel-note">Excluded order IDs</div>
          </article>
          <article class="panel stat-panel">
            <div class="panel-title">Facts Source</div>
            <div class="panel-value">{{ factsSourceStatus }}</div>
            <div class="panel-note">
              {{ facts.value?.serviceVersion || '—' }} ·
              Cache {{ facts.value?.factsCacheStatus || '—' }} ·
              Reconciliation {{ facts.value?.reconciliation?.status || '—' }}
            </div>
            <div class="panel-note">
              Generation {{ facts.value?.generationToken || '—' }} ·
              全域合計 {{ facts ? moneyText(facts.value?.kpiTotals?.combinedRevenue) : '—' }}
            </div>
            <div v-if="factsError" class="health-issues">Facts source unavailable：{{ factsError }}</div>
          </article>
        </div>

        <div v-if="health?.issues?.length" class="health-issues">
          <strong>Operational issues</strong>
          <ul>
            <li v-for="issue in health.issues" :key="issue">{{ issue }}</li>
          </ul>
        </div>

        <div class="panel table-panel freshness-panel">
          <div class="panel-table-title">Operational Health History</div>
          <div class="table-scroll">
            <table class="data-table compact operational-history-table">
              <thead>
                <tr>
                  <th>時間</th>
                  <th>狀態</th>
                  <th>SQLite</th>
                  <th>最新資料</th>
                  <th>Backup</th>
                  <th>Streamlit</th>
                  <th>API</th>
                  <th>Vue</th>
                  <th>診斷</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="record in health?.operationalHistory || []" :key="record.createdAt">
                  <td>{{ formatTimestamp(record.createdAt) }}</td>
                  <td><span class="history-status" :class="record.status">{{ record.status }}</span></td>
                  <td>{{ record.sqliteIntegrity || '—' }}</td>
                  <td>{{ record.latestDataDate || '—' }}</td>
                  <td>{{ formatBytes(record.backupBytes) }}</td>
                  <td>{{ record.endpoints?.streamlit?.ready ? 'Ready' : 'Down' }}</td>
                  <td>{{ record.endpoints?.api?.ready ? 'Ready' : 'Down' }}</td>
                  <td>{{ record.endpoints?.vue?.ready ? 'Ready' : 'Down' }}</td>
                  <td>{{ diagnosisStatusText(record.driftDiagnosis?.status) }}</td>
                </tr>
                <tr v-if="!health?.operationalHistory?.length">
                  <td colspan="9" class="empty-row">
                    尚未建立監控快照；執行 system_manager.py monitor 後會顯示歷史。
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="panel table-panel freshness-panel">
          <div class="panel-table-title">Data Freshness</div>
          <div class="table-scroll">
          <table class="data-table compact">
            <tbody>
              <tr v-for="row in freshnessRows" :key="row[0]">
                <th>{{ row[0] }}</th>
                <td>{{ row[1] }}</td>
              </tr>
              <tr>
                <th>正式口徑</th>
                <td>{{ summary?.revenueTotals?.scope || summary?.revenueScope || '不含掛賬核銷與TT退款轉團款' }}</td>
              </tr>
            </tbody>
          </table>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>
