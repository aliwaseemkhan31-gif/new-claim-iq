<script setup>
import { computed, onMounted, ref } from 'vue'

import { useRoute, useRouter } from 'vue-router'

import * as knowledgeApi from '@/api/knowledge'
import AppButton from '@/components/common/AppButton.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import PageViewer from '@/components/domain/PageViewer.vue'
import { clampPage } from '@/utils/viewer'

/**
 * The source document behind a knowledge base — where a standard-form citation
 * lands. Read-only: publishing and validation live on the detail screen.
 */
const props = defineProps({
  knowledgeBaseId: { type: String, required: true },
})

const route = useRoute()
const router = useRouter()

const base = ref(null)
const clauses = ref([])
const loading = ref(true)
const error = ref(null)

const page = computed(() => clampPage(route.query.page, base.value?.source?.page_count))
const highlight = computed(() => (route.query.q ? String(route.query.q) : null))

function setPage(value) {
  router.replace({ query: { ...route.query, page: String(value) } })
}

function fetchPage(pageNumber) {
  return knowledgeApi.fetchKnowledgeBasePage(props.knowledgeBaseId, pageNumber)
}

function imageUrl(pageNumber, scale) {
  return knowledgeApi.knowledgeBasePageImageUrl(props.knowledgeBaseId, pageNumber, scale)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    base.value = await knowledgeApi.fetchKnowledgeBase(props.knowledgeBaseId)
    const data = await knowledgeApi.listKnowledgeBaseClauses(props.knowledgeBaseId).catch(() => null)
    clauses.value = data?.results ?? []
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
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

    <ErrorState v-else-if="error" :error="error" title="Could not open this knowledge base" @retry="load" />

    <template v-else-if="base">
      <header class="row between gap-3 wrap">
        <div>
          <div class="row gap-2 wrap">
            <AppButton
              size="sm"
              variant="ghost"
              icon="pi pi-arrow-left"
              label="Knowledge base"
              :to="{ name: 'knowledge-base-detail', params: { knowledgeBaseId } }"
            />
            <h1 class="viewer-page__title">{{ base.edition_label }}</h1>
            <StatusBadge :status="base.status" size="sm" />
          </div>
          <p class="text-xs text-muted">
            Standard-form text. The project’s own contract documents govern where they differ.
          </p>
        </div>
      </header>

      <PageViewer
        class="grow"
        :title="base.name"
        :subtitle="base.source?.filename"
        :page="page"
        :highlight="highlight"
        :fetch-page="fetchPage"
        :image-url="imageUrl"
        :file-url="knowledgeApi.knowledgeBaseFileUrl(knowledgeBaseId)"
        :clauses="clauses"
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
  height: 100%;
  min-height: 0;
  max-width: none;
}

.viewer-page__title {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}
</style>
