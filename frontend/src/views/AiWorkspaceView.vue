<script setup>
import { computed, onMounted, ref } from 'vue'

import { useRoute, useRouter } from 'vue-router'

import * as knowledgeApi from '@/api/knowledge'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AiAskPanel from '@/components/domain/AiAskPanel.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import { useProjectSelection } from '@/composables/useProjectSelection'

/**
 * The AI workspace, in one of two scopes.
 *
 * **A project** reads that project's documents, and the form that governs it
 * where the project declares one. **A standard form** reads one published
 * edition and nothing else — the question asked while reading a contract
 * rather than while running a claim, and the one that used to require
 * inventing a project to ask it.
 *
 * The scope lives in the URL, so an answer and the scope that produced it can
 * be reloaded or sent to a colleague together. `edition` in the query means
 * the standard-form scope; `project` means the project scope. They are never
 * both set, because the two are different questions with different answers.
 */
const route = useRoute()
const router = useRouter()

// `edition` in the query is what distinguishes the two scopes, so it is read
// before the project selection — which must not carry an ambient project into
// a URL that is asking about a standard form.
//
// The scope turns on whether the parameter is *present*, not on whether it has
// a value. "Asking about a standard form, but which one is not settled yet" is
// a real state — it is what the screen shows while the edition dropdown is
// still empty — and reading an empty `edition` as "no edition chosen, so this
// must be the project scope" collapsed it back the instant it was entered.
const scope = computed(() =>
  'edition' in route.query ? 'standard-form' : 'project'
)
const edition = computed(() => (route.query.edition ? String(route.query.edition) : null))

const { projectId, selected } = useProjectSelection('project', {
  carryAmbient: () => scope.value === 'project',
})

const editions = ref([])
const editionsLoading = ref(true)

/** Only published editions: an unpublished one has nothing to retrieve. */
const retrievable = computed(() =>
  editions.value.filter((item) => item.knowledge_base?.is_retrievable)
)

const editionLabel = computed(
  () => retrievable.value.find((e) => e.code === edition.value)?.label ?? edition.value
)

function setQuery(next) {
  const query = { ...route.query }
  // Moving between scopes is a change of view, not a step in the journey, and
  // the answer on screen belonged to the scope being left.
  delete query.project
  delete query.edition
  delete query.question
  Object.assign(query, next)
  router.replace({ query })
}

function chooseProjectScope() {
  if (scope.value === 'project') return
  setQuery({})
}

function chooseStandardFormScope() {
  if (scope.value === 'standard-form') return
  // Pre-select when there is only one published form, so the common
  // installation does not ask a question with one possible answer.
  const only = retrievable.value.length === 1 ? retrievable.value[0].code : null
  setQuery(only ? { edition: only } : { edition: '' })
}

function chooseEdition(code) {
  setQuery({ edition: code || '' })
}

onMounted(async () => {
  try {
    const data = await knowledgeApi.listEditions()
    editions.value = data.editions ?? []
  } catch {
    editions.value = []
  } finally {
    editionsLoading.value = false
  }
})
</script>

<template>
  <div class="page">
    <PageHeader
      title="AI workspace"
      description="Grounded question answering, with every assertion carrying the passages that support it. Ask about one project's documents, or about a published standard form on its own."
    >
      <template #actions>
        <div class="scope">
          <div class="scope__modes" role="group" aria-label="What to ask about">
            <button
              type="button"
              class="scope__mode"
              :class="{ 'scope__mode--on': scope === 'project' }"
              :aria-pressed="scope === 'project'"
              @click="chooseProjectScope"
            >
              A project
            </button>
            <button
              type="button"
              class="scope__mode"
              :class="{ 'scope__mode--on': scope === 'standard-form' }"
              :aria-pressed="scope === 'standard-form'"
              @click="chooseStandardFormScope"
            >
              A standard form
            </button>
          </div>

          <ProjectPicker v-if="scope === 'project'" v-model="projectId" />

          <label v-else class="scope__picker">
            <span class="text-xs text-muted">Edition</span>
            <select
              class="field-input scope__select"
              :value="edition || ''"
              :disabled="editionsLoading"
              @change="chooseEdition($event.target.value)"
            >
              <option value="">Choose an edition</option>
              <option v-for="item in retrievable" :key="item.code" :value="item.code">
                {{ item.label }}
              </option>
            </select>
          </label>
        </div>
      </template>
    </PageHeader>

    <AiAskPanel
      v-if="scope === 'project' && projectId"
      :key="`project:${projectId}`"
      :project-id="projectId"
      :project-name="selected?.name"
      :edition-label="selected?.edition_label"
    />

    <AiAskPanel
      v-else-if="scope === 'standard-form' && edition"
      :key="`edition:${edition}`"
      :edition="edition"
      :edition-label="editionLabel"
    />

    <EmptyState
      v-else-if="scope === 'standard-form' && !editionsLoading && !retrievable.length"
      icon="pi pi-book"
      title="No standard form is published yet"
      description="A question about a standard form is answered from its published text. Upload the edition in the knowledge base and publish it, and it will appear here."
    />

    <EmptyState
      v-else-if="scope === 'standard-form'"
      icon="pi pi-book"
      title="Choose an edition first"
      description="Editions differ in ways that change the answer — the claims procedure is Clause 53 in the 1987 Red Book and Clause 20 in the 2017 one. Pick the form the question is about."
    />

    <EmptyState
      v-else
      icon="pi pi-folder-open"
      title="Choose a project first"
      description="An answer about a project is only meaningful against its contract and its documents. Pick the project that establishes the retrieval context — or ask about a standard form on its own."
    />
  </div>
</template>

<style scoped>
.scope {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.scope__modes {
  display: flex;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.scope__mode {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
}

.scope__mode:hover {
  background: var(--color-surface-hover);
}

.scope__mode--on {
  color: var(--color-text);
  font-weight: var(--weight-medium);
  background: var(--color-surface-hover);
}

.scope__picker {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.scope__select {
  width: 260px;
}
</style>
