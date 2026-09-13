<script setup>
import { computed } from 'vue'

import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

const label = computed(() => (ui.isDark ? 'Switch to light theme' : 'Switch to dark theme'))
</script>

<template>
  <button
    type="button"
    class="theme-toggle"
    :aria-label="label"
    :title="label"
    @click="ui.toggleTheme()"
  >
    <i :class="ui.isDark ? 'pi pi-sun' : 'pi pi-moon'" aria-hidden="true" />
  </button>
</template>

<style scoped>
.theme-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  font-size: 14px;
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
}

.theme-toggle:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}
</style>
