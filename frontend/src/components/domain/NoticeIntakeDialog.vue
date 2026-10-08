<script setup>
import { computed, ref, watch } from 'vue'

import * as claimsApi from '@/api/claims'
import * as correspondenceApi from '@/api/correspondence'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import { useUiStore } from '@/stores/ui'
import { formatBytes, formatDate } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * Upload a notice letter and let the model read it.
 *
 * The reading is a proposal. Each value is shown beside the words it was read
 * from, and whether those words are on the letter, so checking it takes a
 * glance rather than a re-read. The date on the letter is the one the
 * deadline turns on, so it is the one field that must be confirmed.
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectId: { type: String, required: true },
  /** Preselect a claim, when opened from one. */
  claimId: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'saved'])

const ui = useUiStore()

const KINDS = [
  ['notice_of_claim', 'Notice of claim / notice of delay'],
  ['detailed_particulars', 'Detailed particulars / fully detailed claim'],
  ['other', 'Other letter (not a notice)'],
]

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

const step = ref('choose') // choose | reading | review
const file = ref(null)
const progress = ref(0)
const error = ref(null)
const duplicateOf = ref(null)
const stored = ref(null)
const reading = ref(null)
const claims = ref([])
const saving = ref(false)
const fieldErrors = ref({})

const form = ref({})
const target = ref('existing') // existing | new | none
const newClaim = ref({})

const fieldsByName = computed(() =>
  Object.fromEntries((reading.value?.fields ?? []).map((field) => [field.name, field])),
)

const clauseOptions = computed(() => fieldsByName.value.clauses?.value ?? [])

const suggestedIds = computed(() => new Set((reading.value?.suggested_claims ?? []).map((c) => c.id)))

const orderedClaims = computed(() => [
  ...claims.value.filter((claim) => suggestedIds.value.has(claim.id)),
  ...claims.value.filter((claim) => !suggestedIds.value.has(claim.id)),
])

const canSave = computed(() => {
  if (saving.value || !form.value.letter_date || !form.value.clause_number) return false
  if (target.value === 'existing' && !form.value.claim) return false
  if (target.value === 'new' && !newClaim.value.title?.trim()) return false
  return true
})

function reset() {
  step.value = 'choose'
  file.value = null
  progress.value = 0
  error.value = null
  duplicateOf.value = null
  stored.value = null
  reading.value = null
  form.value = {}
  fieldErrors.value = {}
  target.value = 'existing'
}

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    reset()
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
    const result = await correspondenceApi.readNotice({
      projectId: props.projectId,
      file: existing ? undefined : file.value,
      document: existing || undefined,
      allowDuplicate,
      onProgress: (value) => {
        progress.value = value
      },
    })
    stored.value = result.document
    reading.value = result.reading
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

function value(name) {
  return fieldsByName.value[name]?.value ?? ''
}

function prefill() {
  const clauses = value('clauses') || []
  form.value = {
    letter_date: value('letter_date') || '',
    received_date: value('received_date') || '',
    reference: value('reference') || '',
    subject: value('subject') || '',
    sender: value('sender') || '',
    recipient: value('recipient') || '',
    clause_number: clauses[clauses.length > 1 ? clauses.length - 1 : 0] || '',
    notice_kind: value('notice_kind') || 'notice_of_claim',
    event_description: value('event_description') || '',
    title: '',
    claim: props.claimId || reading.value?.suggested_claims?.[0]?.id || '',
    confirmed: true,
  }
  if (clauses.length > 1) form.value.clause_number = clauses.find((c) => c.includes('.')) || clauses[0]
  target.value = form.value.claim ? 'existing' : claims.value.length ? 'existing' : 'new'
  newClaim.value = {
    title: value('subject') || value('event_description') || '',
    claim_type: 'eot',
    event_date: value('event_date') || '',
    awareness_date: '',
    reference: value('claim_reference') || '',
  }
}

/** How far a field can be trusted, in words. */
function trust(name) {
  const field = fieldsByName.value[name]
  if (!field || field.source === 'none') return { tone: 'muted', text: 'Not found on the letter' }
  if (!field.verified) return { tone: 'warning', text: 'Could not be found on the letter as read — check it' }
  if (field.source === 'pattern') return { tone: 'info', text: field.note || 'Read from the page' }
  return { tone: 'success', text: 'Found on the letter' }
}

