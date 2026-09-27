<script setup>
import { computed, ref } from 'vue'

import AppButton from '@/components/common/AppButton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'

/**
 * Preliminary screening: whether a claim is in a fit state to work on.
 *
 * Read from the claim as recorded, before any evidence is linked and before
 * any model runs. Deliberately shows satisfied checks as well as outstanding
 * ones — a reader needs to see what was looked at, not only what failed — but
 * collapses them by default so the outstanding items are what you see first.
 *
 * It is not a view on the merits, and the caveat saying so is not optional
 * decoration: the prototype produced a verdict and people acted on it.
 */
const props = defineProps({
  screening: { type: Object, required: true },
})

const STATUS_TONE = {
  satisfied: 'success',
  incomplete: 'warning',
  indeterminate: 'neutral',
  barred: 'danger',
  not_applicable: 'neutral',
}

const STATUS_LABEL = {
  satisfied: 'Recorded',
  incomplete: 'Outstanding',
  indeterminate: 'Cannot be determined',
  barred: 'Possible bar',
  not_applicable: 'Not applicable',
}

const OUTCOME_TONE = {
  barred: 'danger',
  not_ready: 'warning',
  ready_with_queries: 'info',
  ready: 'success',
}

const showSatisfied = ref(false)

const tone = computed(() => OUTCOME_TONE[props.screening.outcome] ?? 'neutral')

/** A check the reader has to do something about. */
function isOutstanding(check) {
  return check.status === 'incomplete' || check.status === 'indeterminate' || check.status === 'barred'
}

const areas = computed(() =>
  props.screening.areas.map((area) => {
    const outstanding = area.checks.filter(isOutstanding)
    return {
      ...area,
      outstanding,
      visible: showSatisfied.value ? area.checks : outstanding,
    }
  }),
)

const hasOutstanding = computed(() => areas.value.some((area) => area.outstanding.length))
</script>

<template>
  <section class="surface screening" :class="`screening--${tone}`">
    <div class="row between gap-3 wrap">
      <div class="stack gap-1">
        <p class="section-title">Preliminary screening</p>
        <div class="row gap-2 wrap">
          <StatusBadge :tone="tone" :label="screening.outcome_label" />
          <span class="text-sm">{{ screening.summary }}</span>
        </div>
      </div>
      <AppButton
        size="sm"
        variant="ghost"
        :icon="showSatisfied ? 'pi pi-eye-slash' : 'pi pi-eye'"
        :label="showSatisfied ? 'Outstanding only' : 'Show all checks'"
        @click="showSatisfied = !showSatisfied"
      />
    </div>

    <p class="text-xs text-muted screening__caveat">{{ screening.caveat }}</p>

    <p v-if="!hasOutstanding && !showSatisfied" class="text-sm">
      Every applicable check is satisfied on the record as it stands.
    </p>

    <div v-for="area in areas" :key="area.code" class="screening__area">
      <template v-if="area.visible.length">
        <p class="text-overline">{{ area.label }}</p>
        <ul class="screening__checks">
          <li v-for="check in area.visible" :key="check.code" class="screening__check">
            <div class="row gap-2 wrap">
              <StatusBadge
                :tone="STATUS_TONE[check.status]"
                :label="STATUS_LABEL[check.status]"
                size="sm"
              />
              <span class="screening__question">{{ check.question }}</span>
              <span v-if="check.weight === 'blocking' && isOutstanding(check)" class="text-xs text-danger">
                Blocks assessment
              </span>
            </div>
            <p class="text-sm screening__detail">{{ check.detail }}</p>
            <ul v-if="check.items.length" class="screening__items">
              <li v-for="item in check.items" :key="item" class="text-xs">{{ item }}</li>
            </ul>
            <p v-if="check.remedy && isOutstanding(check)" class="text-xs text-muted">
              {{ check.remedy }}
            </p>
          </li>
        </ul>
      </template>
    </div>
  </section>
</template>

<style scoped>
.screening {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  border-left: 3px solid var(--color-border);
}

.screening--danger {
  border-left-color: var(--color-danger);
}

.screening--warning {
  border-left-color: var(--color-warning);
}

.screening--info {
  border-left-color: var(--color-info);
}

.screening--success {
  border-left-color: var(--color-success);
}

.screening__caveat {
  max-width: 70ch;
}

.screening__area {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.screening__checks {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  list-style: none;
  margin: 0;
  padding: 0;
}

.screening__check {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.screening__question {
  font-weight: var(--weight-medium);
}

.screening__detail {
  max-width: 80ch;
}

.screening__items {
  margin: 0;
  padding-left: var(--space-4);
  color: var(--color-text-muted);
}
</style>
