<script setup>
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

const ICONS = {
  success: 'pi pi-check-circle',
  info: 'pi pi-info-circle',
  warn: 'pi pi-exclamation-circle',
  error: 'pi pi-times-circle',
}
</script>

<template>
  <div class="toasts" role="region" aria-live="polite" aria-label="Notifications">
    <div
      v-for="toast in ui.toasts"
      :key="toast.id"
      class="toast"
      :class="`toast--${toast.severity}`"
    >
      <i :class="ICONS[toast.severity] || ICONS.info" class="toast__icon" aria-hidden="true" />
      <div class="toast__content">
        <p v-if="toast.summary" class="toast__summary">{{ toast.summary }}</p>
        <p v-if="toast.detail" class="toast__detail">{{ toast.detail }}</p>
        <p v-if="toast.requestId" class="toast__meta text-mono">Request {{ toast.requestId }}</p>
      </div>
      <button
        type="button"
        class="toast__close"
        aria-label="Dismiss notification"
        @click="ui.dismiss(toast.id)"
      >
        <i class="pi pi-times" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.toasts {
  position: fixed;
  right: var(--space-5);
  bottom: var(--space-5);
  z-index: var(--z-toast);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 360px;
  max-width: calc(100vw - var(--space-8));
  pointer-events: none;
}

.toast {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-left: 3px solid var(--color-neutral-badge);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  pointer-events: auto;
}

.toast--success {
  border-left-color: var(--color-success);
}
.toast--info {
  border-left-color: var(--color-accent);
}
.toast--warn {
  border-left-color: var(--color-warning);
}
.toast--error {
  border-left-color: var(--color-danger);
}

.toast__icon {
  margin-top: 1px;
  font-size: 14px;
  flex-shrink: 0;
}

.toast--success .toast__icon {
  color: var(--color-success);
}
.toast--info .toast__icon {
  color: var(--color-accent);
}
.toast--warn .toast__icon {
  color: var(--color-warning);
}
.toast--error .toast__icon {
  color: var(--color-danger);
}

.toast__content {
  flex: 1 1 auto;
  min-width: 0;
}

.toast__summary {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.toast__detail {
  margin-top: 2px;
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.toast__meta {
  margin-top: var(--space-2);
  color: var(--color-text-muted);
}

.toast__close {
  font-size: 11px;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.toast__close:hover {
  color: var(--color-text);
}
</style>
