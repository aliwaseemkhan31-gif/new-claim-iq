<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { useRouter } from 'vue-router'

import * as documentsApi from '@/api/documents'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import UploadDocumentDialog from '@/components/domain/UploadDocumentDialog.vue'
import { usePolling } from '@/composables/usePolling'
import { useAuthStore } from '@/stores/auth'
import { EM_DASH, formatBytes, formatDate, formatRelative } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * The document corpus, for one project or across every project the user can
 * read. Rows show where a document is in the pipeline, because a document that
 * has not finished processing cannot be retrieved and should not be assumed
 * to be part of any answer.
 */
const props = defineProps({
  projectId: { type: String, default: null },
  showProject: { type: Boolean, default: false },
})

const router = useRouter()
const auth = useAuthStore()

const query = ref('')
const typeFilter = ref('')
const rows = ref([])
const taxonomy = ref([])
const loading = ref(true)
const error = ref(null)
const uploadOpen = ref(false)

const canUpload = computed(
  () => Boolean(props.projectId) && auth.canInProject(props.projectId, 'document.upload')
)

const inFlight = computed(() =>
  rows.value.some((row) =>
    ['pending', 'queued', 'processing'].includes(row.current_version?.processing_status)
  )
)

async function fetchRows() {
  const data = await documentsApi.listDocuments({
    project: props.projectId || undefined,
    search: query.value || undefined,
    document_type: typeFilter.value || undefined,
    page_size: 100,
  })
  return rowsOf(data)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    rows.value = await fetchRows()
    if (inFlight.value) polling.start({ immediate: false })
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

const polling = usePolling(fetchRows, {
  interval: 5000,
  isDone: (data) =>
    !data.some((row) =>
      ['pending', 'queued', 'processing'].includes(row.current_version?.processing_status)
    ),
  onData: (data) => {
    rows.value = data
  },
})

let debounce = null
watch([query, typeFilter], () => {
  clearTimeout(debounce)
  debounce = setTimeout(load, 250)
})

function open(document) {
  router.push({ name: 'document-viewer', params: { documentId: document.id } })
}

function quality(document) {
  const value = document.current_version?.extraction_quality
  return value == null ? EM_DASH : Math.round(value * 100) + '%'
}

onMounted(async () => {
  await load()
  try {
    const data = await documentsApi.fetchDocumentTaxonomy()
    taxonomy.value = (data.types ?? []).filter((type) => type.code !== 'standard_form')
  } catch {
    taxonomy.value = []
  }
})
</script>

<template>
  <div class="stack gap-4">
    <div class="toolbar between">
      <div class="toolbar">
        <input
          v-model="query"
          class="field-input documents__search"
          type="search"
          placeholder="Filter by title or reference"
          aria-label="Filter documents"
        />
        <select v-model="typeFilter" class="field-input documents__type" aria-label="Document type">
          <option value="">All types</option>
          <option v-for="type in taxonomy" :key="type.code" :value="type.code">
            {{ type.label }}
          </option>
        </select>
      </div>

      <div class="row gap-2">
        <AppButton
          size="sm"
          variant="ghost"
          icon="pi pi-refresh"
          label="Refresh"
          :loading="loading"
          @click="load"
        />
        <AppButton
          v-if="canUpload"
          variant="primary"
          icon="pi pi-upload"
          label="Upload"
          @click="uploadOpen = true"
        />
      </div>
    </div>

    <div class="surface documents__panel">
      <div v-if="loading" class="documents__pad">
        <LoadingSkeleton variant="table" :rows="6" :columns="5" />
      </div>

      <ErrorState
        v-else-if="error"
        :error="error"
        title="Could not load documents"
        @retry="load"
      />

      <EmptyState
        v-else-if="!rows.length && (query || typeFilter)"
        icon="pi pi-filter-slash"
        title="No documents match"
        description="Nothing here matches the current filter."
      />

      <EmptyState
        v-else-if="!rows.length"
        icon="pi pi-file"
        title="No documents yet"
        description="Upload the contract, correspondence, claims and site records. Each is extracted, OCR'd where needed, split by clause and indexed, so answers can cite a page."
        :action-label="canUpload ? 'Upload a document' : null"
        action-icon="pi pi-upload"
        :hint="canUpload ? null : 'Uploading requires the Upload documents permission on this project.'"
        @action="uploadOpen = true"
      />

      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Document</th>
              <th v-if="showProject" scope="col">Project</th>
              <th scope="col">Type</th>
              <th scope="col">Processing</th>
              <th scope="col" class="cell-numeric">Pages</th>
              <th scope="col" class="cell-numeric">Quality</th>
              <th scope="col">Added</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="document in rows"
              :key="document.id"
              class="documents__row"
              tabindex="0"
              @click="open(document)"
              @keydown.enter="open(document)"
            >
              <td>
                <p class="documents__title">{{ document.title }}</p>
                <p class="documents__sub">
                  <span v-if="document.reference" class="text-mono">{{ document.reference }}</span>
                  <span v-if="document.current_version">
                    {{ formatBytes(document.current_version.file_size_bytes) }}
                  </span>
                  <span v-if="document.document_date">
                    · {{ formatDate(document.document_date) }}
                  </span>
                </p>
              </td>
              <td v-if="showProject" class="text-xs">{{ document.project_name || EM_DASH }}</td>
              <td>{{ document.document_type_label }}</td>
              <td>
                <StatusBadge
                  :status="document.current_version?.processing_status || 'pending'"
                  size="sm"
                />
                <p
                  v-if="document.current_version?.processing_error"
                  class="documents__error"
                  :title="document.current_version.processing_error"
                >
                  {{ document.current_version.processing_error }}
                </p>
              </td>
              <td class="cell-numeric">{{ document.current_version?.page_count ?? EM_DASH }}</td>
              <td class="cell-numeric">{{ quality(document) }}</td>
              <td>{{ formatRelative(document.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <UploadDocumentDialog
      v-if="projectId"
      v-model="uploadOpen"
      :project-id="projectId"
      @uploaded="load"
    />
  </div>
</template>

<style scoped>
.documents__panel {
  overflow: hidden;
}

.documents__pad {
  padding: var(--space-4);
}

.documents__search {
  width: 280px;
}

.documents__type {
  width: 200px;
}

.documents__row {
  cursor: pointer;
}

.documents__title {
  font-weight: var(--weight-medium);
}

.documents__sub {
  display: flex;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.documents__error {
  max-width: 260px;
  font-size: var(--text-2xs);
  color: var(--color-danger);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
