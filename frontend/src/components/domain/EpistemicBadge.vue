<script setup>
import { computed } from 'vue'

import StatusBadge from '@/components/common/StatusBadge.vue'

/** Fact, inference, opinion or unknown — the status of an AI assertion. */
const props = defineProps({
  status: { type: String, default: null },
})

const MAP = {
  fact: { tone: 'success', label: 'Fact', title: 'Established by the cited source.' },
  inference: { tone: 'info', label: 'Inference', title: 'Reasoned from the cited sources.' },
  opinion: { tone: 'warning', label: 'Opinion', title: 'A professional view, not established by the sources.' },
  unknown: { tone: 'neutral', label: 'Unknown', title: 'The sources do not establish this.' },
}

const entry = computed(() => MAP[props.status] ?? { tone: 'neutral', label: props.status || 'Unknown', title: '' })
</script>

<template>
  <span :title="entry.title">
    <StatusBadge :tone="entry.tone" :label="entry.label" size="sm" />
  </span>
</template>
