<script setup>
import { computed } from 'vue'

const props = defineProps({
  title: {
    type: String,
    default: ''
  },
  subtitle: {
    type: String,
    default: ''
  },
  items: {
    type: Array,
    default: () => []
  },
  xKey: {
    type: String,
    default: 'label'
  },
  series: {
    type: Array,
    default: () => []
  }
})

const palette = ['#38bdf8', '#22c55e', '#a78bfa', '#f97316']

const normalizedSeries = computed(() =>
  (props.series || []).map((series, index) => ({
    color: series.color || palette[index % palette.length],
    key: series.key,
    label: series.label || series.key
  }))
)

const chartBounds = computed(() => {
  const values = []
  for (const item of props.items || []) {
    for (const series of normalizedSeries.value) {
      const value = Number(item?.[series.key] || 0)
      if (Number.isFinite(value)) values.push(value)
    }
  }
  const max = values.length ? Math.max(...values) : 1
  const min = values.length ? Math.min(...values) : 0
  const padding = Math.max(1, (max - min) * 0.12)
  return {
    min: Math.max(0, min - padding),
    max: max + padding
  }
})

const chartRows = computed(() => {
  const width = 960
  const height = 280
  const padding = { top: 20, right: 24, bottom: 44, left: 54 }
  const innerWidth = width - padding.left - padding.right
  const innerHeight = height - padding.top - padding.bottom
  const itemCount = Math.max((props.items || []).length - 1, 1)
  const range = Math.max(1, chartBounds.value.max - chartBounds.value.min)

  return normalizedSeries.value.map(series => {
    const points = (props.items || []).map((item, index) => {
      const rawValue = Number(item?.[series.key] || 0)
      const x = padding.left + (innerWidth * index) / itemCount
      const y =
        padding.top + innerHeight - ((rawValue - chartBounds.value.min) / range) * innerHeight
      return {
        x,
        y,
        value: rawValue,
        label: String(item?.[props.xKey] ?? '')
      }
    })
    const path = points
      .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
      .join(' ')
    return { ...series, points, path }
  })
})

const xLabels = computed(() => (props.items || []).map(item => String(item?.[props.xKey] ?? '')))

function formatNumber(value) {
  return Number(value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })
}
</script>

<template>
  <div class="svg-chart">
    <div v-if="title || subtitle" class="svg-chart-head">
      <div class="svg-chart-title">{{ title }}</div>
      <div v-if="subtitle" class="svg-chart-subtitle">{{ subtitle }}</div>
    </div>

    <svg class="svg-chart-canvas" viewBox="0 0 960 280" role="img" :aria-label="title || subtitle || 'line chart'">
      <g v-for="step in 4" :key="step" class="grid-line">
        <line
          x1="54"
          :x2="936"
          :y1="20 + ((260 / 4) * step)"
          :y2="20 + ((260 / 4) * step)"
        />
      </g>

      <g v-for="(series, seriesIndex) in chartRows" :key="series.key">
        <path :d="series.path" :stroke="series.color" fill="none" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" />
        <circle
          v-for="(point, pointIndex) in series.points"
          :key="`${series.key}-${pointIndex}`"
          :cx="point.x"
          :cy="point.y"
          :r="pointIndex === series.points.length - 1 ? 5 : 4"
          :fill="series.color"
        />
        <text
          v-for="(point, pointIndex) in series.points"
          :key="`${series.key}-${pointIndex}-label`"
          :x="point.x"
          :y="point.y - 10"
          text-anchor="middle"
          class="chart-point-label"
        >
          {{ pointIndex === series.points.length - 1 ? formatNumber(point.value) : '' }}
        </text>
      </g>

      <g v-for="(label, index) in xLabels" :key="`${label}-${index}`">
        <text
          :x="54 + ((906) * index) / Math.max(xLabels.length - 1, 1)"
          y="262"
          text-anchor="middle"
          class="chart-axis-label"
        >
          {{ label }}
        </text>
      </g>

      <text x="16" y="28" class="chart-axis-label chart-axis-label-strong">
        {{ formatNumber(chartBounds.max) }}
      </text>
      <text x="16" y="262" class="chart-axis-label chart-axis-label-strong">
        {{ formatNumber(chartBounds.min) }}
      </text>
    </svg>

    <div v-if="normalizedSeries.length" class="svg-chart-legend">
      <span v-for="series in normalizedSeries" :key="series.key" class="legend-item">
        <i :style="{ background: series.color }" />
        {{ series.label }}
      </span>
    </div>
  </div>
</template>
