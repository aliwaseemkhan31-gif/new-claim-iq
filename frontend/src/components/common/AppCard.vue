<script setup>
defineProps({
  title: { type: String, default: null },
  subtitle: { type: String, default: null },
  /** Remove body padding — for cards whose body is a full-bleed table. */
  flush: { type: Boolean, default: false },
  /** Fill the parent's height and let the body scroll. */
  fill: { type: Boolean, default: false },
})
</script>

<template>
  <section class="card" :class="{ 'card--fill': fill }">
    <header v-if="title || $slots.header || $slots.actions" class="card__header">
      <slot name="header">
        <div class="card__titles">
          <h3 class="card__title">{{ title }}</h3>
          <p v-if="subtitle" class="card__subtitle">{{ subtitle }}</p>
        </div>
      </slot>
      <div v-if="$slots.actions" class="card__actions">
        <slot name="actions" />
      </div>
    </header>

    <div class="card__body" :class="{ 'card__body--flush': flush }">
      <slot />
    </div>

    <footer v-if="$slots.footer" class="card__footer">
      <slot name="footer" />
    </footer>
  </section>
</template>

<style scoped>
.card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xs);
}

.card--fill {
  height: 100%;
  min-height: 0;
}

.card--fill .card__body {
  overflow-y: auto;
  min-height: 0;
}

.card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border-subtle);
}

.card__titles {
  min-width: 0;
}

.card__title {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.card__subtitle {
  margin-top: 2px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.card__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.card__body {
  padding: var(--space-4);
}

.card__body--flush {
  padding: 0;
}

.card__footer {
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-subtle);
  background: var(--color-surface-sunken);
  border-radius: 0 0 var(--radius-lg) var(--radius-lg);
}
</style>
