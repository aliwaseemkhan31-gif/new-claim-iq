<script setup>
import { computed, ref, watch } from 'vue'

import AppButton from '@/components/common/AppButton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'

/**
 * Preliminary screening: whether a claim is in a fit state to work on.
 *
 * Read from the claim as recorded, before any evidence is linked and before
 * any model runs.
 *
 * Twenty-five checks is too many to read as a list, and the five areas are the
 * mental model a claims person already has — so the areas lead, each showing
 * its own state, and the checks sit behind them. Areas holding something that
 * blocks assessment open themselves; the rest wait to be asked.
 *
 * A possible time bar is lifted out of its area entirely. It is the one
 * outcome here that is adverse on the contract rather than on the state of the
 * file, and it should not have to be found inside a list.
 *
 * The caveat is not decoration. The prototype produced a verdict and people
 * acted on it.
 */
const props = defineProps({
  screening: { type: Object, required: true },
  /** Where a remedy can be acted on, by check code. See ClaimDetailView. */
  actions: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['act'])

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

const AREA_ICONS = {
  entitlement: 'pi pi-book',
  notice: 'pi pi-send',
  event: 'pi pi-calendar',
  records: 'pi pi-folder-open',
  relief: 'pi pi-calculator',
}

/**
 * Tile labels, shorter than the area's full name.
 *
 * "Supporting evidence and contemporary records" wraps to three lines in a
 * tile and makes the row of five ragged. The full name still heads the
 * expanded section, where there is room for it.
 */
const AREA_SHORT = {
  entitlement: 'Entitlement',
  notice: 'Notice',
  event: 'Event',
  records: 'Records',
  relief: 'Relief',
}

function isOutstanding(check) {
  return ['incomplete', 'indeterminate', 'barred'].includes(check.status)
}

const tone = computed(() => OUTCOME_TONE[props.screening.outcome] ?? 'neutral')

/** Barred checks, lifted out of their area. */
const barred = computed(() =>
  props.screening.areas.flatMap((area) => area.checks.filter((c) => c.status === 'barred')),
)

const areas = computed(() =>
  props.screening.areas.map((area) => {
    const applicable = area.checks.filter((c) => c.status !== 'not_applicable')
    const outstanding = area.checks.filter(isOutstanding)
    return {
      ...area,
      applicable,
      outstanding,
      // Blocking first: they are what stops the claim being assessable.
      ordered: [...area.checks].sort((a, b) => rank(a) - rank(b)),
      state: areaState(area, outstanding),
    }
  }),
)

function rank(check) {
  if (check.status === 'barred') return 0
  if (isOutstanding(check) && check.weight === 'blocking') return 1
  if (isOutstanding(check)) return 2
  if (check.status === 'not_applicable') return 4
  return 3
}

function areaState(area, outstanding) {
  if (area.barred) return { tone: 'danger', label: 'Possible bar' }
  if (area.blocking_outstanding) {
    return { tone: 'warning', label: `${area.blocking_outstanding} blocking` }
  }
  if (outstanding.length) return { tone: 'info', label: `${outstanding.length} to check` }
  return { tone: 'success', label: 'All recorded' }
}

/** Areas holding something that blocks assessment open themselves. */
function defaultOpen() {
  return props.screening.areas
    .filter((area) => area.barred || area.blocking_outstanding)
    .map((area) => area.code)
}

const open = ref(defaultOpen())
watch(() => props.screening, () => (open.value = defaultOpen()))

function toggle(code) {
  open.value = open.value.includes(code)
    ? open.value.filter((c) => c !== code)
    : [...open.value, code]
}

const showSatisfied = ref(false)

function visibleChecks(area) {
  return showSatisfied.value
    ? area.ordered
    : area.ordered.filter((c) => isOutstanding(c) || c.weight === 'informational')
}
</script>

<template>
  <div class="stack gap-4">
    <!-- The one outcome that is adverse on the contract, not on the file. -->
    <section v-if="barred.length" class="surface screen__bar" role="alert">
      <div class="row gap-2">
        <i class="pi pi-exclamation-triangle" aria-hidden="true" />
        <p class="screen__bar-title">{{ screening.outcome_label }}</p>
      </div>
      <p v-for="check in barred" :key="check.code" class="text-sm">{{ check.detail }}</p>
      <p class="text-xs">
        Computed from the recorded dates. It is not a determination, and the consequence
        depends on the wording of the provision itself.
      </p>
    </section>

    <section class="surface screen">
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
          :label="showSatisfied ? 'Outstanding only' : 'Show every check'"
          @click="showSatisfied = !showSatisfied"
        />
      </div>

      <p class="text-xs text-muted screen__caveat">{{ screening.caveat }}</p>

      <!-- Five areas, each with its own state. The checks sit behind them. -->
      <ul class="screen__areas">
        <li v-for="area in areas" :key="area.code">
          <button
            type="button"
            class="screen__area"
            :class="[`is-${area.state.tone}`, { 'is-open': open.includes(area.code) }]"
            :aria-expanded="open.includes(area.code)"
            @click="toggle(area.code)"
          >
            <span class="row gap-2">
              <i :class="AREA_ICONS[area.code]" aria-hidden="true" />
              <span class="screen__area-label">{{ AREA_SHORT[area.code] ?? area.label }}</span>
            </span>
            <StatusBadge :tone="area.state.tone" :label="area.state.label" size="sm" />
            <span class="sr-only">{{ area.label }}</span>
          </button>
        </li>
      </ul>

      <div v-for="area in areas" :key="`panel-${area.code}`">
        <section v-if="open.includes(area.code)" class="screen__checks">
          <p class="text-overline">{{ area.label }}</p>
          <p v-if="!visibleChecks(area).length" class="text-sm text-muted">
            Every applicable check in this area is satisfied.
          </p>
          <article
            v-for="check in visibleChecks(area)"
            :key="check.code"
            class="screen__check"
            :class="{ 'is-outstanding': isOutstanding(check) }"
          >
            <div class="row gap-2 wrap">
              <StatusBadge
                :tone="STATUS_TONE[check.status]"
                :label="STATUS_LABEL[check.status]"
                size="sm"
              />
              <span class="screen__question">{{ check.question }}</span>
              <span
                v-if="check.weight === 'blocking' && isOutstanding(check)"
                class="text-xs text-danger"
              >
                Blocks assessment
              </span>
            </div>
            <p class="text-sm screen__detail">{{ check.detail }}</p>
            <ul v-if="check.items.length" class="screen__items">
              <li v-for="item in check.items" :key="item" class="text-xs">{{ item }}</li>
            </ul>
            <p v-if="isOutstanding(check)" class="text-xs text-muted">
              <template v-if="check.remedy">{{ check.remedy }}</template>
              <AppButton
                v-if="actions[check.code]"
                size="sm"
                variant="ghost"
                :icon="actions[check.code].icon"
                :label="actions[check.code].label"
                @click="emit('act', actions[check.code])"
              />
            </p>
            <p v-else-if="check.weight === 'informational'" class="text-xs text-muted">
              {{ check.why }}
            </p>
          </article>
        </section>
      </div>
    </section>
  </div>
</template>

<style scoped>
.screen {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.screen__caveat {
  max-width: 72ch;
}

.screen__bar {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  border-left: 3px solid var(--color-danger);
  color: var(--color-danger);
  background: var(--color-danger-subtle);
}

.screen__bar-title {
  font-weight: var(--weight-medium);
}

.screen__areas {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: 0;
}

.screen__area {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  width: 100%;
  height: 100%;
  padding: var(--space-3);
  text-align: left;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
}

.screen__area:hover {
  border-color: var(--color-border-strong, var(--color-accent));
}

.screen__area.is-open {
  border-color: var(--color-accent);
  box-shadow: inset 0 -2px 0 var(--color-accent);
}

.screen__area.is-danger {
  border-left: 3px solid var(--color-danger);
}

.screen__area.is-warning {
  border-left: 3px solid var(--color-warning);
}

.screen__area.is-info {
  border-left: 3px solid var(--color-info);
}

.screen__area.is-success {
  border-left: 3px solid var(--color-success);
}

.screen__area-label {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  white-space: nowrap;
}

.screen__checks {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.screen__check {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: var(--space-3);
  border-left: 2px solid transparent;
}

.screen__check.is-outstanding {
  border-left-color: var(--color-border);
}

.screen__question {
  font-weight: var(--weight-medium);
}

.screen__detail {
  max-width: 80ch;
}

.screen__items {
  margin: 0;
  padding-left: var(--space-4);
  color: var(--color-text-muted);
}

@media (max-width: 640px) {
  .screen__areas {
    grid-template-columns: 1fr;
  }
}
</style>
