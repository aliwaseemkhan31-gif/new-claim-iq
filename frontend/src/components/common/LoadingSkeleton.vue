<script setup>
/**
 * Skeletons, not spinners. A skeleton communicates the shape of what is
 * coming and stops the layout jumping when it arrives.
 */
defineProps({
  variant: {
    type: String,
    default: 'text',
    validator: (v) => ['text', 'table', 'card', 'metrics', 'block'].includes(v),
  },
  /** Text lines, table rows, or cards depending on `variant`. */
  rows: { type: Number, default: 3 },
  columns: { type: Number, default: 4 },
  height: { type: String, default: '120px' },
})
</script>

<template>
  <div class="skeleton-root" role="status" aria-live="polite" aria-busy="true" data-testid="loading-skeleton">
    <span class="sr-only">Loading…</span>

    <template v-if="variant === 'text'">
      <div
        v-for="line in rows"
        :key="line"
        class="sk"
        :style="{ width: line === rows && rows > 1 ? '62%' : '100%', height: '12px' }"
      />
    </template>

    <div v-else-if="variant === 'table'" class="sk-table">
      <div class="sk-table__head">
        <div v-for="col in columns" :key="`h-${col}`" class="sk" style="height: 10px" />
      </div>
      <div v-for="row in rows" :key="`r-${row}`" class="sk-table__row">
        <div v-for="col in columns" :key="`c-${row}-${col}`" class="sk" style="height: 12px" />
      </div>
    </div>

    <div v-else-if="variant === 'metrics'" class="sk-metrics">
      <div v-for="card in columns" :key="card" class="sk-metric">
        <div class="sk" style="width: 45%; height: 10px" />
        <div class="sk" style="width: 62%; height: 24px" />
      </div>
    </div>

    <div v-else-if="variant === 'card'" class="sk-cards">
      <div v-for="card in rows" :key="card" class="sk-card">
        <div class="sk" style="width: 58%; height: 13px" />
        <div class="sk" style="width: 100%; height: 10px" />
        <div class="sk" style="width: 78%; height: 10px" />
      </div>
    </div>

    <div v-else class="sk" :style="{ height }" />
  </div>
</template>

<style scoped>
.skeleton-root {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}

.sk {
  position: relative;
  overflow: hidden;
  background: var(--color-skeleton);
  border-radius: var(--radius-sm);
}

.sk::after {
  content: '';
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(
    90deg,
    transparent,
    var(--color-skeleton-shine),
    transparent
  );
  animation: sk-shimmer 1.4s ease-in-out infinite;
}

@keyframes sk-shimmer {
  100% {
    transform: translateX(100%);
  }
}

.sk-table {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.sk-table__head,
.sk-table__row {
  display: grid;
  grid-template-columns: repeat(v-bind(columns), 1fr);
  gap: var(--space-4);
  padding: var(--space-3) var(--space-4);
  align-items: center;
}

.sk-table__head {
  background: var(--color-surface-sunken);
  border-bottom: 1px solid var(--color-border);
}

.sk-table__row + .sk-table__row {
  border-top: 1px solid var(--color-border-subtle);
}

.sk-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-4);
}

.sk-metric,
.sk-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.sk-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-4);
}
</style>
