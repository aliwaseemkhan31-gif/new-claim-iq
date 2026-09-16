<script setup>
import { computed, onMounted, ref } from 'vue'

import * as searchApi from '@/api/search'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AppButton from '@/components/common/AppButton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import CitationLink from '@/components/domain/CitationLink.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import SourceLayerBadge from '@/components/domain/SourceLayerBadge.vue'
import { useProjectsStore } from '@/stores/projects'
import { LAYER_STANDARD_FORM, sourceLayer } from '@/utils/viewer'

/**
 * Search the record: clause lookup, keyword and meaning, over one project's
 * documents and the standard form for its declared edition.
 *
 * Results are passages, not a generated answer, and each says which corpus it
 * came from.
 */
const projects = useProjectsStore()

const projectId = ref(null)
const query = ref('')
const scopes = ref(['documents', 'knowledge'])
const result = ref(null)
const loading = ref(false)
const error = ref(null)

const canSearch = computed(() => Boolean(projectId.value) && query.value.trim().length >= 2)

const groups = computed(() => {
  const rows = result.value?.results ?? []
  return [
    {
      label: 'This project’s documents',
      items: rows.filter((row) => sourceLayer(row) !== LAYER_STANDARD_FORM),
    },
    {
      label: 'Standard form',
      items: rows.filter((row) => sourceLayer(row) === LAYER_STANDARD_FORM),
    },
  ].filter((group) => group.items.length)
})

function toggleScope(scope) {
  scopes.value = scopes.value.includes(scope)
    ? scopes.value.filter((value) => value !== scope)
    : [...scopes.value, scope]
}

async function run() {
  if (!canSearch.value) return
  loading.value = true
  error.value = null
  try {
    result.value = await searchApi.search({
      query: query.value.trim(),
      projectId: projectId.value,
      scopes: scopes.value,
    })
  } catch (err) {
    error.value = err
    result.value = null
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  projectId.value = projects.activeProjectId
})
</script>

<template>
  <div class="page">
    <PageHeader
      title="Search"
      description="Clause lookup, keyword and meaning, across a project's documents and the standard form it declares."
    >
      <template #actions>
        <ProjectPicker v-model="projectId" />
      </template>
    </PageHeader>

    <form class="surface search__bar" @submit.prevent="run">
      <input
        v-model="query"
        class="field-input grow"
        type="search"
        placeholder="A clause number, a phrase, or a question — for example 20.2.1, or notice of claim"
        aria-label="Search"
      />
      <AppButton
        type="submit"
        variant="primary"
        icon="pi pi-search"
        label="Search"
        :loading="loading"
        :disabled="!canSearch"
      />
    </form>

    <div class="toolbar">
      <label class="row gap-2 text-sm">
        <input
          type="checkbox"
          :checked="scopes.includes('documents')"
          @change="toggleScope('documents')"
        />
        Project documents
      </label>
      <label class="row gap-2 text-sm">
        <input
          type="checkbox"
          :checked="scopes.includes('knowledge')"
          @change="toggleScope('knowledge')"
        />
        Standard form
      </label>
      <span v-if="result?.edition_label" class="text-xs text-muted">
        {{ result.edition_label }}
      </span>
    </div>

    <div v-if="loading" class="surface search__pad"><LoadingSkeleton variant="text" :rows="8" /></div>

    <ErrorState v-else-if="error" :error="error" title="The search could not be run" :show-retry="false" />

    <EmptyState
      v-else-if="!projectId"
      icon="pi pi-folder-open"
      title="Choose a project"
      description="Search runs inside one project's corpus and the standard form for its edition."
    />

    <template v-else-if="result">
      <div v-if="result.notes.length" class="surface search__notes">
        <p v-for="(note, index) in result.notes" :key="index" class="text-xs text-muted">{{ note }}</p>
      </div>

      <EmptyState
        v-if="!result.count"
        icon="pi pi-search"
        title="Nothing matched"
        description="Try a clause number, or fewer words. Documents must finish processing before they can be searched."
      />

      <section v-for="group in groups" :key="group.label" class="stack gap-2">
        <p class="text-overline">{{ group.label }} ({{ group.items.length }})</p>
        <article v-for="row in group.items" :key="row.chunk_id" class="surface search__result">
          <div class="row between gap-2 wrap">
            <div class="row gap-2 wrap">
              <SourceLayerBadge :source="row" size="sm" />
              <CitationLink :citation="row" :show-quotation="false" />
            </div>
            <div class="row gap-1">
              <StatusBadge
                v-for="method in row.matched_by"
                :key="method"
                tone="neutral"
                :label="method.replace('_', ' ')"
                size="sm"
              />
            </div>
          </div>
          <p class="search__text">{{ row.text }}</p>
        </article>
      </section>
    </template>

    <EmptyState
      v-else
      icon="pi pi-search"
      title="Search the record"
      description="Clause-exact lookup runs first, then keyword and vector search; results are merged and shown with the document and page they came from."
    />
  </div>
</template>

<style scoped>
.search__bar {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-2);
}

.search__pad {
  padding: var(--space-4);
}

.search__notes {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-3);
}

.search__result {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
}

.search__text {
  font-size: var(--text-sm);
  line-height: 1.55;
  white-space: pre-wrap;
}
</style>
