<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: null },
  /** Small label above the title, e.g. the parent entity. */
  eyebrow: { type: String, default: null },
  /** Optional count shown beside the title. Pass null while loading. */
  count: { type: [Number, String], default: null },
  sticky: { type: Boolean, default: false },
})
</script>

<template>
  <header class="page-header" :class="{ 'page-header--sticky': sticky }">
    <div class="page-header__main">
      <p v-if="eyebrow" class="text-overline">{{ eyebrow }}</p>
      <div class="page-header__title-row">
        <h1 class="page-header__title">{{ title }}</h1>
        <span v-if="count !== null" class="page-header__count">{{ count }}</span>
        <slot name="title-suffix" />
      </div>
      <p v-if="description" class="page-header__description">{{ description }}</p>
    </div>

    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-6);
  flex-wrap: wrap;
}

.page-header--sticky {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  padding-bottom: var(--space-3);
  background: var(--color-canvas);
}

.page-header__main {
  min-width: 0;
}

.page-header__title-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.page-header__title {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-tight);
}

.page-header__count {
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  font-variant-numeric: tabular-nums;
  color: var(--color-text-secondary);
  background: var(--color-surface-sunken);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-full);
}

.page-header__description {
  margin-top: var(--space-1);
  max-width: 76ch;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}
</style>
