<script setup>
import { computed, ref, watch } from 'vue'

import * as claimsApi from '@/api/claims'
import * as documentsApi from '@/api/documents'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import FormField from '@/components/common/FormField.vue'
import { useUiStore } from '@/stores/ui'
import { formatBytes } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * File a document on a claim: the claim itself, a notice, or support.
 *
 * Asks only what makes the document count. A notice is useless to the
 * deadline check without the date it was sent and the provision it was given
 * under; a supporting document counts for nothing until it is tied to the
 * element it proves. Those are the required fields, and nothing else is.
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  claimId: { type: String, required: true },
  projectId: { type: String, required: true },
  /** `claim_submission`, `notice` or `supporting`. */
  role: { type: String, required: true },
  /** Deadline findings for this claim, from the notice-compliance endpoint. */
  findings: { type: Array, default: () => [] },
  /** Evidence elements for this claim, from the evidence-gaps endpoint. */
  elements: { type: Array, default: () => [] },
  /** Preselects what a supporting document proves. */
  element: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue', 'attached'])

const ui = useUiStore()

const COPY = {
  claim_submission: {
    title: 'Add the claim document',
    description:
      'The submitted claim: the detailed particulars of the time and money claimed. Its date is checked against the deadline for the detailed claim.',
    category: 'claim',
    defaultType: 'claim',
  },
  notice: {
    title: 'Add a notice',
    description:
      'A notice given under the contract. The date it was sent, and the clause it was given under, are what the deadline check reads.',
    category: 'correspondence',
    defaultType: 'notice',
  },
  supporting: {
    title: 'Add a supporting document',
    description:
      'A record that proves part of the claim: a site diary, programme, invoice, photograph. Say what it proves so it counts towards that element.',
    category: 'evidence',
    defaultType: 'site_record',
  },
}

const OBLIGATIONS = [
  ['notice_of_claim', 'The notice of claim'],
  ['detailed_claim', 'The detailed particulars / account'],
]

const RELEVANCE = [
  ['supports', 'Supports the claim'],
  ['contradicts', 'Contradicts the claim'],
  ['neutral', 'Neutral'],
]

const copy = computed(() => COPY[props.role] ?? COPY.supporting)

const source = ref('upload')
const file = ref(null)
const existing = ref('')
const documentType = ref('')
const title = ref('')
const sentDate = ref('')
const receivedDate = ref('')
const obligation = ref('notice_of_claim')
const clause = ref('')
const otherClause = ref('')
const confirmed = ref(true)
const elementCode = ref('')
const relevance = ref('supports')
const note = ref('')
const progress = ref(0)
const busy = ref(false)
const fieldErrors = ref({})
const duplicateOf = ref(null)

const taxonomy = ref([])
const documents = ref([])

const isDated = computed(() => props.role !== 'supporting')

/** The provisions this document could be given under, for this claim. */
const clauseOptions = computed(() => {
  const wanted = props.role === 'claim_submission' ? 'detailed_claim' : obligation.value
  const seen = new Set()
  const options = []
  for (const finding of props.findings) {
    if (finding.obligation !== wanted) continue
    const key = finding.clause_number
    if (seen.has(key)) continue
    seen.add(key)
    options.push({ value: key, label: `${key} — ${finding.title}` })
  }
  return options
})

const effectiveClause = computed(() =>
  clause.value === '__other' ? otherClause.value.trim() : clause.value,
)

const typeOptions = computed(() =>
  taxonomy.value.filter((type) => type.category === copy.value.category),
)

const canSubmit = computed(() => {
  if (busy.value) return false
  if (source.value === 'upload' && !file.value) return false
  if (source.value === 'existing' && !existing.value) return false
  if (isDated.value && (!sentDate.value || !effectiveClause.value)) return false
  if (props.role === 'supporting' && !elementCode.value) return false
  return true
})

async function loadReferenceData() {
  try {
    if (!taxonomy.value.length) {
      const data = await documentsApi.fetchDocumentTaxonomy()
      taxonomy.value = data.types ?? []
    }
  } catch {
    taxonomy.value = []
  }
  try {
    documents.value = rowsOf(
      await documentsApi.listDocuments({ project: props.projectId, page_size: 200, ordering: 'title' }),
    )
  } catch {
    documents.value = []
  }
}

function reset() {
  source.value = 'upload'
  file.value = null
  existing.value = ''
  documentType.value = copy.value.defaultType
  title.value = ''
  sentDate.value = ''
  receivedDate.value = ''
  obligation.value = 'notice_of_claim'
  clause.value = ''
  otherClause.value = ''
  confirmed.value = true
  elementCode.value = props.element || ''
  relevance.value = 'supports'
  note.value = ''
  progress.value = 0
  fieldErrors.value = {}
  duplicateOf.value = null
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    reset()
    loadReferenceData()
  },
)

