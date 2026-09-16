<script setup>
import { computed, ref } from 'vue'

import CitationLink from './CitationLink.vue'
import EpistemicBadge from './EpistemicBadge.vue'
import * as aiApi from '@/api/ai'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import FormField from '@/components/common/FormField.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/utils/format'

/**
 * One AI finding, with its citations and its review state.
 *
 * The AI's original statement is always shown. A reviewer's amendment appears
 * beside it, never in place of it: the record must show both what was asserted
 * and what a person decided about it.
 */
const props = defineProps({
  finding: { type: Object, required: true },
  canReview: { type: Boolean, default: false },
})

const emit = defineEmits(['reviewed'])

const ui = useUiStore()
const MIN_REASON = 10

const open = ref(false)
const action = ref('accept')
const reason = ref('')
const amended = ref('')
const saving = ref(false)
const fieldErrors = ref({})

const review = computed(() => props.finding.review ?? { state: 'unreviewed' })
const state = computed(() => review.value.state ?? 'unreviewed')
const latest = computed(() => review.value.latest ?? null)
const isAmended = computed(() => state.value === 'amended')
const isRejected = computed(() => state.value === 'rejected')

const STATE_LABELS = {
  unreviewed: 'Unreviewed AI finding',
  accepted: 'Accepted',
  amended: 'Amended',
  rejected: 'Rejected',
}

const actionTitles = {
  accept: 'Accept this finding',
  amend: 'Amend this finding',
  reject: 'Reject this finding',
}

const actionLabels = {
  accept: 'Accept finding',
  amend: 'Save amendment',
  reject: 'Reject finding',
}

const needsReason = computed(() => action.value === 'reject' || action.value === 'amend')

const canSubmit = computed(() => {
  if (saving.value) return false
  if (needsReason.value && reason.value.trim().length < MIN_REASON) return false
  if (action.value === 'amend' && !amended.value.trim()) return false
  return true
})

function start(next) {
  action.value = next
  reason.value = ''
  amended.value = next === 'amend' ? props.finding.statement : ''
  fieldErrors.value = {}
  open.value = true
}

async function submit() {
  if (!canSubmit.value) return
  saving.value = true
  fieldErrors.value = {}
  try {
    const updated = await aiApi.reviewFinding(props.finding.id, {
      action: action.value,
      reason: reason.value,
      amendedStatement: amended.value,
    })
    emit('reviewed', updated)
    ui.notifySuccess('Review recorded', 'The finding is now ' + updated.review.state + '.')
    open.value = false
  } catch (error) {
    const field = error?.details?.field
    fieldErrors.value = field ? { [field]: error.message } : {}
    if (!field) ui.notifyError(error, 'Could not record the review')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <article
    class="finding"
    :class="{ 'finding--unreviewed': state === 'unreviewed', 'finding--rejected': isRejected }"
  >
    <div class="row between gap-3 wrap">
      <div class="row gap-2 wrap">
        <EpistemicBadge :status="finding.epistemic_status" />
        <StatusBadge :status="state" :label="STATE_LABELS[state] || state" size="sm" />
      </div>
      <div v-if="canReview" class="row gap-1">
        <AppButton size="sm" variant="ghost" label="Accept" icon="pi pi-check" @click="start('accept')" />
        <AppButton size="sm" variant="ghost" label="Amend" icon="pi pi-pencil" @click="start('amend')" />
        <AppButton size="sm" variant="ghost" label="Reject" icon="pi pi-times" @click="start('reject')" />
      </div>
    </div>

    <p class="finding__statement" :class="{ 'finding__statement--struck': isRejected }">
      {{ finding.statement }}
    </p>

    <p v-if="isAmended" class="finding__amended">
      <span class="text-overline">Reviewer's amendment</span>
      {{ finding.effective_statement }}
    </p>

    <p v-if="latest && latest.reason" class="finding__reason">
      {{ latest.reason }}
      <span class="text-muted">
        — {{ latest.reviewer_email }}, {{ formatDateTime(latest.reviewed_at) }}
      </span>
    </p>

    <div v-if="finding.citations && finding.citations.length" class="finding__citations">
      <CitationLink v-for="(citation, index) in finding.citations" :key="index" :citation="citation" />
    </div>
    <p v-else class="text-xs text-muted">No citation — this statement is not tied to a source.</p>

    <AppDialog
      v-model="open"
      :title="actionTitles[action]"
      :busy="saving"
      description="Your decision is recorded against the finding. The AI's original wording is kept either way."
    >
      <div class="stack gap-4">
        <blockquote class="finding__quote">{{ finding.statement }}</blockquote>

        <FormField
          v-if="action === 'amend'"
          label="Amended statement"
          for-id="amended"
          required
          :error="fieldErrors.amended_statement"
          hint="What the finding should say. It must differ from the original."
        >
          <textarea id="amended" v-model="amended" class="field-input" rows="3" />
        </FormField>

        <FormField
          v-if="needsReason"
          label="Reason"
          for-id="reason"
          required
          :error="fieldErrors.reason"
          hint="At least 10 characters. This becomes part of the claim record."
        >
          <textarea id="reason" v-model="reason" class="field-input" rows="3" />
        </FormField>

        <FormField v-else label="Note (optional)" for-id="reason">
          <textarea id="reason" v-model="reason" class="field-input" rows="2" />
        </FormField>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="saving" @click="open = false" />
        <AppButton
          variant="primary"
          :label="actionLabels[action]"
          :loading="saving"
          :disabled="!canSubmit"
          @click="submit"
        />
      </template>
    </AppDialog>
  </article>
</template>

<style scoped>
.finding {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.finding--unreviewed {
  border-left: 3px solid var(--color-warning);
}

.finding--rejected {
  opacity: 0.85;
}

.finding__statement {
  font-size: var(--text-sm);
}

.finding__statement--struck {
  text-decoration: line-through;
  color: var(--color-text-muted);
}

.finding__amended {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2);
  font-size: var(--text-sm);
  background: var(--color-accent-subtle);
  border-radius: var(--radius-sm);
}

.finding__reason {
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.finding__citations {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-1);
}

.finding__quote {
  margin: 0;
  padding: var(--space-3);
  font-size: var(--text-sm);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-md);
}
</style>
