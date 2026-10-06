<script setup>
import { computed, nextTick, ref, watch } from 'vue'

import AppButton from '@/components/common/AppButton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'

/**
 * Preliminary screening: whether a claim is in a fit state to work on.
 *
 * Read from the claim as recorded, before any evidence is linked and before
 * any model runs.
 *
 * Twenty-five checks is too many to read as a list. They are grouped into five
 * areas, and the areas sit on the chain a claim actually travels along:
 *
 *     Event  ->  Notice  ->  Claim
 *                            |- Contractual basis
 *                            |- Records
 *                            '- Relief
 *
 * That shape is the contract's, not a layout preference. FIDIC 2017
 * Sub-Clause 20.2.4 defines the fully detailed Claim as a submission which
 * includes (b) a statement of the contractual and/or other legal basis,
 * (c) the contemporary records relied on and (d) the particulars of the amount
 * and/or EOT claimed — so entitlement, records and relief are parts of the
 * Claim rather than steps beside it. Showing entitlement first, as this panel
 * used to, reads the chain backwards: it puts the conclusion before the event
 * that might support it.
 *
 * Stages holding something that blocks assessment open themselves; the rest
 * wait to be asked.
 *
 * A possible time bar is lifted out of its stage entirely. It is the one
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
  lapsed: 'danger',
  not_applicable: 'neutral',
}

const STATUS_LABEL = {
  satisfied: 'Recorded',
  incomplete: 'Outstanding',
  indeterminate: 'Cannot be determined',
  barred: 'Possible bar',
  lapsed: 'Notice may have lapsed',
  not_applicable: 'Not applicable',
}

const OUTCOME_TONE = {
  barred: 'danger',
  lapsed: 'danger',
  not_ready: 'warning',
  ready_with_queries: 'info',
  ready: 'success',
}

/** Adverse on the contract rather than on the state of the file. */
const ADVERSE = ['barred', 'lapsed']

const STAGE_ICONS = {
  event: 'pi pi-bolt',
  notice: 'pi pi-send',
  claim: 'pi pi-file-edit',
}

const AREA_ICONS = {
  submission: 'pi pi-file-export',
  entitlement: 'pi pi-book',
  notice: 'pi pi-send',
  event: 'pi pi-calendar',
  records: 'pi pi-folder-open',
  relief: 'pi pi-calculator',
}

/**
 * Sub-area labels inside the Claim stage, shorter than the area's full name.
 *
 * "Supporting evidence and contemporary records" wraps to three lines and
 * makes the row ragged. The full name still heads the expanded section, where
 * there is room for it.
 */
const AREA_SHORT = {
  submission: 'Submission',
  entitlement: 'Contractual basis',
  notice: 'Notice',
  event: 'Event',
  records: 'Records',
  relief: 'Relief',
}

function isOutstanding(check) {
  return ['incomplete', 'indeterminate', ...ADVERSE].includes(check.status)
}

const tone = computed(() => OUTCOME_TONE[props.screening.outcome] ?? 'neutral')

/**
 * Stages, falling back to the flat area list.
 *
 * A client holding a cached bundle from before the hierarchy landed, or a
 * server not yet redeployed, sends `areas` without `stages`. Rather than
 * render nothing, derive the chain from the areas present.
 */
const STAGE_FALLBACK = [
  { code: 'event', label: 'Event', description: 'What happened, and when.', areas: ['event'] },
  {
    code: 'notice',
    label: 'Notice',
    description: 'Whether the event was notified, in time and to the right party.',
    areas: ['notice'],
  },
  {
    code: 'claim',
    label: 'Claim',
    description: 'What is being asked for, on what basis, and on what evidence.',
    areas: ['submission', 'entitlement', 'records', 'relief'],
  },
]

const rawStages = computed(() => {
  if (props.screening.stages?.length) return props.screening.stages
  const byCode = new Map((props.screening.areas ?? []).map((a) => [a.code, a]))
  return STAGE_FALLBACK.map((stage) => {
    const areas = stage.areas.map((code) => byCode.get(code)).filter(Boolean)
    return {
      ...stage,
      areas,
      ...sumCounts(areas),
    }
  }).filter((stage) => stage.areas.length)
})

function sumCounts(items) {
  const keys = ['blocking_outstanding', 'outstanding', 'barred', 'lapsed']
  return Object.fromEntries(
    keys.map((key) => [key, items.reduce((n, item) => n + (item[key] ?? 0), 0)]),
  )
}

/** Every check, whichever shape arrived. */
const allChecks = computed(() => rawStages.value.flatMap((s) => s.areas.flatMap((a) => a.checks)))

