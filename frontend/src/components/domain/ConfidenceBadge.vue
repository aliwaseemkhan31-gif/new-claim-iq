<script setup>
import { computed } from 'vue'

import StatusBadge from '@/components/common/StatusBadge.vue'

const props = defineProps({
  level: { type: String, default: null },
  reasons: { type: Array, default: () => [] },
})

const TONES = { high: 'success', medium: 'warning', low: 'danger' }
const tone = computed(() => TONES[props.level] ?? 'neutral')
const label = computed(() => (props.level ? `Confidence: ${props.level}` : 'Confidence not assessed'))
const title = computed(() => props.reasons.join('\n'))
</script>

<template>
  <span :title="title">
    <StatusBadge :tone="tone" :label="label" size="sm" />
  </span>
</template>
