<script setup>
import { onMounted, ref } from 'vue'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const ready = ref(false)

/**
 * Resolve the session once before the shell renders, so a hard refresh onto a
 * deep link does not flash the login screen at an authenticated user.
 */
onMounted(async () => {
  await auth.bootstrap()
  ready.value = true
})
</script>

<template>
  <div v-if="!ready" class="boot" role="status" aria-live="polite">
    <div class="boot__mark" aria-hidden="true">CQ</div>
    <p class="boot__label">Starting ClaimIQ Enterprise…</p>
  </div>

  <RouterView v-else />
</template>

<style scoped>
.boot {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  height: 100%;
  background: var(--color-canvas);
}

.boot__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  font-size: var(--text-xs);
  font-weight: var(--weight-bold);
  color: var(--color-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-lg);
}

.boot__label {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
</style>
