<script setup>
import { computed, ref, watch } from 'vue'

import { useRouter } from 'vue-router'

import * as claimsApi from '@/api/claims'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import { useUiStore } from '@/stores/ui'
import { formatBytes } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * Upload a whole claim submission — the letter and everything behind it.
 *
 * The bundle is split into the documents it is made of: the claim letter,
 * notices, programmes, site records, invoices and the rest. The split is
 * shown page by page for a person to correct; saving files the letter as the
 * claim and each part as evidence of what it proves.
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectId: { type: String, required: true },
  /** File onto this claim rather than creating one. */
  claimId: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'applied'])

const router = useRouter()
const ui = useUiStore()

const CLAIM_TYPES = [
  ['eot', 'Extension of Time'],
  ['cost', 'Additional Cost'],
  ['variation', 'Variation'],
  ['delay', 'Delay'],
  ['disruption', 'Disruption'],
  ['acceleration', 'Acceleration'],
  ['payment', 'Payment'],
  ['compensation_event', 'Compensation Event'],
  ['other', 'Other'],
]

const ELEMENTS = [
  ['', 'Nothing in particular'],
  ['event', 'The triggering event'],
  ['notice', 'Notice given in time'],
  ['causation', 'Causation'],
  ['responsibility', 'Responsibility'],
  ['instruction', 'The instruction'],
  ['time_impact', 'Time impact'],
  ['cost_impact', 'Cost incurred'],
  ['quantum', 'Quantification'],
  ['mitigation', 'Mitigation'],
]

const step = ref('choose') // choose | reading | review
const file = ref(null)
const progress = ref(0)
const error = ref(null)
const duplicateOf = ref(null)
const result = ref(null)
const segments = ref([])
const claims = ref([])
const target = ref('new')
const claim = ref('')
const newClaim = ref({})
const letterDate = ref('')
const clause = ref('')
const applying = ref(false)
const fieldErrors = ref({})

const kinds = computed(() => result.value?.kinds ?? [])
const draft = computed(() =>
  Object.fromEntries((result.value?.draft?.fields ?? []).map((field) => [field.name, field])),
)
const letter = computed(() => segments.value.find((s) => s.include && s.kind === 'claim_letter'))
const clauseHints = computed(() => draft.value.contractual_basis?.value ?? [])
const pageCount = computed(() => result.value?.pages_read ?? 0)

const coverage = computed(() => {
  const covered = new Set()
  for (const s of segments.value) for (let n = Number(s.first_page); n <= Number(s.last_page); n++) covered.add(n)
  const missing = []
  for (let n = 1; n <= pageCount.value; n++) if (!covered.has(n)) missing.push(n)
  return missing
})

const canApply = computed(() => {
  if (applying.value || !letter.value) return false
  if (target.value === 'existing' && !claim.value) return false
  if (target.value === 'new' && !newClaim.value.title?.trim()) return false
  return true
})

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    step.value = 'choose'
    file.value = null
    error.value = null
    duplicateOf.value = null
    result.value = null
    segments.value = []
    fieldErrors.value = {}
    target.value = props.claimId ? 'existing' : 'new'
    claim.value = props.claimId || ''
    try {
      claims.value = rowsOf(await claimsApi.listClaims({ project: props.projectId, page_size: 200 }))
    } catch {
      claims.value = []
    }
  },
)

function onFile(event) {
  file.value = event.target.files?.[0] ?? null
  duplicateOf.value = null
}

async function read({ existing = null, allowDuplicate = false } = {}) {
  step.value = 'reading'
  error.value = null
  progress.value = 0
  try {
    result.value = await claimsApi.readClaimBundle({
      projectId: props.projectId,
      file: existing ? undefined : file.value,
      document: existing || undefined,
      allowDuplicate,
      onProgress: (value) => {
        progress.value = value
      },
    })
    segments.value = result.value.segments.map((s) => ({ ...s, include: true, element: s.element || '' }))
    prefill()
    step.value = 'review'
  } catch (err) {
    if (err?.details?.reason === 'duplicate_of_another_document') {
      duplicateOf.value = err.details
      step.value = 'choose'
      return
    }
    error.value = err
    step.value = 'choose'
  }
}

function draftValue(name) {
  return draft.value[name]?.value ?? ''
}