// The only provision on offer is the right default; a choice of two is a
// question for the person.
watch(
  clauseOptions,
  (options) => {
    if (!options.some((option) => option.value === clause.value)) {
      clause.value = options.length === 1 ? options[0].value : ''
    }
  },
  { immediate: true },
)

function onFile(event) {
  file.value = event.target.files?.[0] ?? null
  duplicateOf.value = null
  if (file.value && !title.value) title.value = file.value.name.replace(/\.[^.]+$/, '')
}

async function submit({ allowDuplicate = false } = {}) {
  if (!canSubmit.value) return
  busy.value = true
  fieldErrors.value = {}
  try {
    const fields = {
      role: props.role,
      sent_date: isDated.value ? sentDate.value : undefined,
      received_date: isDated.value ? receivedDate.value : undefined,
      clause_number: isDated.value ? effectiveClause.value : undefined,
      obligation: props.role === 'notice' ? obligation.value : undefined,
      confirmed: isDated.value ? confirmed.value : undefined,
      element: props.role === 'supporting' ? elementCode.value : undefined,
      relevance: props.role === 'supporting' ? relevance.value : undefined,
      note: note.value.trim(),
    }
    if (source.value === 'upload') {
      Object.assign(fields, {
        file: file.value,
        document_type: documentType.value || copy.value.defaultType,
        title: title.value.trim(),
        allow_duplicate: allowDuplicate || undefined,
      })
    } else {
      fields.document = existing.value
    }
    const result = await claimsApi.attachClaimFile(props.claimId, fields, {
      onProgress: (value) => {
        progress.value = value
      },
    })
    ui.notifySuccess('Document added', 'The checklist and deadlines have been recomputed.')
    emit('attached', result)
    emit('update:modelValue', false)
  } catch (error) {
    if (error?.details?.reason === 'duplicate_of_another_document') {
      duplicateOf.value = error.details
      return
    }
    const field = error?.details?.field
    fieldErrors.value = field ? { [field]: error.message } : {}
    if (!field) ui.notifyError(error, 'Could not add the document')
  } finally {
    busy.value = false
  }
}

/** The file is already in the project: file that copy instead of a second one. */
function useExisting() {
  existing.value = duplicateOf.value.document_id
  source.value = 'existing'
  duplicateOf.value = null
}
</script>

