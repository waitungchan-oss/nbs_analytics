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
  color: {
    type: String,
    default: '#38bdf8'
  }
})

const chartHeight = computed(() => Math.max(160, 34 * (props.items?.length || 0) + 28))

const maxValue = computed(() =>
  Math.max(1, ...(props.items || []).map(item => Number(item?.[props.valueKey] || 0)))
)

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

    <svg class="svg-chart-canvas" :viewBox="`0 0 960 ${chartHeight}`" role="img" :aria-label="title || subtitle || 'bar chart'">
      <g v-for="(item, index) in items" :key="`${item?.[labelKey] || index}`">
        <text :x="18" :y="28 + index * 34" class="chart-bar-label">
          {{ item?.[labelKey] }}
        </text>
        <rect x="190" :y="12 + index * 34" width="720" height="18" rx="9" class="chart-bar-track" />
        <rect
          x="190"
          :y="12 + index * 34"
          :width="Math.max(10, 720 * (Number(item?.[valueKey] || 0) / maxValue))"
          height="18"
          rx="9"
          :fill="item?.color || color"
        />
        <text x="924" :y="28 + index * 34" text-anchor="end" class="chart-bar-value">
          {{ formatNumber(item?.[valueKey]) }}
        </text>
      </g>
    </svg>
  </div>
</template>
