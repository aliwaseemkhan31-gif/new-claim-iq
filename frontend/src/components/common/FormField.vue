<script setup>
/**
 * Label, control, hint and error for one form field.
 *
 * The error comes from the API's field errors; it is shown beside the field it
 * belongs to rather than only in a toast.
 */
defineProps({
  label: { type: String, required: true },
  forId: { type: String, default: null },
  hint: { type: String, default: null },
  error: { type: [String, Array], default: null },
  required: { type: Boolean, default: false },
})
</script>

<template>
  <div class="field">
    <label class="field__label" :for="forId">
      {{ label }}<span v-if="required" class="field__required" aria-hidden="true">*</span>
    </label>
    <slot />
    <p v-if="error" class="field__error" role="alert">
      {{ Array.isArray(error) ? error.join(' ') : error }}
    </p>
    <p v-else-if="hint" class="field__hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.field__required {
  margin-left: 2px;
  color: var(--color-danger);
}

.field__hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.field__error {
  font-size: var(--text-xs);
  color: var(--color-danger);
}
</style>