<template>
  <AppDialog
    :model-value="modelValue"
    :title="copy.title"
    :description="copy.description"
    size="lg"
    :busy="busy"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="stack gap-4">
      <div class="attach__source" role="radiogroup" aria-label="Where the document comes from">
        <label class="attach__choice">
          <input v-model="source" type="radio" value="upload" />
          Upload a file
        </label>
        <label class="attach__choice">
          <input v-model="source" type="radio" value="existing" />
          Use a document already in the project
        </label>
      </div>

      <template v-if="source === 'upload'">
        <FormField label="File" for-id="attach-file" required :error="fieldErrors.file">
          <input id="attach-file" class="field-input" type="file" @change="onFile" />
          <p v-if="file" class="text-xs text-muted">{{ file.name }} · {{ formatBytes(file.size) }}</p>
        </FormField>
        <div class="row gap-3 wrap">
          <FormField label="Title" for-id="attach-title" class="grow" :error="fieldErrors.title">
            <input id="attach-title" v-model="title" class="field-input" type="text" />
          </FormField>
          <FormField
            label="Document type"
            for-id="attach-type"
            class="grow"
            :error="fieldErrors.document_type"
          >
            <select id="attach-type" v-model="documentType" class="field-input">
              <option v-for="type in typeOptions" :key="type.code" :value="type.code">
                {{ type.label }}
              </option>
            </select>
          </FormField>
        </div>
      </template>

      <FormField
        v-else
        label="Document"
        for-id="attach-existing"
        required
        :error="fieldErrors.document"
      >
        <select id="attach-existing" v-model="existing" class="field-input">
          <option value="" disabled>Choose a document</option>
          <option v-for="document in documents" :key="document.id" :value="document.id">
            {{ document.title }} ({{ document.document_type_label || document.document_type }})
          </option>
        </select>
      </FormField>

      <!-- Notice and claim: what the deadline check needs -->
      <template v-if="isDated">
        <FormField
          v-if="role === 'notice'"
          label="What this is"
          for-id="attach-obligation"
          :error="fieldErrors.obligation"
          hint="Under FIDIC 1987 Sub-Clause 44.2, and 1999 Sub-Clause 20.1, one clause asks for both, so say which."
        >
          <select id="attach-obligation" v-model="obligation" class="field-input">
            <option v-for="[value, label] in OBLIGATIONS" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </FormField>

        <FormField
          label="Given under clause"
          for-id="attach-clause"
          required
          :error="fieldErrors.clause_number"
          hint="The provision of this contract it answers. Only the deadlines that govern this claim are offered."
        >
          <select id="attach-clause" v-model="clause" class="field-input">
            <option value="" disabled>Choose the clause</option>
            <option v-for="option in clauseOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
            <option value="__other">Another clause…</option>
          </select>
          <input
            v-if="clause === '__other'"
            v-model="otherClause"
            class="field-input attach__other"
            type="text"
            placeholder="For example 52.2"
            aria-label="Clause number"
          />
        </FormField>

        <div class="row gap-3 wrap">
          <FormField
            label="Date sent"
            for-id="attach-sent"
            class="grow"
            required
            :error="fieldErrors.sent_date"
            hint="The date on the letter."
          >
            <input id="attach-sent" v-model="sentDate" class="field-input" type="date" />
          </FormField>
          <FormField
            label="Date received"
            for-id="attach-received"
            class="grow"
            :error="fieldErrors.received_date"
            hint="If known. Most notice provisions turn on receipt."
          >
            <input id="attach-received" v-model="receivedDate" class="field-input" type="date" />
          </FormField>
        </div>

        <label class="attach__confirm">
          <input v-model="confirmed" type="checkbox" />
          <span>
            I have checked this document and it is given under this clause.
            <span class="text-xs text-muted">
              Leave unticked if it is a letter that may or may not count as a contractual notice.
            </span>
          </span>
        </label>
      </template>

      <!-- Support: what it proves -->
      <template v-else>
        <div class="row gap-3 wrap">
          <FormField
            label="What it proves"
            for-id="attach-element"
            class="grow"
            required
            :error="fieldErrors.element"
          >
            <select id="attach-element" v-model="elementCode" class="field-input">
              <option value="" disabled>Choose what it proves</option>
              <option v-for="item in elements" :key="item.code" :value="item.code">
                {{ item.label }}{{ item.is_essential ? '' : ' (optional)' }}
              </option>
            </select>
          </FormField>
          <FormField label="It" for-id="attach-relevance" class="grow">
            <select id="attach-relevance" v-model="relevance" class="field-input">
              <option v-for="[value, label] in RELEVANCE" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </FormField>
        </div>
      </template>

      <FormField label="Note" for-id="attach-note">
        <textarea id="attach-note" v-model="note" class="field-input" rows="2" />
      </FormField>

      <div v-if="duplicateOf" class="attach__duplicate">
        <p class="text-sm">
          This file is already in the project as <strong>{{ duplicateOf.document_title }}</strong>.
        </p>
        <p class="text-xs text-muted">
          Use that copy rather than indexing the same text twice, unless they are genuinely
          different records.
        </p>
      </div>

      <div v-if="busy && source === 'upload'" class="attach__progress">
        <div class="attach__bar"><div class="attach__fill" :style="{ width: progress + '%' }" /></div>
        <p class="text-xs text-muted">
          {{ progress < 100 ? `Uploading ${progress}%` : 'Reading the document — this can take a minute for a scan' }}
        </p>
      </div>
    </div>

    <template #footer>
      <AppButton variant="ghost" label="Cancel" :disabled="busy" @click="emit('update:modelValue', false)" />
      <template v-if="duplicateOf">
        <AppButton variant="ghost" label="Upload anyway" :disabled="busy" @click="submit({ allowDuplicate: true })" />
        <AppButton variant="primary" label="Use the existing copy" @click="useExisting" />
      </template>
      <AppButton
        v-else
        variant="primary"
        label="Add document"
        :loading="busy"
        :disabled="!canSubmit"
        @click="submit()"
      />
    </template>
  </AppDialog>
</template>

<style scoped>
.attach__source {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  font-size: var(--text-sm);
}

.attach__choice,
.attach__confirm {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.attach__confirm input,
.attach__choice input {
  margin-top: 3px;
}

.attach__other {
  margin-top: var(--space-2);
}

.attach__duplicate {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2) var(--space-3);
  color: var(--color-warning);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-sm);
}

.attach__progress {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.attach__bar {
  height: 4px;
  background: var(--color-surface-sunken);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.attach__fill {
  height: 100%;
  background: var(--color-accent);
  transition: width 0.2s ease;
}
</style>
