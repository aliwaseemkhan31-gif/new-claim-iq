<script setup>
import { computed, onMounted, ref } from 'vue'

import { useRoute, useRouter } from 'vue-router'

import * as documentsApi from '@/api/documents'
import AppButton from '@/components/common/AppButton.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageViewer from '@/components/domain/PageViewer.vue'
import ProcessingStages from '@/components/domain/ProcessingStages.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatDate } from '@/utils/format'
import { clampPage } from '@/utils/viewer'

/**
 * A project document, page by page. This is where a citation lands.
 */
const props = defineProps({
  documentId: { type: String, required: true },
})

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()

const document = ref(null)
const sections = ref({ clauses: [], tables: [] })
const processing = ref(null)
const loading = ref(true)
const error = ref(null)
const reprocessing = ref(false)
const showStages = ref(false)

const page = computed(() => clampPage(route.query.page, document.value?.current_version?.page_count))
const highlight = computed(() => (route.query.q ? String(route.query.q) : null))
const canReprocess = computed(() =>
  auth.canInProject(document.value?.project, 'document.reprocess')
)

function setPage(value) {
  router.replace({ query: { ...route.query, page: String(value) } })
}

function fetchPage(pageNumber) {
  return documentsApi.fetchDocumentPage(props.documentId, pageNumber)
}

function imageUrl(pageNumber, scale) {
  return documentsApi.documentPageImageUrl(props.documentId, pageNumber, scale)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    document.value = await documentsApi.fetchDocument(props.documentId)
    const [sectionData, processingData] = await Promise.all([
      documentsApi.fetchDocumentSections(props.documentId).catch(() => ({ clauses: [], tables: [] })),
      documentsApi.fetchDocumentProcessing(props.documentId).catch(() => null),
    ])
    sections.value = sectionData
    processing.value = processingData
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

async function reprocess() {
  reprocessing.value = true
  try {
    await documentsApi.reprocessDocument(props.documentId)
    ui.notifySuccess('Reprocessing started', 'The pipeline runs in the background.')
    processing.value = await documentsApi.fetchDocumentProcessing(props.documentId)
    showStages.value = true
  } catch (err) {
    ui.notifyError(err, 'Could not start reprocessing')
  } finally {
    reprocessing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page-tight viewer-page">
    <div v-if="loading" class="stack gap-4">
      <LoadingSkeleton variant="text" :rows="2" />
      <LoadingSkeleton variant="block" height="480px" />
    </div>

    <ErrorState v-else-if="error" :error="error" title="Could not open this document" @retry="load" />

    <template v-else-if="document">
      <header class="row between gap-3 wrap">
        <div>
          <div class="row gap-2 wrap">
            <AppButton
              size="sm"
              variant="ghost"
              icon="pi pi-arrow-left"
              label="Documents"
              :to="{ name: 'project-documents', params: { projectId: document.project } }"
            />
            <h1 class="viewer-page__title">{{ document.title }}</h1>
          </div>
          <p class="text-xs text-muted">
            {{ document.document_type_label }}
            <template v-if="document.reference"> · {{ document.reference }}</template>
            · {{ document.document_date ? formatDate(document.document_date) : 'no document date' }}
            · {{ document.current_version?.page_count ?? EM_DASH }} page(s)
          </p>
        </div>

        <div class="row gap-2">
          <AppButton
            size="sm"
            variant="ghost"
            :icon="showStages ? 'pi pi-chevron-up' : 'pi pi-chevron-down'"
            label="Processing"
            @click="showStages = !showStages"
          />
          <AppButton
            v-if="canReprocess"
            size="sm"
            variant="ghost"
            icon="pi pi-refresh"
            label="Reprocess"
            :loading="reprocessing"
            @click="reprocess"
          />
        </div>
      </header>

      <div v-if="showStages" class="surface viewer-page__stages">
        <ProcessingStages :processing="processing" />
      </div>

      <PageViewer
        class="grow"
        :title="document.title"
        :subtitle="document.current_version?.original_filename"
        :page="page"
        :highlight="highlight"
        :fetch-page="fetchPage"
        :image-url="imageUrl"
        :file-url="documentsApi.documentFileUrl(documentId)"
        :clauses="sections.clauses"
        @update:page="setPage"
      />
    </template>
  </div>
</template>

<style scoped>
.viewer-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  max-width: none;
}

.viewer-page__title {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}

.viewer-page__stages {
  padding: var(--space-3);
}
</style>
