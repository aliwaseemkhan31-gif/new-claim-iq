<script setup>
import { computed } from 'vue'

import AppButton from './AppButton.vue'
import { useReturnTo } from '@/composables/useReturnTo'

/**
 * One way out of a detail screen, pointing at wherever the user came from.
 *
 * Renders a real link when it is falling back to the named list, so it can be
 * opened in a new tab; a button when it is stepping back through history,
 * because there is no URL that means "back".
 */
const props = defineProps({
  /** Where to go when there is no previous entry — a reload, or a deep link. */
  fallbackTo: { type: [String, Object], required: true },
  fallbackLabel: { type: String, required: true },
})

const { target, usingHistory, go } = useReturnTo(() => ({
  to: props.fallbackTo,
  label: props.fallbackLabel,
}))

const label = computed(() => target.value?.label ?? props.fallbackLabel)

function onClick() {
  if (usingHistory.value) go()
}
</script>

<template>
  <AppButton
    size="sm"
    variant="ghost"
    icon="pi pi-arrow-left"
    :label="label"
    :aria-label="`Back to ${label}`"
    :to="usingHistory ? null : target?.to"
    @click="onClick"
  />
</template>