/**
 * Checks adverse on the contract, lifted out of their stage.
 *
 * Two mechanisms, not one. A time bar is notice late under a condition
 * precedent. A lapse is FIDIC 2017 Sub-Clause 20.2.4: the statement of
 * contractual basis was not submitted in time, so the Notice of Claim is
 * deemed to have lapsed — reversible, if the Engineer did not give Notice of
 * it within 14 days. Both belong at the top; neither is a determination.
 */
const adverse = computed(() => allChecks.value.filter((c) => ADVERSE.includes(c.status)))

/** Which stage and area each check code sits in, for dependency deep-links. */
const locationByCode = computed(() => {
  const map = new Map()
  for (const stage of rawStages.value) {
    for (const area of stage.areas) {
      for (const check of area.checks) map.set(check.code, { stage: stage.code, area: area.code })
    }
  }
  return map
})

function decorateArea(area) {
  const outstanding = area.checks.filter(isOutstanding)
  return {
    ...area,
    outstanding,
    // Blocking first: they are what stops the claim being assessable.
    ordered: [...area.checks].sort((a, b) => rank(a) - rank(b)),
    state: areaState(area, outstanding),
  }
}

const stages = computed(() =>
  rawStages.value.map((stage) => {
    const areas = stage.areas.map(decorateArea)
    const outstanding = areas.flatMap((a) => a.outstanding)
    return {
      ...stage,
      areas,
      outstanding,
      state: areaState(stage, outstanding),
    }
  }),
)

function rank(check) {
  if (ADVERSE.includes(check.status)) return 0
  if (isOutstanding(check) && check.weight === 'blocking') return 1
  if (isOutstanding(check)) return 2
  if (check.status === 'not_applicable') return 4
  return 3
}

function areaState(area, outstanding) {
  if (area.barred) return { tone: 'danger', label: 'Possible bar' }
  if (area.lapsed) return { tone: 'danger', label: 'Notice may have lapsed' }
  if (area.blocking_outstanding) {
    return { tone: 'warning', label: `${area.blocking_outstanding} blocking` }
  }
  if (outstanding.length) return { tone: 'info', label: `${outstanding.length} to check` }
  return { tone: 'success', label: 'All recorded' }
}

/** Stages holding something that blocks assessment open themselves. */
function defaultOpen() {
  return rawStages.value
    .filter((stage) => stage.barred || stage.lapsed || stage.blocking_outstanding)
    .map((stage) => stage.code)
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

/**
 * A check gated on another one names it: "Cannot be determined until NC1 is
 * resolved: ...". Under the old flat list the reader could scan for NC1; on a
 * chain they cannot, because the check that blocks EV3 lives in a different
 * stage — and under this hierarchy a Notice check can gate an Event check
 * shown above it. So the code becomes a link that opens where it lives.
 */
const UNRESOLVED = /^Cannot be determined until ([A-Z]{2}\d+) is resolved: ?/

function gatedOn(check) {
  const code = UNRESOLVED.exec(check.detail ?? '')?.[1]
  return code && locationByCode.value.has(code) ? code : null
}

function detailWithoutGate(check) {
  return (check.detail ?? '').replace(UNRESOLVED, '')
}

const focused = ref(null)

/** Open the stage holding `code` and mark it, so the eye lands on it. */
function reveal(code) {
  const at = locationByCode.value.get(code)
  if (!at) return
  if (!open.value.includes(at.stage)) open.value = [...open.value, at.stage]
  // It may be satisfied, in which case the outstanding-only filter hides it.
  showSatisfied.value = true
  focused.value = code
  nextTick(() => {
    document
      .getElementById(`screen-check-${code}`)
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  })
}
</script>

<template>
  <div class="stack gap-4">
    <!-- The one outcome that is adverse on the contract, not on the file. -->
    <section v-if="adverse.length" class="surface screen__bar" role="alert">
      <div class="row gap-2">
        <i class="pi pi-exclamation-triangle" aria-hidden="true" />
        <p class="screen__bar-title">{{ screening.outcome_label }}</p>
      </div>
      <p v-for="check in adverse" :key="check.code" class="text-sm">{{ check.detail }}</p>
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

      <!--
        The chain: Event -> Notice -> Claim. Three stages, not five peers,
        because the contractual basis, the records and the relief are parts of
        the Claim (Sub-Clause 20.2.4(b), (c), (d)) rather than steps beside it.
      -->
      <ol class="screen__stages">
        <li v-for="(stage, i) in stages" :key="stage.code" class="screen__stage-slot">
          <button
            type="button"
            class="screen__stage"
            :class="[`is-${stage.state.tone}`, { 'is-open': open.includes(stage.code) }]"
            :aria-expanded="open.includes(stage.code)"
            :aria-controls="`screen-stage-${stage.code}`"
            @click="toggle(stage.code)"
          >
            <span class="row gap-2">
              <span class="screen__step" aria-hidden="true">{{ i + 1 }}</span>
              <i :class="STAGE_ICONS[stage.code]" aria-hidden="true" />
              <span class="screen__stage-label">{{ stage.label }}</span>
            </span>
            <span class="text-xs text-muted screen__stage-desc">{{ stage.description }}</span>
            <StatusBadge :tone="stage.state.tone" :label="stage.state.label" size="sm" />
            <!-- The parts of the Claim, named on the tile so the hierarchy is
                 legible before anything is opened. -->
            <span v-if="stage.areas.length > 1" class="screen__parts">
              <span v-for="area in stage.areas" :key="area.code" class="screen__part">
                {{ AREA_SHORT[area.code] ?? area.label }}
              </span>
            </span>
          </button>
          <i
            v-if="i < stages.length - 1"
            class="pi pi-angle-right screen__arrow"
            aria-hidden="true"
          />
        </li>
      </ol>

      <div v-for="stage in stages" :key="`panel-${stage.code}`">
        <section
          v-if="open.includes(stage.code)"
          :id="`screen-stage-${stage.code}`"
          class="screen__checks"
        >
          <section
            v-for="area in stage.areas"
            :key="area.code"
            class="screen__area-block"
            :class="{ 'is-nested': stage.areas.length > 1 }"
          >
            <p class="text-overline row gap-2">
              <i :class="AREA_ICONS[area.code]" aria-hidden="true" />
              {{ area.label }}
            </p>
            <p v-if="!visibleChecks(area).length" class="text-sm text-muted">
              Every applicable check in this area is satisfied.
            </p>
            <article
              v-for="check in visibleChecks(area)"
              :id="`screen-check-${check.code}`"
              :key="check.code"
              class="screen__check"
              :class="{
                'is-outstanding': isOutstanding(check),
                'is-focused': focused === check.code,
              }"
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
              <p v-if="gatedOn(check)" class="text-sm screen__detail">
                Cannot be determined until
                <button type="button" class="screen__dep" @click="reveal(gatedOn(check))">
                  {{ gatedOn(check) }}
                </button>
                is resolved: {{ detailWithoutGate(check) }}
              </p>
              <p v-else class="text-sm screen__detail">{{ check.detail }}</p>
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

