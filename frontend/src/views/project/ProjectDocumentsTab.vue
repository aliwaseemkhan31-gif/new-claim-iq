<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import * as documentsApi from '@/api/documents'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useAuthStore } from '@/stores/auth'
import { EM_DASH, formatBytes, formatRelative } from '@/utils/format'

const props = defineProps({
  project: { type: Object, required: true },
})

const auth = useAuthStore()
const query = ref('')

const request = useAsyncData(({ signal }) =>
  documentsApi.listDocuments(
    { project: props.project.id, search: query.value || undefined, page_size: 50 },
    { signal }
  )
)

const rows = computed(() => {
  const value = request.data.value
  if (!value) return []
  return Array.isArray(value) ? value : (value.results ?? [])
})

const canUpload = computed(() => auth.hasPermission('document.upload'))

let debounce = null
watch(query, () => {
  clearTimeout(debounce)
  debounce = setTimeout(() => request.execute(), 250)
})

onMounted(() => request.execute())
</script>

<template>
  <div class="page">
    <div class="toolbar between">
      <input
        v-model="query"
        class="field-input documents__search"
        type="search"
        placeholder="Filter documents"
        aria-label="Filter documents"
      />
      <AppButton
        v-if="canUpload"
        variant="primary"
        icon="pi pi-upload"
        label="Upload"
        disabled
        title="Upload requires the documents API"
      />
    </div>

    <div class="surface documents__panel">
      <div v-if="request.loading.value" class="documents__pad">
        <LoadingSkeleton variant="table" :rows="6" :columns="5" />
      </div>

      <ErrorState
        v-else-if="request.error.value"
        :error="request.error.value"
        title="Could not load documents"
        @retry="request.execute()"
      />

      <EmptyState
        v-else-if="request.isEmpty.value && query"
        icon="pi pi-filter-slash"
        title="No documents match"
        description="No document in this project matches that filter."
        secondary-action-label="Clear filter"
        @secondary-action="query = ''"
      />

      <EmptyState
        v-else-if="request.isEmpty.value"
        icon="pi pi-file"
        title="No documents in this project"
        description="Upload the contract, drawings, programme, site records and correspondence. ClaimIQ extracts text, classifies each document and indexes it for retrieval."
        :hint="canUpload ? null : 'Uploading requires the “Upload documents” permission.'"
      />

      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Type</th>
              <th scope="col">Status</th>
              <th scope="col" class="cell-numeric">Pages</th>
              <th scope="col" class="cell-numeric">Size</th>
              <th scope="col">Added</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="doc in rows" :key="doc.id">
              <td>
                <p class="documents__name truncate">{{ doc.title || doc.filename }}</p>
                <p v-if="doc.title && doc.filename" class="documents__sub truncate">
                  {{ doc.filename }}
                </p>
              </td>
              <td>{{ doc.document_type_label || doc.document_type || EM_DASH }}</td>
              <td><StatusBadge :status="doc.processing_status" size="sm" /></td>
              <td class="cell-numeric">{{ doc.page_count ?? EM_DASH }}</td>
              <td class="cell-numeric">{{ formatBytes(doc.size_bytes) }}</td>
              <td>{{ formatRelative(doc.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
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
  width: 320px;
  height: 32px;
}

.documents__name {
  font-weight: var(--weight-medium);
}

.documents__sub {
  margin-top: 1px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
