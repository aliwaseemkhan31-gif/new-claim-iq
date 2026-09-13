<script setup>
import { computed } from 'vue'

import { ApiError } from '@/api/client'

/**
 * The failure state.
 *
 * It shows what went wrong, offers a retry, and surfaces the request id —
 * in an air-gapped install the request id is how a user gets support to the
 * right log line, so it is never hidden behind a console.
 */
const props = defineProps({
  error: { type: [Object, Error, String], default: null },
  title: { type: String, default: null },
  retryLabel: { type: String, default: 'Try again' },
  showRetry: { type: Boolean, default: true },
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['retry'])

const apiError = computed(() => (props.error instanceof ApiError ? props.error : null))

const isNetwork = computed(
  () => apiError.value?.code === 'network_error' || apiError.value?.code === 'request_timeout'
)

const isPermission = computed(() => apiError.value?.isPermissionError === true)

const heading = computed(() => {
  if (props.title) return props.title
  if (isPermission.value) return 'You do not have access to this'
  if (isNetwork.value) return 'Cannot reach the server'
  return 'Something went wrong'
})

const message = computed(() => {
  if (typeof props.error === 'string') return props.error
  return props.error?.message || 'The request could not be completed.'
})

const icon = computed(() => {
  if (isPermission.value) return 'pi pi-lock'
  if (isNetwork.value) return 'pi pi-wifi'
  return 'pi pi-exclamation-triangle'
})

const code = computed(() => apiError.value?.code ?? null)
const requestId = computed(() => apiError.value?.requestId ?? null)
</script>

<template>
  <div class="error-state" :class="{ 'error-state--compact': compact }" role="alert" data-testid="error-state">
    <div class="error-state__icon" aria-hidden="true">
      <i :class="icon" />
    </div>

    <h3 class="error-state__title">{{ heading }}</h3>
    <p class="error-state__message">{{ message }}</p>

    <p v-if="isNetwork" class="error-state__hint">
      The ClaimIQ API should be running on port 8100. If this is a fresh install, the backend may
      not be started yet.
    </p>

    <div v-if="showRetry || $slots.actions" class="error-state__actions">
      <slot name="actions">
        <button v-if="showRetry" type="button" class="error-state__button" @click="emit('retry')">
          <i class="pi pi-refresh" aria-hidden="true" />
          <span>{{ retryLabel }}</span>
        </button>
      </slot>
    </div>

    <dl v-if="code || requestId" class="error-state__meta">
      <template v-if="code">
        <dt>Code</dt>
        <dd class="text-mono">{{ code }}</dd>
      </template>
      <template v-if="requestId">
        <dt>Request</dt>
        <dd class="text-mono">{{ requestId }}</dd>
      </template>
    </dl>
  </div>
</template>

<style scoped>
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-12) var(--space-6);
  text-align: center;
}

.error-state--compact {
  padding: var(--space-6) var(--space-4);
}

.error-state__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: var(--space-2);
  font-size: 19px;
  color: var(--color-danger);
  background: var(--color-danger-subtle);
  border: 1px solid var(--color-danger-border);
  border-radius: var(--radius-xl);
}

.error-state__title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.error-state__message {
  max-width: 52ch;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.error-state__hint {
  max-width: 52ch;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.error-state__actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.error-state__button {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 32px;
  padding: 0 var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
}

.error-state__button:hover {
  background: var(--color-surface-hover);
}

.error-state__meta {
  display: grid;
  grid-template-columns: auto auto;
  gap: var(--space-1) var(--space-3);
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-subtle);
  font-size: var(--text-2xs);
}

.error-state__meta dt {
  color: var(--color-text-muted);
  text-align: right;
}

.error-state__meta dd {
  margin: 0;
  color: var(--color-text-secondary);
  text-align: left;
}
</style>
