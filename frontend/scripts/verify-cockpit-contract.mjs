import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(import.meta.dirname, '..')
const app = readFileSync(resolve(root, 'src/App.vue'), 'utf8')
const api = readFileSync(resolve(root, 'src/lib/api.js'), 'utf8')

const requiredAppContracts = [
  'summary.value?.revenueTotals',
  'summary.value?.dataFreshness',
  'analytics.value?.branchRanking',
  'analytics.value?.specialistRanking',
  'summary.value?.stabilityBaseline',
  'getStabilityHistory',
  'Acceptance History',
  'Drift Diagnosis',
  'latestAcceptance',
  'latestDiagnosis',
  'latestDiagnosisSourceLabel',
  'health?.latestAcceptance?.latestDiagnosisSourceLabel',
  'diagnosisStatusText',
  'diagnosisTone',
  'driftDiagnosis',
  'stabilityHistory',
  'history-status',
  'rollbackStatus',
  'rejected_rolled_back',
  'rollback_failed',
  'SQLite Integrity',
  'Latest Acceptance',
  'Backup Storage',
  'health?.storage?.backups',
  'health?.runtimeCache?.fileCount',
  'Operational Health History',
  'health?.operationalHistory',
  'Acceptance Status Panel',
  'acceptanceStatusCards',
  'recentAcceptanceRows',
  'Latest Upload',
  'Latest Preflight',
  'Latest Rollback',
  'History Note',
  'capacityWarning',
  '3 GB',
  'stabilityBaseline.value?.coreValidation',
  'stabilityBaseline.value?.freshnessUpdate',
  'Phase 2B Stability Monitor',
  '核心口徑驗收',
  '資料更新狀態',
  'Phase 2D Filter Summary',
  'toggleYear',
  'toggleMonth',
  'filterSummaryItems',
  'table-scroll',
  'HKD 12,057,968',
  '2026-06-22',
  'applyFilters',
  'scrollToSection'
  ,'getDashboardAnalytics'
  ,'Annual Channel Summary'
  ,'Monthly Revenue Trend'
  ,'Full Branch Ranking'
  ,'Product Composition'
  ,'analytics?.reconciliation'
  ,'Latest Drift Diagnosis'
  ,'Phase 2P Formal Upload'
  ,'Vue Upload'
  ,'uploadMonthlyData'
  ,'submitVueUpload'
  ,'preflightStatusText'
  ,'writeCommitted'
  ,'preflightReport'
  ,'Record #'
  ,'Pending'
  ,'Top drivers capped at 50 rows'
  ,'drift_diagnosis.xlsx'
  ,'getDataQuality'
  ,'getForecastInsights'
  ,'Data Quality Scorecard'
  ,'Official Forecast'
  ,'7-Day Macro'
  ,'Month-End Macro'
  ,'forecastInsights?.status'
  ,'SvgLineChart'
  ,'SvgBarChart'
  ,'SvgDonutChart'
  ,'Monthly Combined Revenue'
  ,'Product Mix'
  ,'Branch Revenue Ranking'
  ,'Specialist Revenue Ranking'
  ,'Daily Forecast'
  ,'Download Dashboard Workbook'
  ,'Download Quality Scorecard'
  ,'Download Forecast Report'
  ,'downloadDashboardReport'
  ,'downloadQualityReport'
  ,'downloadForecastReport'
]

for (const token of requiredAppContracts) {
  if (!app.includes(token)) {
    throw new Error(`App.vue is missing cockpit contract token: ${token}`)
  }
}

if (!api.includes('/api/dashboard/context') || !api.includes('/api/dashboard/summary')) {
  throw new Error('API client must use dashboard context and summary endpoints.')
}

if (!api.includes('/api/dashboard/facts') || !api.includes('getDashboardFacts')) {
  throw new Error('Vue must consume the Dashboard Facts API.')
}

for (const token of ['getDashboardFacts', 'facts.value?.generationToken', 'facts.value?.factsCacheStatus', 'Facts Source']) {
  if (!app.includes(token)) throw new Error(`App.vue is missing Facts consumer token: ${token}`)
}

if (app.includes('v-model="filters.years" multiple') || app.includes('v-model="filters.months" multiple')) {
  throw new Error('Phase 2D cockpit must not use cramped native multi-select controls for years/months.')
}

const scrollBody = app.slice(app.indexOf('function scrollToSection'), app.indexOf('async function applyFilters'))
if (scrollBody.includes('getDashboardSummary')) {
  throw new Error('Navigation scrollToSection must not call dashboard summary API.')
}

console.log('Vue cockpit contract verified.')