/* The chain. Three stages separated by an arrow, not a grid of peers. */
.screen__stages {
  display: flex;
  align-items: stretch;
  gap: var(--space-2);
  list-style: none;
  margin: 0;
  padding: 0;
  counter-reset: none;
}

.screen__stage-slot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1 1 0;
  min-width: 0;
}

.screen__arrow {
  flex: none;
  color: var(--color-text-muted);
}

.screen__stage {
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

.screen__stage:hover {
  border-color: var(--color-border-strong, var(--color-accent));
}

.screen__stage.is-open {
  border-color: var(--color-accent);
  box-shadow: inset 0 -2px 0 var(--color-accent);
}

.screen__stage.is-danger {
  border-left: 3px solid var(--color-danger);
}

.screen__stage.is-warning {
  border-left: 3px solid var(--color-warning);
}

.screen__stage.is-info {
  border-left: 3px solid var(--color-info);
}

.screen__stage.is-success {
  border-left: 3px solid var(--color-success);
}

.screen__stage-label {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  white-space: nowrap;
}

.screen__stage-desc {
  line-height: var(--leading-snug, 1.3);
}

/* The step number. The chain is an order, and the order is the point. */
.screen__step {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 50%;
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  background: var(--color-surface-sunken, var(--color-border));
  color: var(--color-text-muted);
}

/* The Claim's parts, named on the tile before it is opened. */
.screen__parts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: auto;
}

.screen__part {
  padding: 1px var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm, 4px);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.screen__area-block + .screen__area-block {
  padding-top: var(--space-3);
}

/* Inside the Claim stage the areas are parts of one thing, so they are
   indented under it rather than reading as three more peers. */
.screen__area-block.is-nested {
  padding-left: var(--space-3);
  border-left: 2px solid var(--color-border);
}

.screen__dep {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-accent);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}

.screen__check.is-focused {
  background: var(--color-accent-subtle, var(--color-surface-sunken));
  border-left-color: var(--color-accent);
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

@media (max-width: 860px) {
  /* The chain reads top to bottom instead, arrows turned to match. */
  .screen__stages {
    flex-direction: column;
  }

  .screen__stage-slot {
    flex-direction: column;
    align-items: stretch;
  }

  .screen__arrow {
    align-self: center;
    transform: rotate(90deg);
  }
}
</style>
