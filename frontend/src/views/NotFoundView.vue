<script setup>
import { useRoute, useRouter } from 'vue-router'

import AppButton from '@/components/common/AppButton.vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push({ name: auth.isAuthenticated ? 'dashboard' : 'login' })
}
</script>

<template>
  <div class="notfound">
    <div class="notfound__card">
      <p class="notfound__code">404</p>
      <h1 class="notfound__title">Page not found</h1>
      <p class="notfound__message">
        There is nothing at <code>{{ route.fullPath }}</code
        >. The link may be out of date, or the record it pointed to may have been removed.
      </p>

      <div class="notfound__actions">
        <AppButton label="Go back" icon="pi pi-arrow-left" @click="goBack" />
        <AppButton
          variant="primary"
          :label="auth.isAuthenticated ? 'Dashboard' : 'Sign in'"
          :to="{ name: auth.isAuthenticated ? 'dashboard' : 'login' }"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.notfound {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: var(--space-6);
  background: var(--color-canvas);
}

.notfound__card {
  max-width: 440px;
  text-align: center;
}

.notfound__code {
  font-size: var(--text-3xl);
  font-weight: var(--weight-bold);
  font-variant-numeric: tabular-nums;
  letter-spacing: var(--tracking-tight);
  color: var(--color-text-disabled);
}

.notfound__title {
  margin-top: var(--space-2);
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.notfound__message {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}

.notfound__actions {
  display: flex;
  justify-content: center;
  gap: var(--space-2);
  margin-top: var(--space-6);
}
</style>