function prefill() {
  const basis = draftValue('contractual_basis') || []
  newClaim.value = {
    title: draftValue('title') || result.value.segments.find((s) => s.kind === 'claim_letter')?.title || '',
    reference: draftValue('reference') || '',
    claim_type: draftValue('claim_type') || 'eot',
    event_date: draftValue('event_date') || '',
    awareness_date: draftValue('awareness_date') || '',
    amount_claimed: draftValue('amount_claimed') || '',
    currency: draftValue('currency') || '',
    time_claimed_days: draftValue('time_claimed_days') || '',
    contractual_basis: Array.isArray(basis) ? basis.join(', ') : String(basis || ''),
    description: draftValue('description') || '',
  }
  letterDate.value = draftValue('submission_date') || letter.value?.date || ''
  clause.value = ''
}

function onKind(segment) {
  const kind = kinds.value.find((k) => k.code === segment.kind)
  segment.element = kind?.element || ''
}

function split(index) {
  const segment = segments.value[index]
  if (segment.first_page === segment.last_page) return
  const at = Number(segment.first_page) + Math.floor((segment.last_page - segment.first_page + 1) / 2)
  const second = { ...segment, first_page: at, title: '', date: null }
  segment.last_page = at - 1
  segments.value.splice(index + 1, 0, second)
}

function joinNext(index) {
  const segment = segments.value[index]
  const next = segments.value[index + 1]
  if (!next) return
  segment.last_page = next.last_page
  segments.value.splice(index + 1, 1)
}

async function apply() {
  if (!canApply.value) return
  applying.value = true
  fieldErrors.value = {}
  try {
    const payload = {
      document: result.value.document.id,
      letter_date: letterDate.value || null,
      clause_number: clause.value.trim(),
      segments: segments.value.map((s) => ({
        first_page: Number(s.first_page),
        last_page: Number(s.last_page),
        kind: s.kind,
        title: s.title,
        date: s.date || null,
        element: s.element || null,
        include: s.include,
        clause_number: clause.value.trim(),
      })),
    }
    if (target.value === 'existing') payload.claim = claim.value
    else {
      payload.new_claim = {
        ...newClaim.value,
        contractual_basis: newClaim.value.contractual_basis
          .split(',')
          .map((c) => c.trim())
          .filter(Boolean),
      }
    }
    const filed = await claimsApi.applyClaimBundle(payload)
    ui.notifySuccess(
      'Claim filed',
      `Claim letter ${filed.letter ? 'filed with its date' : 'filed without a date — add one to check its deadline'}; ` +
        `${filed.evidence} supporting part(s) and ${filed.notices} notice(s) recorded.`,
    )
    emit('applied', filed)
    emit('update:modelValue', false)
    router.push({ name: 'claim-detail', params: { claimId: filed.claim }, query: { tab: 'checklist' } })
  } catch (err) {
    const field = err?.details?.field
    fieldErrors.value = field ? { [field]: err.message } : {}
    if (!field) ui.notifyError(err, 'Could not file the claim')
  } finally {
    applying.value = false
  }
}
</script>

