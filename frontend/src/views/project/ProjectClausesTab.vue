<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import * as documentsApi from '@/api/documents'
import * as knowledgeApi from '@/api/knowledge'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { rowsOf } from '@/utils/viewer'

/**
 * The clause map of this project's own contract documents, beside the standard
 * form it is based on.
 *
 * This is the comparison the claims workflow starts from: the Particular or
 * Supplementary Conditions govern where they differ, so seeing the project's
 * text and the unamended form together is what tells you whether they do.
 */
const props = defineProps({
  project: { type: Object, required: true },
})

const CONTRACT_TYPES = [
  'contract_agreement',
  'conditions_of_contract',
  'particular_conditions',
  'general_conditions',
  'addendum',
  'amendment',
]

const documents = ref([])
const selectedDocument = ref(null)
const sections = ref({ clauses: [], tables: [] })
const loading = ref(true)
const loadingSections = ref(false)
const error = ref(null)

const knowledgeBase = ref(null)
const selectedClause = ref(null)
const standardText = ref(null)
const loadingStandard = ref(false)

const clauses = computed(() => sections.value.clauses ?? [])

async function loadDocuments() {
  loading.value = true
  error.value = null
  try {
    const data = await documentsApi.listDocuments({ project: props.project.id, page_size: 100 })
    documents.value = rowsOf(data).filter((document) =>
      CONTRACT_TYPES.includes(document.document_type)
    )
    if (documents.value.length) selectedDocument.value = documents.value[0].id
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

async function loadSections() {
  if (!selectedDocument.value) return
  loadingSections.value = true
  try {
    sections.value = await documentsApi.fetchDocumentSections(selectedDocument.value)
  } catch {
    sections.value = { clauses: [], tables: [] }
  } finally {
    loadingSections.value = false
  }
}

async function loadKnowledgeBase() {
  if (!props.project.contract_edition) return
  try {
    const data = await knowledgeApi.listKnowledgeBases({ edition: props.project.contract_edition })
    knowledgeBase.value = (data.results ?? []).find((base) => base.status === 'published') ?? null
  } catch {
    knowledgeBase.value = null
  }
}

async function compare(clause) {
  selectedClause.value = clause
  standardText.value = null
  if (!knowledgeBase.value || !clause.clause_number) return
  loadingStandard.value = true
  try {
    standardText.value = await knowledgeApi.fetchKnowledgeBaseClause(
      knowledgeBase.value.id,
      clause.clause_number
    )
  } catch {
    standardText.value = null
  } finally {
    loadingStandard.value = false
  }
}

watch(selectedDocument, loadSections)

onMounted(async () => {
  await Promise.all([loadDocuments(), loadKnowledgeBase()])
  await loadSections()
})
</script>

<template>
  <div class="page">
    <div v-if="loading" class="surface clauses__pad"><LoadingSkeleton variant="text" :rows="6" /></div>

    <ErrorState v-else-if="error" :error="error" title="Could not load contract documents" @retry="loadDocuments" />

    <EmptyState
      v-else-if="!documents.length"
      icon="pi pi-file"
      title="No contract documents uploaded"
      description="Upload the contract agreement, conditions of contract and any particular or supplementary conditions. Their clause structure is detected during processing and shown here."
    />

    <template v-else>
      <div class="toolbar between">
        <label class="row gap-2">
          <span class="text-xs text-muted">Document</span>
          <select v-model="selectedDocument" class="field-input clauses__select">
            <option v-for="document in documents" :key="document.id" :value="document.id">
              {{ document.title }} ({{ document.document_type_label }})
            </option>
          </select>
        </label>

        <div class="row gap-2">
          <StatusBadge
            v-if="knowledgeBase"
            tone="info"
            :label="'Comparing against ' + knowledgeBase.edition_label"
            size="sm"
          />
          <span v-else-if="project.contract_edition" class="text-xs text-warning">
            No published standard form for this edition to compare against.
          </span>
          <span v-else class="text-xs text-warning">
            No conditions of contract declared, so no comparison is possible.
          </span>
        </div>
      </div>

      <div class="clauses">
        <section class="surface clauses__list">
          <div v-if="loadingSections" class="clauses__pad"><LoadingSkeleton variant="text" :rows="10" /></div>
          <p v-else-if="!clauses.length" class="clauses__pad text-sm text-muted">
            No clause structure was detected in this document.
          </p>
          <ul v-else class="clauses__items">
            <li v-for="clause in clauses" :key="clause.id">
              <button
                type="button"
                class="clauses__item"
                :class="{ 'is-active': selectedClause?.id === clause.id }"
                :style="{ paddingLeft: 8 + (clause.depth - 1) * 12 + 'px' }"
                @click="compare(clause)"
              >
                <span class="text-mono">{{ clause.clause_number }}</span>
                <span class="truncate">{{ clause.title }}</span>
              </button>
            </li>
          </ul>
        </section>

        <section class="surface clauses__detail">
          <EmptyState
            v-if="!selectedClause"
            compact
            icon="pi pi-list"
            title="Choose a clause"
            description="Its text in this project's document is shown beside the standard form's text for the same clause."
          />

          <template v-else>
            <header class="row between gap-2 wrap">
              <h3 class="clauses__heading">
                {{ selectedClause.clause_number }} {{ selectedClause.title }}
              </h3>
              <RouterLink
                class="text-xs"
                :to="{
                  name: 'document-viewer',
                  params: { documentId: selectedDocument },
                  query: { page: String(selectedClause.page) },
                }"
              >
                Open at page {{ selectedClause.page }}
              </RouterLink>
            </header>

            <div class="clauses__compare">
              <article>
                <p class="text-overline">This project's document</p>
                <p class="text-xs text-muted">
                  Detected with confidence {{ Math.round((selectedClause.confidence ?? 0) * 100) }}%.
                  Open the page to read the text as it appears.
                </p>
              </article>

              <article>
                <p class="text-overline">Standard form</p>
                <div v-if="loadingStandard"><LoadingSkeleton variant="text" :rows="4" /></div>
                <template v-else-if="standardText">
                  <p
                    v-for="chunk in standardText.chunks"
                    :key="chunk.id"
                    class="clauses__standard"
                  >
                    {{ chunk.text }}
                  </p>
                  <RouterLink
                    v-if="standardText.chunks.length"
                    class="text-xs"
                    :to="{
                      name: 'knowledge-base-viewer',
                      params: { knowledgeBaseId: knowledgeBase.id },
                      query: { page: String(standardText.chunks[0].page_number) },
                    }"
                  >
                    Open in the standard form
                  </RouterLink>
                </template>
                <p v-else class="text-sm text-muted">
                  This clause number is not in the published standard form for this edition — which
                  may itself be the point: it may be a project-specific provision.
                </p>
              </article>
            </div>
          </template>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.clauses {
  display: grid;
  grid-template-columns: minmax(240px, 320px) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

@media (max-width: 900px) {
  .clauses {
    grid-template-columns: minmax(0, 1fr);
  }
}

.clauses__pad {
  padding: var(--space-4);
}

.clauses__select {
  width: 340px;
}

.clauses__list {
  max-height: 560px;
  overflow: auto;
}

.clauses__items {
  display: flex;
  flex-direction: column;
  gap: 1px;
  margin: 0;
  padding: var(--space-2);
  list-style: none;
}

.clauses__item {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: var(--space-2);
  width: 100%;
  padding: 3px var(--space-2);
  font-size: var(--text-xs);
  text-align: left;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
}

.clauses__item:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.clauses__item.is-active {
  background: var(--color-surface-selected);
  color: var(--color-text);
}

.clauses__detail {
  padding: var(--space-4);
  min-height: 240px;
}

.clauses__heading {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.clauses__compare {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--space-4);
  margin-top: var(--space-3);
}

.clauses__standard {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
