<script setup>
import { computed, ref, watch } from 'vue'

import { useRouter } from 'vue-router'

import * as documentsApi from '@/api/documents'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import FormField from '@/components/common/FormField.vue'
import { useUiStore } from '@/stores/ui'
import { formatBytes } from '@/utils/format'

/**
 * Upload one document into a project.
 *
 * The document type is chosen from the server's taxonomy, not typed: the type
 * decides whether clause detection runs and how retrieval treats the document.
 * Standard forms are excluded — they belong to the knowledge base, which is
 * organization-wide and edition-scoped.
 */
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  projectId: { type: String, required: true },
})

const emit = defineEmits(['update:modelValue', 'uploaded'])

const ui = useUiStore()
const router = useRouter()

const file = ref(null)
const documentType = ref('')
const title = ref('')
const reference = ref('')
const documentDate = ref('')
const progress = ref(0)
const busy = ref(false)
const fieldErrors = ref({})
const taxonomy = ref([])

const grouped = computed(() => {
  const groups = new Map()
  for (const type of taxonomy.value) {
    if (type.code === 'standard_form') continue
    if (!groups.has(type.category)) groups.set(type.category, [])
    groups.get(type.category).push(type)
  }
  return [...groups.entries()].map(([category, types]) => ({ category, types }))
})

const canSubmit = computed(() => Boolean(file.value && documentType.value) && !busy.value)

async function loadTaxonomy() {
  if (taxonomy.value.length) return
  try {
    const data = await documentsApi.fetchDocumentTaxonomy()
    taxonomy.value = data.types ?? []
  } catch (error) {
    ui.notifyError(error, 'Could not load document types')
  }
}

function onFile(event) {
  file.value = event.target.files?.[0] ?? null
  if (file.value && !title.value) title.value = file.value.name.replace(/\.[^.]+$/, '')
}

function reset() {
  file.value = null
  documentType.value = ''
  title.value = ''
  reference.value = ''
  documentDate.value = ''
  progress.value = 0
  fieldErrors.value = {}
}

watch(
  () => props.modelValue,
  (open) => {
    if (open) loadTaxonomy()
    else reset()
  }
)

// A different file is a different question.
watch(file, () => {
  duplicateOf.value = null
})

/**
 * The document already holding these bytes, when the server has refused the
 * upload as a duplicate.
 *
 * The same standard form uploaded twice is indexed twice and comes back twice
 * in every result set, so the server stops it and names the existing copy.
 * Keeping both is still allowed — it is a question for the person, not a rule.
 */
const duplicateOf = ref(null)

async function submit({ allowDuplicate = false } = {}) {
  if (!canSubmit.value) return
  busy.value = true
  fieldErrors.value = {}
  if (!allowDuplicate) duplicateOf.value = null
  try {
    const result = await documentsApi.uploadDocument({
      projectId: props.projectId,
      file: file.value,
      documentType: documentType.value,
      title: title.value,
      reference: reference.value,
      documentDate: documentDate.value || undefined,
      allowDuplicate,
      onProgress: (value) => {
        progress.value = value
      },
    })
    emit('uploaded', result)
    if (result.queued === false) {
      ui.notify({
        severity: 'warn',
        summary: 'Uploaded, but not queued',
        detail: 'The file is stored. Processing did not start; retry it from the document.',
      })
    } else {
      ui.notifySuccess('Upload started', 'Processing runs in the background.')
    }
    emit('update:modelValue', false)
  } catch (error) {
    if (error?.details?.reason === 'duplicate_of_another_document') {
      // Not an error to dismiss: the next step is a decision, so the dialog
      // stays open and states what is already there.
      duplicateOf.value = error.details
      return
    }
    const field = error?.details?.field
    fieldErrors.value = field ? { [field]: error.message } : {}
    ui.notifyError(error, 'Upload failed')
  } finally {
    busy.value = false
  }
}

function openExisting() {
  emit('update:modelValue', false)
  router.push({
    name: 'document-viewer',
    params: { documentId: duplicateOf.value.document_id },
  })
}
</script>

<template>
  <AppDialog
    :model-value="modelValue"
    title="Upload a document"
    description="Contract documents, correspondence, claims and site records. Processing — extraction, OCR where needed, clause detection and indexing — runs in the background."
    :busy="busy"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="stack gap-4">
      <FormField label="File" for-id="upload-file" required :error="fieldErrors.file">
        <input id="upload-file" class="field-input" type="file" @change="onFile" />
        <p v-if="file" class="text-xs text-muted">{{ file.name }} · {{ formatBytes(file.size) }}</p>
      </FormField>

      <FormField
        label="Document type"
        for-id="upload-type"
        required
        :error="fieldErrors.document_type"
        hint="Decides whether clause detection runs and how the document is retrieved."
      >
        <select id="upload-type" v-model="documentType" class="field-input">
          <option value="" disabled>Choose a type</option>
          <optgroup v-for="group in grouped" :key="group.category" :label="group.category">
            <option v-for="type in group.types" :key="type.code" :value="type.code">
              {{ type.label }}
            </option>
          </optgroup>
        </select>
      </FormField>

      <FormField label="Title" for-id="upload-title" :error="fieldErrors.title">
        <input id="upload-title" v-model="title" class="field-input" type="text" />
      </FormField>

      <div class="row gap-3">
        <FormField label="Reference" for-id="upload-reference" class="grow">
          <input id="upload-reference" v-model="reference" class="field-input" type="text" />
        </FormField>
        <FormField
          label="Document date"
          for-id="upload-date"
          class="grow"
          hint="The date on the document, not today."
        >
          <input id="upload-date" v-model="documentDate" class="field-input" type="date" />
        </FormField>
      </div>

      <div v-if="duplicateOf" class="upload__duplicate">
        <p class="text-sm">
          This file is already in this project, uploaded as
          <strong>{{ duplicateOf.document_title }}</strong>.
        </p>
        <p class="text-xs text-muted">
          Uploading it again indexes the same text twice, so it is returned twice in
          search and cited twice in an answer. Open the existing document, or keep both
          if they are genuinely different records.
        </p>
      </div>

      <div v-if="busy" class="upload__progress">
        <div class="upload__bar"><div class="upload__fill" :style="{ width: progress + '%' }" /></div>
        <p class="text-xs text-muted">Uploading {{ progress }}%</p>
      </div>
    </div>

    <template #footer>
      <AppButton variant="ghost" label="Cancel" :disabled="busy" @click="emit('update:modelValue', false)" />
      <template v-if="duplicateOf">
        <AppButton
          variant="ghost"
          icon="pi pi-external-link"
          label="Open the existing one"
          :disabled="busy"
          @click="openExisting"
        />
        <AppButton
          variant="primary"
          label="Upload anyway"
          :loading="busy"
          :disabled="!canSubmit"
          @click="submit({ allowDuplicate: true })"
        />
      </template>
      <AppButton
        v-else
        variant="primary"
        label="Upload"
        :loading="busy"
        :disabled="!canSubmit"
        @click="submit()"
      />
    </template>
  </AppDialog>
</template>

<style scoped>
.upload__duplicate {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2) var(--space-3);
  color: var(--color-warning);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-sm);
}

.upload__progress {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.upload__bar {
  height: 4px;
  background: var(--color-surface-sunken);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.upload__fill {
  height: 100%;
  background: var(--color-accent);
  transition: width 0.2s ease;
}
</style>
