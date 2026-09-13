<script setup>
/**
 * The honest "there is nothing here" state.
 *
 * Used for three genuinely different situations, and the copy should say
 * which one it is:
 *   - no data has been created yet (offer the action that creates it),
 *   - a filter matched nothing (offer to clear it),
 *   - the feature is not built yet (say so plainly).
 *
 * It never stands in for data the product could not load — that is ErrorState.
 */
defineProps({
  icon: { type: String, default: 'pi pi-inbox' },
  title: { type: String, required: true },
  description: { type: String, default: null },
  /** Short, quiet note under the action — e.g. the permission required. */
  hint: { type: String, default: null },
  actionLabel: { type: String, default: null },
  actionIcon: { type: String, default: null },
  secondaryActionLabel: { type: String, default: null },
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['action', 'secondary-action'])
</script>

<template>
  <div class="empty" :class="{ 'empty--compact': compact }" data-testid="empty-state">
    <div class="empty__icon" aria-hidden="true">
      <i :class="icon" />
    </div>

    <h3 class="empty__title">{{ title }}</h3>

    <p v-if="description" class="empty__description">{{ description }}</p>

    <slot />

    <div v-if="actionLabel || secondaryActionLabel || $slots.actions" class="empty__actions">
      <slot name="actions">
        <button
          v-if="actionLabel"
          type="button"
          class="empty__button empty__button--primary"
          @click="emit('action')"
        >
          <i v-if="actionIcon" :class="actionIcon" aria-hidden="true" />
          <span>{{ actionLabel }}</span>
        </button>
        <button
          v-if="secondaryActionLabel"
          type="button"
          class="empty__button"
          @click="emit('secondary-action')"
        >
          {{ secondaryActionLabel }}
        </button>
      </slot>
    </div>

    <p v-if="hint" class="empty__hint">{{ hint }}</p>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-16) var(--space-6);
  text-align: center;
}

.empty--compact {
  padding: var(--space-8) var(--space-4);
}

.empty__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  margin-bottom: var(--space-2);
  font-size: 19px;
  color: var(--color-text-muted);
  background: var(--color-surface-sunken);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-xl);
}

.empty__title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  color: var(--color-text);
}

.empty__description {
  max-width: 46ch;
  font-size: var(--text-sm);
  line-height: var(--leading-normal);
  color: var(--color-text-secondary);
}

.empty__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.empty__button {
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

.empty__button:hover {
  background: var(--color-surface-hover);
}

.empty__button--primary {
  color: var(--color-on-accent);
  background: var(--color-accent);
  border-color: var(--color-accent);
}

.empty__button--primary:hover {
  background: var(--color-accent-hover);
}

.empty__hint {
  margin-top: var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
