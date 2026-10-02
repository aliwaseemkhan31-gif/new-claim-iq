<script setup>
import { computed } from 'vue'

import { RouterLink } from 'vue-router'

const props = defineProps({
  variant: {
    type: String,
    default: 'secondary',
    validator: (v) => ['primary', 'secondary', 'ghost', 'danger', 'link'].includes(v),
  },
  size: {
    type: String,
    default: 'md',
    validator: (v) => ['sm', 'md', 'lg'].includes(v),
  },
  icon: { type: String, default: null },
  iconRight: { type: String, default: null },
  label: { type: String, default: null },
  loading: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  block: { type: Boolean, default: false },
  type: { type: String, default: 'button' },
  to: { type: [String, Object], default: null },
  href: { type: String, default: null },
})

const emit = defineEmits(['click'])

const isDisabled = computed(() => props.disabled || props.loading)

const tag = computed(() => {
  // A disabled link is rendered as a button. `disabled` means nothing to an
  // anchor, so the element stayed in the tab order and still navigated —
  // `aria-disabled` told assistive technology one thing while the element did
  // another.
  if (isDisabled.value) return 'button'
  if (props.to) return RouterLink
  if (props.href) return 'a'
  return 'button'
})

const bindings = computed(() => {
  if (isDisabled.value) return { type: props.type, disabled: true }
  if (props.to) return { to: props.to }
  if (props.href) return { href: props.href, rel: 'noopener' }
  return { type: props.type, disabled: false }
})

const iconOnly = computed(() => Boolean(props.icon) && !props.label)

function onClick(event) {
  if (isDisabled.value) {
    event.preventDefault()
    event.stopPropagation()
    return
  }
  emit('click', event)
}
</script>

<template>
  <component
    :is="tag"
    v-bind="bindings"
    class="btn"
    :class="[
      `btn--${variant}`,
      `btn--${size}`,
      { 'btn--block': block, 'btn--icon-only': iconOnly, 'is-disabled': isDisabled },
    ]"
    :aria-busy="loading || undefined"
    :aria-disabled="isDisabled || undefined"
    @click="onClick"
  >
    <i v-if="loading" class="pi pi-spinner btn__spinner" aria-hidden="true" />
    <i v-else-if="icon" :class="icon" aria-hidden="true" />
    <span v-if="label || $slots.default" class="btn__label"><slot>{{ label }}</slot></span>
    <i v-if="iconRight && !loading" :class="iconRight" aria-hidden="true" />
  </component>
</template>

<style scoped>
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: 1;
  white-space: nowrap;
  text-decoration: none;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  transition:
    background-color var(--duration-fast) var(--ease-standard),
    border-color var(--duration-fast) var(--ease-standard),
    color var(--duration-fast) var(--ease-standard);
  user-select: none;
}

.btn:hover {
  text-decoration: none;
}

.btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* Sizes */
.btn--sm {
  height: 28px;
  padding: 0 var(--space-2);
  font-size: var(--text-xs);
}

.btn--md {
  height: 32px;
  padding: 0 var(--space-3);
}

.btn--lg {
  height: 40px;
  padding: 0 var(--space-4);
  font-size: var(--text-base);
}

.btn--icon-only.btn--sm {
  width: 28px;
  padding: 0;
}
.btn--icon-only.btn--md {
  width: 32px;
  padding: 0;
}
.btn--icon-only.btn--lg {
  width: 40px;
  padding: 0;
}

.btn--block {
  display: flex;
  width: 100%;
}

/* Variants */
.btn--primary {
  color: var(--color-on-accent);
  background: var(--color-accent);
  border-color: var(--color-accent);
}

.btn--primary:hover:not(.is-disabled) {
  background: var(--color-accent-hover);
  border-color: var(--color-accent-hover);
}

.btn--primary:active:not(.is-disabled) {
  background: var(--color-accent-active);
}

.btn--secondary {
  color: var(--color-text);
  background: var(--color-surface);
  border-color: var(--color-border-strong);
  box-shadow: var(--shadow-xs);
}

.btn--secondary:hover:not(.is-disabled) {
  background: var(--color-surface-hover);
}

.btn--ghost {
  color: var(--color-text-secondary);
  background: transparent;
}

.btn--ghost:hover:not(.is-disabled) {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.btn--danger {
  color: var(--color-on-accent);
  background: var(--color-danger);
  border-color: var(--color-danger);
}

.btn--danger:hover:not(.is-disabled) {
  background: var(--color-danger-hover);
  border-color: var(--color-danger-hover);
}

.btn--link {
  height: auto;
  padding: 0;
  color: var(--color-text-link);
  background: none;
}

.btn--link:hover:not(.is-disabled) {
  text-decoration: underline;
}

.btn.is-disabled {
  opacity: 0.5;
  pointer-events: none;
}

.btn__spinner {
  animation: btn-spin 900ms linear infinite;
}

@keyframes btn-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
