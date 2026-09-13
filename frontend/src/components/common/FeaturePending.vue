<script setup>
import EmptyState from './EmptyState.vue'

/**
 * The honest "not built yet" state.
 *
 * Used by surfaces whose backend endpoint does not exist. It says so plainly
 * and names what will appear here. It never renders placeholder rows, sample
 * charts or example figures — a demo that looks like data is a demo that gets
 * quoted in a claim submission.
 */
defineProps({
  title: { type: String, required: true },
  description: { type: String, required: true },
  icon: { type: String, default: 'pi pi-wrench' },
  /** REST path this surface will read from, shown for operator transparency. */
  endpoint: { type: String, default: null },
  compact: { type: Boolean, default: false },
})
</script>

<template>
  <EmptyState :icon="icon" :title="title" :description="description" :compact="compact">
    <p v-if="endpoint" class="pending__endpoint">
      Will read from <code>{{ endpoint }}</code>
    </p>
  </EmptyState>
</template>

<style scoped>
.pending__endpoint {
  margin-top: var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
