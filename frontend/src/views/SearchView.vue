<script setup>
import { computed, ref } from 'vue'

import * as searchApi from '@/api/search'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useProjectsStore } from '@/stores/projects'

const projects = useProjectsStore()

const query = ref('')
const submitted = ref('')

const SCOPES = [
  { id: 'documents', label: 'Documents' },
  { id: 'correspondence', label: 'Correspondence' },
  { id: 'clauses', label: 'Contract clauses' },
  { id: 'knowledge', label: 'FIDIC knowledge base' },
]
const scopes = ref(SCOPES.map((s) => s.id))

const request = useAsyncData(({ signal }) =>
  searchApi.search(
    {
      query: submitted.value,
      projectId: projects.activeProjectId ?? undefined,
      scopes: scopes.value,
    },
    { signal }
  )
)

const results = computed(() => {
  const value = request.data.value
  if (!value) return []
  return Array.isArray(value) ? value : (value.results ?? [])
})

const canSearch = computed(() => query.value.trim().length >= 2)

function runSearch() {
  if (!canSearch.value) return
  submitted.value = query.value.trim()
  request.execute()
}

function toggleScope(id) {
  scopes.value = scopes.value.includes(id)
    ? scopes.value.filter((s) => s !== id)
    : [...scopes.value, id]
}
</script>

<template>
  <div class="page">
    <PageHeader
      title="Search"
      description="Hybrid keyword and semantic retrieval across your project corpus and the ingested FIDIC editions."
    />

    <form class="search__bar" @submit.prevent="runSearch">
      <div class="search__input-wrap">
        <i class="pi pi-search" aria-hidden="true" />
        <input
          v-model="query"
          class="field-input search__input"
          type="search"
          placeholder="e.g. notice period for extension of time under clause 20.1"
          aria-label="Search query"
        />
      </div>
      <AppButton
        type="submit"
        variant="primary"
        size="lg"
        label="Search"
        :disabled="!canSearch"
        :loading="request.loading.value"
      />
    </form>

    <div class="search__scopes" role="group" aria-label="Search scopes">
      <button
        v-for="scope in SCOPES"
        :key="scope.id"
        type="button"
        class="search__chip"
        :class="{ 'is-on': scopes.includes(scope.id) }"
        :aria-pressed="scopes.includes(scope.id)"
        @click="toggleScope(scope.id)"
      >
        {{ scope.label }}
      </button>
    </div>

    <div class="surface search__results">
      <div v-if="request.loading.value" class="search__pad">
        <LoadingSkeleton variant="text" :rows="6" />
      </div>

      <ErrorState
        v-else-if="request.error.value"
        :error="request.error.value"
        title="Search failed"
        @retry="request.execute()"
      />

      <EmptyState
        v-else-if="request.isEmpty.value"
        icon="pi pi-search-minus"
        title="No matches"
        :description="`Nothing in the selected scopes matched “${submitted}”. Try broadening the scopes or rephrasing the question.`"
      />

      <ul v-else-if="results.length > 0" class="search__list">
        <li v-for="(hit, index) in results" :key="hit.id ?? index" class="search__hit">
          <div class="row between gap-3">
            <p class="search__hit-title truncate">{{ hit.title || hit.source || 'Result' }}</p>
            <span v-if="hit.score !== undefined" class="text-mono text-muted">
              {{ Number(hit.score).toFixed(3) }}
            </span>
          </div>
          <p v-if="hit.snippet || hit.text" class="search__hit-snippet clamp-2">
            {{ hit.snippet || hit.text }}
          </p>
          <p class="search__hit-meta">
            <span v-if="hit.scope">{{ hit.scope }}</span>
            <span v-if="hit.edition"> · {{ hit.edition }}</span>
            <span v-if="hit.clause_number"> · Clause {{ hit.clause_number }}</span>
            <span v-if="hit.page !== undefined && hit.page !== null"> · p.{{ hit.page }}</span>
          </p>
        </li>
      </ul>

      <EmptyState
        v-else
        icon="pi pi-search"
        title="Search the corpus"
        description="Ask in plain language or search for an exact phrase. Results are drawn from the documents in scope and from the FIDIC editions ingested into this installation — nothing is retrieved from the internet."
      />
    </div>
  </div>
</template>

<style scoped>
.search__bar {
  display: flex;
  gap: var(--space-2);
  max-width: 860px;
}

.search__input-wrap {
  position: relative;
  flex: 1 1 auto;
}

.search__input-wrap .pi {
  position: absolute;
  top: 50%;
  left: var(--space-3);
  transform: translateY(-50%);
  font-size: 13px;
  color: var(--color-text-muted);
  pointer-events: none;
}

.search__input {
  height: 40px;
  padding-left: var(--space-8);
  font-size: var(--text-md);
}

.search__scopes {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.search__chip {
  height: 26px;
  padding: 0 var(--space-3);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: var(--color-text-secondary);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
}

.search__chip:hover {
  border-color: var(--color-border-strong);
}

.search__chip.is-on {
  color: var(--color-accent);
  background: var(--color-accent-subtle);
  border-color: var(--color-accent-border);
}

.search__results {
  overflow: hidden;
}

.search__pad {
  padding: var(--space-4);
}

.search__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.search__hit {
  padding: var(--space-4);
}

.search__hit + .search__hit {
  border-top: 1px solid var(--color-border-subtle);
}

.search__hit-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.search__hit-snippet {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.search__hit-meta {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
