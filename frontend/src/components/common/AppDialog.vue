<script setup>
import { nextTick, ref, watch } from 'vue'

/**
 * A modal dialog for forms and detail panels.
 *
 * Escape and the overlay close it unless `busy` — closing mid-submit would
 * leave the user unsure whether the action happened.
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, required: true },
  description: { type: String, default: null },
  size: { type: String, default: 'md', validator: (v) => ['sm', 'md', 'lg', 'xl'].includes(v) },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue', 'close'])

const panel = ref(null)

function close() {
  if (props.busy) return
  emit('update:modelValue', false)
  emit('close')
}

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    await nextTick()
    const target = panel.value?.querySelector('input, select, textarea, button:not([data-dialog-close])')
    ;(target || panel.value)?.focus?.()
  }
)
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="dialog__overlay" @click.self="close">
      <div
        ref="panel"
        class="dialog"
        :class="`dialog--${size}`"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
        tabindex="-1"
        @keydown.esc="close"
      >
        <header class="dialog__header">
          <div class="grow">
            <h2 class="dialog__title">{{ title }}</h2>
            <p v-if="description" class="dialog__description">{{ description }}</p>
          </div>
          <button
            type="button"
            class="dialog__close"
            aria-label="Close"
            data-dialog-close
            :disabled="busy"
            @click="close"
          >
            <i class="pi pi-times" aria-hidden="true" />
          </button>
        </header>
        <div class="dialog__body scroll-y">
          <slot />
        </div>
        <footer v-if="$slots.footer" class="dialog__footer">
          <slot name="footer" />
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.dialog__overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-6);
  background: var(--color-overlay);
}

.dialog {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-height: calc(100vh - 2 * var(--space-6));
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-xl);
}

.dialog:focus {
  outline: none;
}

.dialog--sm {
  max-width: 420px;
}
.dialog--md {
  max-width: 560px;
}
.dialog--lg {
  max-width: 760px;
}
.dialog--xl {
  max-width: 1040px;
}

.dialog__header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-5) var(--space-3);
}

.dialog__title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.dialog__description {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.dialog__close {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
}

.dialog__close:hover:not(:disabled) {
  background: var(--color-surface-hover);
}

.dialog__body {
  padding: 0 var(--space-5) var(--space-5);
  min-height: 0;
}

.dialog__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  border-top: 1px solid var(--color-border-subtle);
  background: var(--color-surface-sunken);
  border-radius: 0 0 var(--radius-xl) var(--radius-xl);
}
</style>
