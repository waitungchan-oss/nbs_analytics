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
  labelKey: {
    type: String,
    default: 'label'
  },
  valueKey: {
    type: String,
    default: 'value'
  },
  centerLabel: {
    type: String,
    default: ''
  }
})

const palette = ['#38bdf8', '#22c55e', '#a78bfa', '#f97316', '#ec4899', '#eab308']

const total = computed(() =>
  (props.items || []).reduce((sum, item) => sum + Number(item?.[props.valueKey] || 0), 0)
)

const segments = computed(() => {
  const circumference = 2 * Math.PI * 42
  let offset = 0
  return (props.items || []).map((item, index) => {
    const value = Number(item?.[props.valueKey] || 0)
    const share = total.value ? value / total.value : 0
    const dash = share * circumference
    const segment = {
      label: item?.[props.labelKey] || `Series ${index + 1}`,
      value,
      color: item?.color || palette[index % palette.length],
      dash,
      offset
    }
    offset += dash
    return segment
  })
})

function formatNumber(value) {
  return Number(value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })
}

function formatPercent(value) {
  return `${Number(value || 0).toFixed(1)}%`
}
</script>

<template>
  <div class="svg-chart">
    <div v-if="title || subtitle" class="svg-chart-head">
      <div class="svg-chart-title">{{ title }}</div>
      <div v-if="subtitle" class="svg-chart-subtitle">{{ subtitle }}</div>
    </div>

    <div class="donut-layout">
      <svg class="svg-chart-canvas donut-canvas" viewBox="0 0 220 220" role="img" :aria-label="title || subtitle || 'donut chart'">
        <circle cx="110" cy="110" r="42" class="donut-track" />
        <circle
          v-for="segment in segments"
          :key="segment.label"
          cx="110"
          cy="110"
          r="42"
          fill="none"
          stroke-linecap="round"
          stroke-width="16"
          :stroke="segment.color"
          :stroke-dasharray="`${segment.dash} ${2 * Math.PI * 42 - segment.dash}`"
          :stroke-dashoffset="-(segment.offset)"
          transform="rotate(-90 110 110)"
        />
        <text x="110" y="100" text-anchor="middle" class="donut-center-label">
          {{ centerLabel || '總額' }}
        </text>
        <text x="110" y="126" text-anchor="middle" class="donut-center-value">
          {{ formatNumber(total) }}
        </text>
      </svg>

      <div class="svg-chart-legend donut-legend">
        <span v-for="segment in segments" :key="segment.label" class="legend-item">
          <i :style="{ background: segment.color }" />
          {{ segment.label }} ({{ formatPercent(total ? segment.value / total * 100 : 0) }})
        </span>
      </div>
    </div>
  </div>
</template>
