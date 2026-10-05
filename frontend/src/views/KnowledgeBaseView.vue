<script setup>
import { computed, onMounted, ref } from 'vue'

import * as knowledgeApi from '@/api/knowledge'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useUiStore } from '@/stores/ui'
import { formatBytes, formatDateTime } from '@/utils/format'

/**
 * The standard forms this installation can cite.
 *
 * One knowledge base per edition, per organization. An edition becomes
 * retrievable only when someone publishes it: a built, validated edition waits
 * at "ready" until then.
 */
const ui = useUiStore()

const editions = ref([])
const bases = ref([])
const canManage = ref(false)
const loading = ref(true)
const error = ref(null)

const uploadOpen = ref(false)
const uploadEdition = ref('')
const uploadFile = ref(null)
const uploadName = ref('')
const uploadProgress = ref(0)
const uploading = ref(false)
const fieldErrors = ref({})

const basesByEdition = computed(() => {
  const map = new Map()
  for (const base of bases.value) map.set(base.edition_code, base)
  return map
})

const missingEditions = computed(() =>
  editions.value.filter((edition) => !basesByEdition.value.has(edition.code))
)

async function load() {
  loading.value = true
  error.value = null
  try {
    const [editionData, baseData] = await Promise.all([
      knowledgeApi.listEditions(),
      knowledgeApi.listKnowledgeBases(),
    ])
    editions.value = editionData.editions ?? []
    bases.value = baseData.results ?? []
    canManage.value = Boolean(baseData.can_manage)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

function openUpload(editionCode) {
  uploadEdition.value = editionCode || ''
  uploadFile.value = null
  uploadName.value = ''
  uploadProgress.value = 0
  fieldErrors.value = {}
  uploadOpen.value = true
}

function onFile(event) {
  uploadFile.value = event.target.files?.[0] ?? null
}

/**
 * The edition this exact file is already registered as, when the server has
 * refused it.
 *
 * One file cannot be two editions, and answers are grounded in whichever
 * edition a project declares — so a form filed under the wrong one yields
 * confident answers from the wrong contract (ADR 0004).
 */
const duplicateEdition = ref(null)

async function submitUpload() {
  if (!uploadFile.value || !uploadEdition.value || uploading.value) return
  uploading.value = true
  fieldErrors.value = {}
  duplicateEdition.value = null
  try {
    await knowledgeApi.createKnowledgeBase({
      file: uploadFile.value,
      editionCode: uploadEdition.value,
      name: uploadName.value,
      onProgress: (value) => {
        uploadProgress.value = value
      },
    })
    ui.notifySuccess(
      'Upload started',
      'The form is being processed. It becomes retrievable only after validation and publication.'
    )
    uploadOpen.value = false
    await load()
  } catch (err) {
    if (err?.details?.reason === 'duplicate_source_under_another_edition') {
      // Stated in the dialog rather than as a toast: the next step is to check
      // which edition the file actually is, and a toast disappears while the
      // person is still reading it.
      duplicateEdition.value = err.details
      return
    }
    const field = err?.details?.field
    fieldErrors.value = field ? { [field]: err.message } : {}
    ui.notifyError(err, 'Could not start the knowledge base')
  } finally {
    uploading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader
      title="Knowledge base"
      description="Standard forms of contract, ingested once and shared across the organization. Retrieval is scoped to the edition a project declares; editions are never mixed."
    >
      <template #actions>
        <AppButton
          v-if="canManage"
          variant="primary"
          icon="pi pi-upload"
          label="Add a standard form"
          @click="openUpload('')"
        />
      </template>
    </PageHeader>

    <div v-if="loading" class="surface kb__pad"><LoadingSkeleton variant="table" :rows="4" /></div>
    <ErrorState v-else-if="error" :error="error" title="Could not load knowledge bases" @retry="load" />

    <template v-else>
      <EmptyState
        v-if="!bases.length"
        icon="pi pi-book"
        title="No standard form has been ingested yet"
        description="Upload a published edition. Until one is published, AI answers and analysis have no standard-form text to cite, and they say so rather than guessing."
        :hint="canManage ? null : 'Adding a standard form requires the Manage knowledge bases permission.'"
      />

      <div v-else class="grid-auto">
        <article v-for="base in bases" :key="base.id" class="surface kb__card">
          <div class="row between gap-2">
            <RouterLink
              :to="{ name: 'knowledge-base-detail', params: { knowledgeBaseId: base.id } }"
              class="kb__name"
            >
              {{ base.edition_label }}
            </RouterLink>
            <StatusBadge :status="base.status" size="sm" />
          </div>

          <p class="text-xs text-muted">{{ base.name }}</p>

          <dl class="kb__facts">
            <div>
              <dt>Passages</dt>
              <dd>{{ base.retained_chunks ?? base.chunk_count }} retained</dd>
            </div>
            <div>
              <dt>Quarantined</dt>
              <dd>{{ base.quarantined_chunks ?? 0 }}</dd>
            </div>
            <div>
              <dt>Embedded</dt>
              <dd>{{ base.embedded_chunks ?? 0 }}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd>
                {{ base.source?.page_count ?? 0 }} page(s)
                <template v-if="base.source?.file_size_bytes">
                  · {{ formatBytes(base.source.file_size_bytes) }}
                </template>
              </dd>
            </div>
          </dl>

          <p v-if="base.build_error" class="kb__error">{{ base.build_error }}</p>
          <p v-else-if="base.published_at" class="text-xs text-muted">
            Published {{ formatDateTime(base.published_at) }}
            <template v-if="base.published_by">by {{ base.published_by }}</template>
          </p>
          <p v-else-if="base.status === 'ready'" class="text-xs text-warning">
            Validated and waiting to be published. Not retrievable yet.
          </p>
        </article>
      </div>

      <section v-if="missingEditions.length" class="surface kb__missing">
        <p class="section-title">Editions with no ingested text</p>
        <p class="text-xs text-muted">
          A project may declare any of these, but questions about them cannot cite standard-form
          text until that edition is ingested and published.
        </p>
        <ul class="kb__missing-list">
          <li v-for="edition in missingEditions" :key="edition.code">
            <span>{{ edition.label }}</span>
            <AppButton
              v-if="canManage"
              size="sm"
              variant="ghost"
              icon="pi pi-plus"
              label="Ingest"
              @click="openUpload(edition.code)"
            />
          </li>
        </ul>
      </section>
    </template>

    <AppDialog
      v-model="uploadOpen"
      title="Add a standard form"
      description="The file runs through the same pipeline as any document - extraction, OCR where needed, clause detection, chunking and embeddings - then validation for contents pages and guidance notes before it can be published."
      :busy="uploading"
    >
      <div class="stack gap-4">
        <FormField label="Edition" for-id="kb-edition" required :error="fieldErrors.edition_code">
          <select id="kb-edition" v-model="uploadEdition" class="field-input">
            <option value="" disabled>Choose the edition this file is</option>
            <option
              v-for="edition in editions"
              :key="edition.code"
              :value="edition.code"
              :disabled="basesByEdition.has(edition.code)"
            >
              {{ edition.label }}{{ basesByEdition.has(edition.code) ? ' - already ingested' : '' }}
            </option>
          </select>
        </FormField>

        <FormField
          label="File"
          for-id="kb-file"
          required
          :error="fieldErrors.file"
          hint="The published conditions of contract, as a PDF."
        >
          <input id="kb-file" class="field-input" type="file" accept="application/pdf" @change="onFile" />
          <p v-if="uploadFile" class="text-xs text-muted">
            {{ uploadFile.name }} · {{ formatBytes(uploadFile.size) }}
          </p>
        </FormField>

        <FormField label="Name" for-id="kb-name" hint="Defaults to the edition's name.">
          <input id="kb-name" v-model="uploadName" class="field-input" type="text" />
        </FormField>

        <p v-if="duplicateEdition" class="kb__duplicate">
          This exact file is already held as
          <strong>{{ duplicateEdition.edition_label }}</strong>. One file cannot be two
          editions: a project is answered from the edition it declares, so a form filed
          under the wrong one produces confident answers from the wrong contract. Check
          which edition this file is — or replace the source of the existing entry
          instead of adding another.
        </p>

        <div v-if="uploading" class="kb__progress">
          <div class="kb__bar"><div class="kb__fill" :style="{ width: uploadProgress + '%' }" /></div>
          <p class="text-xs text-muted">Uploading {{ uploadProgress }}%</p>
        </div>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="uploading" @click="uploadOpen = false" />
        <AppButton
          variant="primary"
          label="Upload and process"
          :loading="uploading"
          :disabled="!uploadFile || !uploadEdition"
          @click="submitUpload"
        />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.kb__pad {
  padding: var(--space-4);
}

.kb__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
}

.kb__name {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.kb__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2);
  margin: 0;
}

.kb__facts dt {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.kb__facts dd {
  margin: 0;
  font-size: var(--text-sm);
}

.kb__duplicate {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-warning);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-sm);
}

.kb__error {
  font-size: var(--text-xs);
  color: var(--color-danger);
}

.kb__missing {
  padding: var(--space-4);
}

.kb__missing-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: var(--space-3) 0 0;
  padding: 0;
  list-style: none;
  font-size: var(--text-sm);
}

.kb__missing-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  border-bottom: 1px solid var(--color-border-subtle);
}

.kb__progress {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.kb__bar {
  height: 4px;
  background: var(--color-surface-sunken);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.kb__fill {
  height: 100%;
  background: var(--color-accent);
  transition: width 0.2s ease;
}
</style>