async function save() {
  if (!canSave.value) return
  saving.value = true
  fieldErrors.value = {}
  try {
    const payload = {
      document: stored.value.id,
      ...form.value,
      clauses: value('clauses') || [],
      claim: target.value === 'existing' ? form.value.claim : null,
      new_claim:
        target.value === 'new'
          ? {
              ...newClaim.value,
              contractual_basis: [form.value.clause_number],
            }
          : null,
    }
    const result = await correspondenceApi.saveNotice(payload)
    ui.notifySuccess(
      'Notice saved',
      result.claim ? 'Filed on the claim; its deadlines have been rechecked.' : 'Added to the notices register.',
    )
    emit('saved', result)
    emit('update:modelValue', false)
  } catch (err) {
    const field = err?.details?.field
    fieldErrors.value = field ? { [field]: err.message } : {}
    if (!field) ui.notifyError(err, 'Could not save the notice')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <AppDialog
    :model-value="modelValue"
    title="Upload a notice"
    description="Upload the notice letter as a PDF or scan. It is read for the date on the letter, the clause it is given under and what it notifies; you check the reading before it is saved."
    size="lg"
    :busy="step === 'reading' || saving"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <!-- 1. Choose -->
    <div v-if="step === 'choose'" class="stack gap-4">
      <FormField label="Notice letter" for-id="notice-file" required>
        <input id="notice-file" class="field-input" type="file" accept=".pdf,image/*" @change="onFile" />
        <p v-if="file" class="text-xs text-muted">{{ file.name }} · {{ formatBytes(file.size) }}</p>
      </FormField>

      <div v-if="duplicateOf" class="intake__warning">
        <p class="text-sm">
          This file is already in the project as <strong>{{ duplicateOf.document_title }}</strong>.
        </p>
      </div>

      <ErrorState v-if="error" :error="error" title="Could not read the notice" compact />
    </div>

    <!-- 2. Reading -->
    <div v-else-if="step === 'reading'" class="intake__reading">
      <i class="pi pi-spin pi-spinner" aria-hidden="true" />
      <p class="text-sm">
        {{ progress < 100 ? `Uploading ${progress}%` : 'Reading the letter — finding its date, reference and clause…' }}
      </p>
      <p class="text-xs text-muted">This runs on this computer and usually takes under a minute.</p>
    </div>

    <!-- 3. Review -->
    <div v-else-if="step === 'review'" class="stack gap-4">
      <div v-if="reading.notes.length" class="intake__notes">
        <p v-for="note in reading.notes" :key="note" class="text-xs">{{ note }}</p>
      </div>

      <section class="intake__group">
        <p class="section-title">Dates on the letter</p>
        <div class="row gap-3 wrap">
          <FormField label="Date on the letter" for-id="n-date" class="grow" required :error="fieldErrors.letter_date">
            <input id="n-date" v-model="form.letter_date" class="field-input" type="date" />
            <p class="text-xs" :class="`text-${trust('letter_date').tone}`">
              {{ trust('letter_date').text }}
              <template v-if="fieldsByName.letter_date?.quote"> — “{{ fieldsByName.letter_date.quote }}”</template>
            </p>
          </FormField>
          <FormField label="Date received" for-id="n-received" class="grow" :error="fieldErrors.received_date">
            <input id="n-received" v-model="form.received_date" class="field-input" type="date" />
            <p class="text-xs" :class="`text-${trust('received_date').tone}`">{{ trust('received_date').text }}</p>
          </FormField>
        </div>
        <div v-if="reading.dates_on_page.length" class="intake__dates">
          <span class="text-xs text-muted">Dates on the page:</span>
          <button
            v-for="item in reading.dates_on_page.filter((d) => d.value)"
            :key="item.text"
            type="button"
            class="intake__chip"
            :class="{ 'is-active': form.letter_date === item.value }"
            :title="`Use ${item.text} as the date on the letter`"
            @click="form.letter_date = item.value"
          >
            {{ item.text }}
          </button>
        </div>
      </section>

      <section class="intake__group">
        <p class="section-title">What it is</p>
        <div class="row gap-3 wrap">
          <FormField label="Type" for-id="n-kind" class="grow">
            <select id="n-kind" v-model="form.notice_kind" class="field-input">
              <option v-for="[code, label] in KINDS" :key="code" :value="code">{{ label }}</option>
            </select>
          </FormField>
          <FormField label="Given under clause" for-id="n-clause" class="grow" required :error="fieldErrors.clause_number">
            <input id="n-clause" v-model="form.clause_number" class="field-input" type="text" list="n-clause-options" placeholder="e.g. 44.2" />
            <datalist id="n-clause-options">
              <option v-for="clause in clauseOptions" :key="clause" :value="clause" />
            </datalist>
            <p v-if="clauseOptions.length" class="text-xs text-muted">Cited in the letter: {{ clauseOptions.join(', ') }}</p>
          </FormField>
        </div>
        <div class="row gap-3 wrap">
          <FormField label="Reference" for-id="n-ref" class="grow">
            <input id="n-ref" v-model="form.reference" class="field-input" type="text" />
          </FormField>
          <FormField label="Subject" for-id="n-subject" class="grow">
            <input id="n-subject" v-model="form.subject" class="field-input" type="text" />
          </FormField>
        </div>
        <div class="row gap-3 wrap">
          <FormField label="From" for-id="n-from" class="grow">
            <input id="n-from" v-model="form.sender" class="field-input" type="text" />
          </FormField>
          <FormField label="To" for-id="n-to" class="grow">
            <input id="n-to" v-model="form.recipient" class="field-input" type="text" />
          </FormField>
        </div>
        <FormField label="Event notified" for-id="n-event">
          <textarea id="n-event" v-model="form.event_description" class="field-input" rows="2" />
        </FormField>
      </section>

      <section class="intake__group">
        <p class="section-title">Which claim it belongs to</p>
        <div class="intake__targets" role="radiogroup" aria-label="Which claim">
          <label class="intake__choice">
            <input v-model="target" type="radio" value="existing" :disabled="!claims.length" />
            An existing claim
          </label>
          <label class="intake__choice">
            <input v-model="target" type="radio" value="new" />
            Start a new claim from this notice
          </label>
          <label class="intake__choice">
            <input v-model="target" type="radio" value="none" />
            Not yet — keep it on the notices register
          </label>
        </div>

        <FormField v-if="target === 'existing'" label="Claim" for-id="n-claim" required :error="fieldErrors.claim">
          <select id="n-claim" v-model="form.claim" class="field-input">
            <option value="" disabled>Choose a claim</option>
            <option v-for="claim in orderedClaims" :key="claim.id" :value="claim.id">
              {{ suggestedIds.has(claim.id) ? '★ ' : '' }}{{ claim.reference ? claim.reference + ' · ' : '' }}{{ claim.title }}
            </option>
          </select>
          <p v-for="hint in reading.suggested_claims.filter((c) => c.id === form.claim)" :key="hint.id" class="text-xs text-muted">
            Suggested because {{ hint.why }}.
          </p>
        </FormField>

        <template v-else-if="target === 'new'">
          <div class="row gap-3 wrap">
            <FormField label="Claim title" for-id="nc-title" class="grow" required :error="fieldErrors.title">
              <input id="nc-title" v-model="newClaim.title" class="field-input" type="text" />
            </FormField>
            <FormField label="Type" for-id="nc-type" class="grow">
              <select id="nc-type" v-model="newClaim.claim_type" class="field-input">
                <option v-for="[code, label] in CLAIM_TYPES" :key="code" :value="code">{{ label }}</option>
              </select>
            </FormField>
          </div>
          <div class="row gap-3 wrap">
            <FormField label="Date of the event" for-id="nc-event" class="grow">
              <input id="nc-event" v-model="newClaim.event_date" class="field-input" type="date" />
            </FormField>
            <FormField
              label="Date the contractor became aware"
              for-id="nc-aware"
              class="grow"
              hint="Notice deadlines run from this. Leave blank if not yet settled."
            >
              <input id="nc-aware" v-model="newClaim.awareness_date" class="field-input" type="date" />
            </FormField>
          </div>
        </template>

        <label class="intake__choice">
          <input v-model="form.confirmed" type="checkbox" />
          <span>
            I have checked this reading against the letter.
            <span class="text-xs text-muted">Untick if it is a letter that may or may not count as a contractual notice.</span>
          </span>
        </label>
      </section>

      <p class="text-xs text-muted">
        Read by {{ reading.model || 'pattern only (no model configured)' }} from {{ reading.pages_read }} page(s) of
        {{ stored?.title }}.
        <template v-if="form.letter_date"> Letter dated {{ formatDate(form.letter_date) }}.</template>
      </p>
    </div>

    <template #footer>
      <AppButton variant="ghost" label="Cancel" :disabled="step === 'reading' || saving" @click="emit('update:modelValue', false)" />
      <template v-if="step === 'choose'">
        <template v-if="duplicateOf">
          <AppButton variant="ghost" label="Upload anyway" @click="read({ allowDuplicate: true })" />
          <AppButton variant="primary" label="Read the copy already uploaded" @click="read({ existing: duplicateOf.document_id })" />
        </template>
        <AppButton v-else variant="primary" icon="pi pi-sparkles" label="Upload and read" :disabled="!file" @click="read()" />
      </template>
      <AppButton
        v-else-if="step === 'review'"
        variant="primary"
        icon="pi pi-check"
        label="Save notice"
        :loading="saving"
        :disabled="!canSave"
        @click="save"
      />
    </template>
  </AppDialog>
</template>

<style scoped>
.intake__reading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-6) 0;
  text-align: center;
}

.intake__reading .pi {
  font-size: 24px;
  color: var(--color-accent);
}

.intake__group {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-subtle);
}

.intake__group:first-of-type {
  border-top: none;
  padding-top: 0;
}

.intake__notes {
  padding: var(--space-2) var(--space-3);
  color: var(--color-info);
  background: var(--color-info-subtle);
  border-radius: var(--radius-sm);
}

.intake__warning {
  padding: var(--space-2) var(--space-3);
  color: var(--color-warning);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-sm);
}

.intake__dates {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.intake__chip {
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
}

.intake__chip.is-active {
  color: var(--color-accent);
  border-color: var(--color-accent);
}

.intake__targets {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
}

.intake__choice {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.intake__choice input {
  margin-top: 3px;
}

.text-success {
  color: var(--color-success);
}

.text-info {
  color: var(--color-info);
}
</style>
