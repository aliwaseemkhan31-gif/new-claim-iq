<script setup>
import { computed } from 'vue'

/**
 * Says so when a list is showing less than the record holds.
 *
 * For the panels that cannot be paged without breaking the order they are
 * sorted in. Silence is the worse option: a register that quietly stops at
 * its first hundred rows reads as complete, and someone who scrolls to the
 * bottom of it concludes the record does not contain what they are looking
 * for.
 */
const props = defineProps({
  shown: { type: Number, required: true },
  total: { type: Number, default: null },
  unit: { type: String, default: 'rows' },
})

const truncated = computed(() => props.total != null && props.total > props.shown)
</script>

<template>
  <p v-if="truncated" class="limit" role="status">
    <i class="pi pi-info-circle" aria-hidden="true" />
    Showing the first {{ shown }} of {{ total }} {{ unit }}. Narrow the filter to see the rest.
  </p>
</template>

<style scoped>
.limit {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  border-top: 1px solid var(--color-border-subtle);
}
</style>
