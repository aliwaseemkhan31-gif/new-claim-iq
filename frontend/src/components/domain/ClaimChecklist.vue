<script setup>
import { formatDate } from '@/utils/format'

/**
 * A claim's checklist: event, notice, claim submission, evidence.
 *
 * Each row answers "is it on record" and, where the contract sets a deadline,
 * "was it in time". The statuses come from the server, which computes them
 * with the same deadline rules as screening and the analysis engine.
 */
defineProps({
  checklist: { type: Object, required: true },
  /** Offer the "add it" action on rows that can be fixed by filing a document. */
  canEdit: { type: Boolean, default: false },
})

const emit = defineEmits(['act'])

const STATUS = {
  ok: { icon: 'pi pi-check-circle', label: 'Done', tone: 'success' },
  late: { icon: 'pi pi-clock', label: 'Late', tone: 'warning' },
  barred: { icon: 'pi pi-ban', label: 'Late — time bar', tone: 'danger' },
  missing: { icon: 'pi pi-times-circle', label: 'Missing', tone: 'danger' },
  due: { icon: 'pi pi-hourglass', label: 'Due', tone: 'info' },
  unknown: { icon: 'pi pi-question-circle', label: 'Cannot tell yet', tone: 'warning' },
  optional: { icon: 'pi pi-minus-circle', label: 'Optional', tone: 'neutral' },
  contested: { icon: 'pi pi-exclamation-triangle', label: 'Contested', tone: 'warning' },
}

/**
 * What fixes a row, when filing a document or editing the claim can.
 * Rows already done have nothing to offer.
 */
function actionFor(section, row) {
  if (row.status === 'ok' || row.status === 'optional') return null
  if (row.key === 'event_date' || row.key === 'awareness_date') {
    return { kind: 'edit', label: 'Record the date' }
  }
  if (section.code === 'notice') return { kind: 'file', role: 'notice', label: 'Add notice' }
  // The claim document is the detailed claim: filing it answers both rows.
  if (section.code === 'claim') {
    return { kind: 'file', role: 'claim_submission', label: 'Add claim document' }
  }
  if (row.key.startsWith('evidence:')) {
    return {
      kind: 'file',
      role: 'supporting',
      element: row.key.slice('evidence:'.length),
      label: 'Add document',
    }
  }
  return null
}
</script>

<template>
  <div class="checklist">
    <section v-for="section in checklist.sections" :key="section.code" class="checklist__section">
      <header class="checklist__head">
        <p class="checklist__heading">{{ section.label }}</p>
        <span v-if="section.outstanding" class="checklist__count is-warning">
          {{ section.outstanding }} to do
        </span>
        <span v-else class="checklist__count is-success">
          <i class="pi pi-check" aria-hidden="true" /> complete
        </span>
      </header>

      <ul class="checklist__rows">
        <li
          v-for="row in section.rows"
          :key="row.key"
          class="checklist__row"
          :class="`is-${STATUS[row.status]?.tone || 'neutral'}`"
        >
          <i :class="STATUS[row.status]?.icon || 'pi pi-circle'" class="checklist__icon" aria-hidden="true" />
          <div class="checklist__body">
            <p class="checklist__label">
              {{ row.label }}
              <span class="checklist__status">{{ STATUS[row.status]?.label || row.status }}</span>
              <span v-if="row.is_amended" class="checklist__tag" title="Set by this contract's own terms">
                amended by contract
              </span>
            </p>
            <p class="checklist__detail">{{ row.detail }}</p>
            <p v-if="row.rule" class="checklist__meta">
              {{ row.rule }}<template v-if="row.deadline"> · deadline {{ formatDate(row.deadline) }}</template>
            </p>
            <p v-if="row.document_title" class="checklist__meta">
              <i class="pi pi-file" aria-hidden="true" /> {{ row.document_title }}
              <span v-if="row.filed === false" class="text-warning">
                · recorded, but no file is attached to the claim
              </span>
            </p>
          </div>
          <button
            v-if="canEdit && actionFor(section, row)"
            type="button"
            class="checklist__action"
            @click="emit('act', actionFor(section, row))"
          >
            {{ actionFor(section, row).label }}
            <i class="pi pi-arrow-right" aria-hidden="true" />
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.checklist {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: var(--space-4);
}

.checklist__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.checklist__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding-bottom: var(--space-1);
  border-bottom: 1px solid var(--color-border-subtle);
}

.checklist__heading {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-secondary);
}

.checklist__count {
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
}

.checklist__count.is-warning {
  color: var(--color-warning);
}

.checklist__count.is-success {
  color: var(--color-success);
}

.checklist__rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.checklist__row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-2);
  border-radius: var(--radius-md);
  border-left: 3px solid transparent;
  background: var(--color-surface-sunken);
}

.checklist__row.is-success {
  border-left-color: var(--color-success);
}

.checklist__row.is-warning {
  border-left-color: var(--color-warning);
}

.checklist__row.is-danger {
  border-left-color: var(--color-danger);
}

.checklist__row.is-info {
  border-left-color: var(--color-info);
}

.checklist__icon {
  margin-top: 2px;
  font-size: 15px;
}

.is-success .checklist__icon {
  color: var(--color-success);
}

.is-warning .checklist__icon {
  color: var(--color-warning);
}

.is-danger .checklist__icon {
  color: var(--color-danger);
}

.is-info .checklist__icon {
  color: var(--color-info);
}

.is-neutral .checklist__icon {
  color: var(--color-text-muted);
}

.checklist__body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.checklist__label {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.checklist__status {
  margin-left: var(--space-1);
  font-size: var(--text-xs);
  font-weight: var(--weight-regular, 400);
  color: var(--color-text-muted);
}

.checklist__tag {
  margin-left: var(--space-1);
  padding: 0 6px;
  font-size: var(--text-2xs);
  color: var(--color-info);
  border: 1px solid var(--color-info);
  border-radius: var(--radius-full);
}

.checklist__detail {
  font-size: var(--text-xs);
  line-height: 1.5;
  color: var(--color-text-secondary);
}

.checklist__meta {
  font-size: var(--text-2xs);
  color: var(--color-text-muted);
}

.checklist__action {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: var(--color-accent);
  white-space: nowrap;
  border-radius: var(--radius-sm);
}

.checklist__action:hover {
  background: var(--color-surface-hover);
}
</style>
