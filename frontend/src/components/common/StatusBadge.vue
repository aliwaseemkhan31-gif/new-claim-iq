<script setup>
import { computed } from 'vue'

import { TONES, humaniseStatus, statusTone } from '@/constants/status'

/**
 * Status pill.
 *
 * The tone comes from the shared status vocabulary, so "approved" reads the
 * same green whether it is a claim, a report or a document. Anything
 * unrecognised falls back to neutral — an unknown status must never be
 * coloured as if it were understood.
 */
const props = defineProps({
  /** Machine status value, e.g. `in_review`. */
  status: { type: String, default: null },
  /** Override the displayed text; otherwise the status is humanised. */
  label: { type: String, default: null },
  /** Force a tone regardless of the status vocabulary. */
  tone: {
    type: String,
    default: null,
    validator: (v) => v === null || TONES.includes(v),
  },
  size: { type: String, default: 'md', validator: (v) => ['sm', 'md'].includes(v) },
  /** Show a leading dot instead of relying on colour alone at a glance. */
  dot: { type: Boolean, default: true },
})

const resolvedTone = computed(() => props.tone || statusTone(props.status))

const displayLabel = computed(() => props.label || humaniseStatus(props.status))
</script>

<template>
  <span
    class="badge"
    :class="[`badge--${resolvedTone}`, `badge--${size}`]"
    :data-tone="resolvedTone"
    data-testid="status-badge"
  >
    <span v-if="dot" class="badge__dot" aria-hidden="true" />
    {{ displayLabel }}
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  line-height: 18px;
  white-space: nowrap;
  border: 1px solid transparent;
  border-radius: var(--radius-full);
}

.badge--md {
  padding: 0 var(--space-2);
}

.badge--sm {
  padding: 0 var(--space-1) 0 var(--space-2);
  font-size: var(--text-2xs);
  line-height: 16px;
}

.badge__dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentcolor;
  flex-shrink: 0;
}

.badge--neutral {
  color: var(--color-neutral-badge);
  background: var(--color-neutral-badge-subtle);
  border-color: var(--color-neutral-badge-border);
}

.badge--info {
  color: var(--color-info);
  background: var(--color-info-subtle);
  border-color: var(--color-info-border);
}

.badge--success {
  color: var(--color-success);
  background: var(--color-success-subtle);
  border-color: var(--color-success-border);
}

.badge--warning {
  color: var(--color-warning);
  background: var(--color-warning-subtle);
  border-color: var(--color-warning-border);
}

.badge--danger {
  color: var(--color-danger);
  background: var(--color-danger-subtle);
  border-color: var(--color-danger-border);
}
</style>
