const API_BASE = ''

async function requestJson(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    },
    ...options
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(`${response.status} ${response.statusText}${text ? `: ${text}` : ''}`)
  }

  return response.json()
}

async function requestBinary(path, options = {}) {
  const { filename, ...fetchOptions } = options
  const response = await fetch(`${API_BASE}${path}`, {
    ...fetchOptions
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(`${response.status} ${response.statusText}${text ? `: ${text}` : ''}`)
  }

  const contentDisposition = response.headers.get('content-disposition') || ''
  const match = /filename="?([^"]+)"?/i.exec(contentDisposition)
  return {
    blob: await response.blob(),
    filename: match?.[1] || filename || 'report.xlsx'
  }
}

export function getHealth() {
  return requestJson('/api/health')
}

export function getDashboardContext() {
  return requestJson('/api/dashboard/context')
}

export function getDashboardFacts() {
  return requestJson('/api/dashboard/facts')
}

export function getDashboardSummary(filters) {
  return requestJson('/api/dashboard/summary', {
    method: 'POST',
    body: JSON.stringify(filters)
  })
}

export function getDashboardAnalytics(filters) {
  return requestJson('/api/dashboard/analytics', {
    method: 'POST',
    body: JSON.stringify(filters)
  })
}

export function getStabilityHistory(limit = 20) {
  return requestJson(`/api/stability/history?limit=${limit}`)
}

export function getDataQuality() {
  return requestJson('/api/insights/data-quality')
}

export function getForecastInsights() {
  return requestJson('/api/insights/forecast')
}

export function downloadDashboardReport(filters) {
  return requestBinary('/api/exports/dashboard.xlsx', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(filters)
  })
}

export function downloadQualityReport() {
  return requestBinary('/api/exports/quality.xlsx')
}

export function downloadForecastReport() {
  return requestBinary('/api/exports/forecast.xlsx')
}

export async function uploadMonthlyData(formData) {
  const response = await fetch('/api/upload', {
    method: 'POST',
    body: formData
  })

  if (!response.ok) {
    const text = await response.text()
    throw new Error(`${response.status} ${response.statusText}${text ? `: ${text}` : ''}`)
  }

  return response.json()
}