<template>
  <AppDialog
    :model-value="modelValue"
    title="Upload a claim"
    description="Upload the whole claim submission — the claim letter and its supporting documents in one PDF. It is split into its parts for you to check, then filed on the claim."
    size="xl"
    :busy="step === 'reading' || applying"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <!-- 1. Choose -->
    <div v-if="step === 'choose'" class="stack gap-4">
      <FormField label="Claim submission" for-id="bundle-file" required hint="One PDF holding the claim letter and its annexures. A scan is fine.">
        <input id="bundle-file" class="field-input" type="file" accept=".pdf,image/*" @change="onFile" />
        <p v-if="file" class="text-xs text-muted">{{ file.name }} · {{ formatBytes(file.size) }}</p>
      </FormField>
      <div v-if="duplicateOf" class="bundle__warning">
        This file is already in the project as <strong>{{ duplicateOf.document_title }}</strong>.
      </div>
      <ErrorState v-if="error" :error="error" title="Could not read the claim" compact />
    </div>

    <!-- 2. Reading -->
    <div v-else-if="step === 'reading'" class="bundle__reading">
      <i class="pi pi-spin pi-spinner" aria-hidden="true" />
      <p class="text-sm">
        {{ progress < 100 ? `Uploading ${progress}%` : 'Reading the submission — finding the claim letter and sorting the supporting documents…' }}
      </p>
      <p class="text-xs text-muted">
        This runs on this computer. A large scanned bundle can take several minutes.
      </p>
    </div>

    <!-- 3. Review -->
    <div v-else-if="step === 'review'" class="stack gap-4">
      <div v-if="result.notes.length" class="bundle__notes">
        <p v-for="note in result.notes" :key="note" class="text-xs">{{ note }}</p>
      </div>

      <!-- The parts -->
      <section class="stack gap-2">
        <div class="row between gap-2 wrap">
          <p class="section-title">What the {{ pageCount }} pages contain</p>
          <span class="text-xs text-muted">
            Split by {{ result.model || 'page headings (no model configured)' }}. Correct anything that is wrong.
          </span>
        </div>
        <p v-if="coverage.length" class="text-xs text-warning">Pages not in any part: {{ coverage.join(', ') }}</p>

        <div class="table-scroll">
          <table class="data-table bundle__table">
            <thead>
              <tr>
                <th scope="col">Use</th>
                <th scope="col">Pages</th>
                <th scope="col">What it is</th>
                <th scope="col">Title</th>
                <th scope="col">Date</th>
                <th scope="col">Proves</th>
                <th scope="col"><span class="sr-only">Split or join</span></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(segment, index) in segments"
                :key="`${index}-${segment.first_page}`"
                :class="{ 'is-letter': segment.kind === 'claim_letter', 'is-off': !segment.include }"
              >
                <td><input v-model="segment.include" type="checkbox" :aria-label="`Use pages ${segment.first_page}–${segment.last_page}`" /></td>
                <td class="bundle__pages">
                  <input v-model.number="segment.first_page" class="field-input" type="number" min="1" :max="pageCount" aria-label="First page" />
                  –
                  <input v-model.number="segment.last_page" class="field-input" type="number" min="1" :max="pageCount" aria-label="Last page" />
                </td>
                <td>
                  <select v-model="segment.kind" class="field-input" aria-label="What it is" @change="onKind(segment)">
                    <option v-for="kind in kinds" :key="kind.code" :value="kind.code">{{ kind.label }}</option>
                  </select>
                </td>
                <td><input v-model="segment.title" class="field-input" type="text" aria-label="Title" /></td>
                <td><input v-model="segment.date" class="field-input" type="date" aria-label="Date" /></td>
                <td>
                  <select
                    v-model="segment.element"
                    class="field-input"
                    aria-label="What it proves"
                    :disabled="segment.kind === 'claim_letter'"
                  >
                    <option v-for="[code, label] in ELEMENTS" :key="code" :value="code">{{ label }}</option>
                  </select>
                </td>
                <td class="bundle__actions">
                  <button type="button" title="Split this part in two" :disabled="segment.first_page === segment.last_page" @click="split(index)">
                    <i class="pi pi-arrows-v" aria-hidden="true" />
                  </button>
                  <button type="button" title="Join with the next part" :disabled="index === segments.length - 1" @click="joinNext(index)">
                    <i class="pi pi-link" aria-hidden="true" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!letter" class="text-xs text-danger">
          Mark which pages are the claim letter (set "What it is" to Claim letter).
        </p>
      </section>

      <!-- The claim letter -->
      <section class="bundle__group">
        <p class="section-title">The claim letter</p>
        <div class="row gap-3 wrap">
          <FormField
            label="Date on the claim letter"
            for-id="b-date"
            class="grow"
            :error="fieldErrors.letter_date"
            hint="Checked against the deadline for the detailed claim."
          >
            <input id="b-date" v-model="letterDate" class="field-input" type="date" />
          </FormField>
          <FormField
            label="Submitted under clause"
            for-id="b-clause"
            class="grow"
            hint="The detailed-claim provision it answers, e.g. 44.2 or 53.3 (1987), 20.1 (1999), 20.2.4 (2017)."
          >
            <input id="b-clause" v-model="clause" class="field-input" type="text" list="b-clause-hints" />
            <datalist id="b-clause-hints">
              <option v-for="hint in clauseHints" :key="hint" :value="hint" />
            </datalist>
          </FormField>
        </div>
      </section>

      <!-- Which claim -->
      <section class="bundle__group">
        <p class="section-title">Which claim</p>
        <div class="row gap-4 wrap">
          <label class="bundle__choice"><input v-model="target" type="radio" value="new" /> Create a new claim from this letter</label>
          <label class="bundle__choice"><input v-model="target" type="radio" value="existing" :disabled="!claims.length" /> An existing claim</label>
        </div>

        <FormField v-if="target === 'existing'" label="Claim" for-id="b-claim" required :error="fieldErrors.claim">
          <select id="b-claim" v-model="claim" class="field-input">
            <option value="" disabled>Choose a claim</option>
            <option v-for="item in claims" :key="item.id" :value="item.id">
              {{ item.reference ? item.reference + ' · ' : '' }}{{ item.title }}
            </option>
          </select>
        </FormField>

        <template v-else>
          <p class="text-xs text-muted">Read from the claim letter. Fields marked ⚠ could not be found on the page as quoted.</p>
          <div class="row gap-3 wrap">
            <FormField label="Title" for-id="bc-title" class="grow" required :error="fieldErrors.title">
              <input id="bc-title" v-model="newClaim.title" class="field-input" type="text" />
            </FormField>
            <FormField label="Reference" for-id="bc-ref" class="grow">
              <input id="bc-ref" v-model="newClaim.reference" class="field-input" type="text" />
            </FormField>
            <FormField label="Type" for-id="bc-type" class="grow">
              <select id="bc-type" v-model="newClaim.claim_type" class="field-input">
                <option v-for="[code, label] in CLAIM_TYPES" :key="code" :value="code">{{ label }}</option>
              </select>
            </FormField>
          </div>
          <div class="row gap-3 wrap">
            <FormField label="Date of the event" for-id="bc-event" class="grow">
              <input id="bc-event" v-model="newClaim.event_date" class="field-input" type="date" />
            </FormField>
            <FormField label="Date the contractor became aware" for-id="bc-aware" class="grow" hint="Notice deadlines run from this.">
              <input id="bc-aware" v-model="newClaim.awareness_date" class="field-input" type="date" />
            </FormField>
            <FormField label="Clauses relied on" for-id="bc-basis" class="grow">
              <input id="bc-basis" v-model="newClaim.contractual_basis" class="field-input" type="text" />
            </FormField>
          </div>
          <div class="row gap-3 wrap">
            <FormField label="Time claimed (days)" for-id="bc-days" class="grow">
              <input id="bc-days" v-model="newClaim.time_claimed_days" class="field-input" type="number" />
            </FormField>
            <FormField :label="`Amount claimed${draft.amount_claimed && !draft.amount_claimed.quote_verified && draft.amount_claimed.found ? ' ⚠' : ''}`" for-id="bc-amount" class="grow">
              <input id="bc-amount" v-model="newClaim.amount_claimed" class="field-input" type="number" step="0.01" />
            </FormField>
            <FormField label="Currency" for-id="bc-currency" class="grow">
              <input id="bc-currency" v-model="newClaim.currency" class="field-input" type="text" maxlength="3" />
            </FormField>
          </div>
        </template>
      </section>
    </div>

    <template #footer>
      <AppButton variant="ghost" label="Cancel" :disabled="step === 'reading' || applying" @click="emit('update:modelValue', false)" />
      <template v-if="step === 'choose'">
        <template v-if="duplicateOf">
          <AppButton variant="ghost" label="Upload anyway" @click="read({ allowDuplicate: true })" />
          <AppButton variant="primary" label="Read the copy already uploaded" @click="read({ existing: duplicateOf.document_id })" />
        </template>
        <AppButton v-else variant="primary" icon="pi pi-sparkles" label="Upload and split" :disabled="!file" @click="read()" />
      </template>
      <AppButton
        v-else-if="step === 'review'"
        variant="primary"
        icon="pi pi-check"
        :label="target === 'new' ? 'Create claim and file' : 'File on the claim'"
        :loading="applying"
        :disabled="!canApply"
        @click="apply"
      />
    </template>
  </AppDialog>
</template>

<style scoped>
.bundle__reading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-6) 0;
  text-align: center;
}

.bundle__reading .pi {
  font-size: 24px;
  color: var(--color-accent);
}

.bundle__notes {
  padding: var(--space-2) var(--space-3);
  color: var(--color-info);
  background: var(--color-info-subtle);
  border-radius: var(--radius-sm);
}

.bundle__warning {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-warning);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-sm);
}

.bundle__group {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-subtle);
}

.bundle__table .field-input {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
}

.bundle__table tr.is-letter td {
  background: var(--color-surface-selected);
}

.bundle__table tr.is-off td {
  opacity: 0.5;
}

.bundle__pages {
  white-space: nowrap;
}

.bundle__pages .field-input {
  width: 56px;
}

.bundle__actions {
  white-space: nowrap;
}

.bundle__actions button {
  padding: 2px 6px;
  color: var(--color-text-secondary);
}

.bundle__actions button:disabled {
  opacity: 0.3;
}

.bundle__choice {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
}
</style>
