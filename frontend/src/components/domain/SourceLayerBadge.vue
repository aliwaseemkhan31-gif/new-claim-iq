<script setup>
import { computed } from 'vue'

import { LAYER_STANDARD_FORM, layerLabel, sourceLayer } from '@/utils/viewer'

/**
 * Which corpus a source belongs to. Shown on every citation, search result and
 * source list, so a reader can never mistake standard-form text for what this
 * project's contract says.
 */
const props = defineProps({
  source: { type: Object, required: true },
  size: { type: String, default: 'md', validator: (v) => ['sm', 'md'].includes(v) },
})

const isStandardForm = computed(() => sourceLayer(props.source) === LAYER_STANDARD_FORM)
const label = computed(() => layerLabel(props.source))
const explanation = computed(() =>
  isStandardForm.value
    ? 'Unamended standard-form text. The project’s own contract documents govern where they differ.'
    : 'A document in this project’s own record.'
)
</script>

<template>
  <span
    class="layer"
    :class="[isStandardForm ? 'layer--standard' : 'layer--project', `layer--${size}`]"
    :title="explanation"
    data-testid="source-layer"
  >
    <i :class="isStandardForm ? 'pi pi-book' : 'pi pi-folder'" aria-hidden="true" />
    {{ label }}
  </span>
</template>

<style scoped>
.layer {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px var(--space-2);
  font-size: var(--text-2xs);
  font-weight: var(--weight-medium);
  line-height: 18px;
  border-radius: var(--radius-full);
  border: 1px solid transparent;
  white-space: nowrap;
}

.layer--sm {
  padding: 0 6px;
  line-height: 16px;
}

.layer--standard {
  color: var(--color-info);
  background: var(--color-info-subtle);
  border-color: var(--color-info-border);
}

.layer--project {
  color: var(--color-accent);
  background: var(--color-accent-subtle);
  border-color: var(--color-accent-border);
}
</style>
