<script setup>
import { computed, ref, watch } from 'vue'

import AppButton from './AppButton.vue'
import { useModal } from '@/composables/useModal'

/**
 * Confirmation for destructive or irreversible actions.
 *
 * When `confirmPhrase` is set the user must type it exactly. Reserved for
 * genuinely unrecoverable operations (deleting a project and its corpus) —
 * used everywhere it becomes noise people click through.
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  title: { type: String, required: true },
  message: { type: String, default: null },
  confirmLabel: { type: String, default: 'Confirm' },
  cancelLabel: { type: String, default: 'Cancel' },
  variant: { type: String, default: 'danger', validator: (v) => ['danger', 'primary'].includes(v) },
  loading: { type: Boolean, default: false },
  confirmPhrase: { type: String, default: null },
})

const emit = defineEmits(['update:modelValue', 'confirm', 'cancel'])

const typed = ref('')
const panel = ref(null)
const inputEl = ref(null)
const confirmEl = ref(null)

const canConfirm = computed(() => {
  if (props.loading) return false
  if (!props.confirmPhrase) return true
  return typed.value.trim() === props.confirmPhrase
})

watch(
  () => props.modelValue,
  (open) => {
    if (!open) typed.value = ''
  }
)

const { onKeydown } = useModal(
  computed(() => props.modelValue),
  panel,
  {
    onRequestClose: () => onCancel(),
    // On the phrase field when one is required, otherwise on the confirm
    // button — the dialog asks one question and that is the answer.
    initialFocus: () =>
      props.confirmPhrase ? inputEl.value : (confirmEl.value?.$el ?? null),
  }
)

function close() {
  emit('update:modelValue', false)
}

function onCancel() {
  emit('cancel')
  close()
}

function onConfirm() {
  if (!canConfirm.value) return
  emit('confirm')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="confirm__overlay" @click.self="onCancel" @keydown="onKeydown">
      <div
        ref="panel"
        class="confirm"
        role="alertdialog"
        aria-modal="true"
        tabindex="-1"
        :aria-label="title"
        data-testid="confirm-dialog"
      >
        <header class="confirm__header">
          <div class="confirm__icon" :class="`confirm__icon--${variant}`" aria-hidden="true">
            <i :class="variant === 'danger' ? 'pi pi-exclamation-triangle' : 'pi pi-question'" />
          </div>
          <h2 class="confirm__title">{{ title }}</h2>
        </header>

        <div class="confirm__body">
          <p v-if="message" class="confirm__message">{{ message }}</p>
          <slot />

          <div v-if="confirmPhrase" class="confirm__phrase">
            <label :for="'confirm-phrase'">
              Type <strong>{{ confirmPhrase }}</strong> to continue
            </label>
            <input
              id="confirm-phrase"
              ref="inputEl"
              v-model="typed"
              class="field-input"
              type="text"
              autocomplete="off"
              spellcheck="false"
              @keydown.enter="onConfirm"
            />
          </div>
        </div>

        <footer class="confirm__footer">
          <AppButton variant="ghost" :label="cancelLabel" :disabled="loading" @click="onCancel" />
          <AppButton
            ref="confirmEl"
            :variant="variant"
            :label="confirmLabel"
            :loading="loading"
            :disabled="!canConfirm"
            @click="onConfirm"
          />
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm__overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-6);
  background: var(--color-overlay);
}

.confirm {
  width: 100%;
  max-width: 440px;
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-xl);
}

.confirm__header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-5) var(--space-3);
}

.confirm__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  font-size: 15px;
  border-radius: var(--radius-lg);
  flex-shrink: 0;
}

.confirm__icon--danger {
  color: var(--color-danger);
  background: var(--color-danger-subtle);
}

.confirm__icon--primary {
  color: var(--color-accent);
  background: var(--color-accent-subtle);
}

.confirm__title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.confirm__body {
  padding: 0 var(--space-5) var(--space-5);
}

.confirm__message {
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.confirm__phrase {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.confirm__footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  border-top: 1px solid var(--color-border-subtle);
  background: var(--color-surface-sunken);
  border-radius: 0 0 var(--radius-xl) var(--radius-xl);
}
</style>
