<script setup>
import { computed } from 'vue'

import AppButton from './AppButton.vue'

/**
 * Page controls for a register, with the total stated.
 *
 * The total is not decoration. A list that silently shows the first hundred
 * rows of three hundred reads as a complete register, and a user who scrolls
 * to the end of it concludes the record does not contain what they are
 * looking for.
 */
const props = defineProps({
  page: { type: Number, required: true },
  pageCount: { type: Number, required: true },
  total: { type: Number, default: null },
  /** Plural noun for the total, e.g. "documents". */
  unit: { type: String, default: 'rows' },
})

const emit = defineEmits(['update:page'])

const summary = computed(() => {
  const parts = []
  if (props.total != null) parts.push(`${props.total} ${props.unit}`)
  if (props.pageCount > 1) parts.push(`page ${props.page} of ${props.pageCount}`)
  return parts.join(' · ')
})
</script>

<template>
  <footer class="pager">
    <span class="text-xs text-muted">{{ summary }}</span>
    <div v-if="pageCount > 1" class="row gap-2">
      <AppButton
        size="sm"
        label="Previous"
        icon="pi pi-angle-left"
        :disabled="page <= 1"
        @click="emit('update:page', page - 1)"
      />
      <AppButton
        size="sm"
        label="Next"
        icon-right="pi pi-angle-right"
        :disabled="page >= pageCount"
        @click="emit('update:page', page + 1)"
      />
    </div>
  </footer>
</template>

<style scoped>
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-subtle);
}
</style>
