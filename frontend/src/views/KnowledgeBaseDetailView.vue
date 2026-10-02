<script setup>
import { computed, onMounted, ref } from 'vue'

import { useRouter } from 'vue-router'

import * as knowledgeApi from '@/api/knowledge'
import AppButton from '@/components/common/AppButton.vue'
import BackLink from '@/components/common/BackLink.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import ProcessingStages from '@/components/domain/ProcessingStages.vue'
import { usePolling } from '@/composables/usePolling'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/utils/format'

/**
 * One standard-form edition: how its source processed, what validation found,
 * what it will contribute to an answer, and whether it is published.
 */
const props = defineProps({
  knowledgeBaseId: { type: String, required: true },
})

const router = useRouter()
const ui = useUiStore()

const base = ref(null)
const clauses = ref([])
const chunks = ref([])
const loading = ref(true)
const error = ref(null)
const busy = ref('')
const clauseFilter = ref('')
const chunkFilter = ref('quarantined')
const confirmDelete = ref(false)

const report = computed(() => base.value?.validation_report ?? null)
const canManage = computed(() => Boolean(base.value?.can_manage))
const isBuilding = computed(() => ['processing', 'validating'].includes(base.value?.status))

const filteredClauses = computed(() => {
  const needle = clauseFilter.value.trim().toLowerCase()
  if (!needle) return clauses.value
  return clauses.value.filter(
    (clause) =>
      clause.clause_number.toLowerCase().includes(needle) ||
      (clause.title || '').toLowerCase().includes(needle)
  )
})

async function loadChunks() {
  try {
    const data = await knowledgeApi.listKnowledgeBaseChunks(props.knowledgeBaseId, {
      quarantined: chunkFilter.value === 'quarantined' ? 'true' : 'false',
      page_size: 25,
    })
    chunks.value = data.results ?? []
  } catch {
    chunks.value = []
  }
}

async function loadClauses() {
  try {
    const data = await knowledgeApi.listKnowledgeBaseClauses(props.knowledgeBaseId)
    clauses.value = data.results ?? []
  } catch {
    clauses.value = []
  }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    base.value = await knowledgeApi.fetchKnowledgeBase(props.knowledgeBaseId)
    ui.setBreadcrumbLeaf(base.value.edition_label)
    await Promise.all([loadClauses(), loadChunks()])
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

const polling = usePolling(() => knowledgeApi.fetchKnowledgeBase(props.knowledgeBaseId), {
  interval: 4000,
  isDone: (data) => !['processing', 'validating'].includes(data.status),
  onData: (data) => {
    const wasBuilding = isBuilding.value
    base.value = { ...base.value, ...data }
    if (wasBuilding && !['processing', 'validating'].includes(data.status)) {
      loadClauses()
      loadChunks()
    }
  },
})

async function run(action, fn, message) {
  busy.value = action
  try {
    await fn()
    await load()
    if (message) ui.notifySuccess(message)
    if (isBuilding.value) polling.start()
  } catch (err) {
    ui.notifyError(err, 'The action could not be completed')
  } finally {
    busy.value = ''
  }
}

function validate() {
  run(
    'validate',
    () => knowledgeApi.validateKnowledgeBase(props.knowledgeBaseId),
    'Validation complete'
  )
}

function publish() {
  run(
    'publish',
    () => knowledgeApi.publishKnowledgeBase(props.knowledgeBaseId),
    'Published — projects on this edition can cite it now'
  )
}

function unpublish() {
  run(
    'unpublish',
    () => knowledgeApi.unpublishKnowledgeBase(props.knowledgeBaseId),
    'Withdrawn from retrieval'
  )
}

async function remove() {
  busy.value = 'delete'
  try {
    await knowledgeApi.deleteKnowledgeBase(props.knowledgeBaseId)
    ui.notifySuccess('Knowledge base deleted')
    router.push({ name: 'knowledge-base' })
  } catch (err) {
    ui.notifyError(err, 'Could not delete this knowledge base')
  } finally {
    busy.value = ''
    confirmDelete.value = false
  }
}

function onReplace(event) {
  const file = event.target.files?.[0]
  if (!file) return
  run(
    'replace',
    () => knowledgeApi.replaceKnowledgeBaseSource(props.knowledgeBaseId, { file }),
    'Rebuilding from the new source'
  )
}

onMounted(async () => {
  await load()
  if (isBuilding.value) polling.start({ immediate: false })
})
</script>

<template>
  <div class="page">
    <div v-if="loading" class="surface kbd__pad"><LoadingSkeleton variant="text" :rows="6" /></div>
    <ErrorState
      v-else-if="error"
      :error="error"
      title="Could not load this knowledge base"
      @retry="load"
    />

    <template v-else-if="base">
      <header class="row between gap-3 wrap">
        <div>
          <div class="row gap-2 wrap">
            <BackLink :fallback-to="{ name: 'knowledge-base' }" fallback-label="Knowledge base" />
            <h1 class="kbd__title">{{ base.edition_label }}</h1>
            <StatusBadge :status="base.status" />
          </div>
          <p class="text-xs text-muted">
            {{ base.name }} · source {{ base.source?.filename || 'not uploaded' }}
            <template v-if="base.validated_at">
              · validated {{ formatDateTime(base.validated_at) }}
            </template>
          </p>
        </div>

        <div v-if="canManage" class="row gap-2 wrap">
          <AppButton
            size="sm"
            variant="ghost"
            icon="pi pi-check-circle"
            label="Re-validate"
            :loading="busy === 'validate'"
            :disabled="isBuilding"
            @click="validate"
          />
          <AppButton
            v-if="base.status !== 'published'"
            size="sm"
            variant="primary"
            icon="pi pi-send"
            label="Publish"
            :loading="busy === 'publish'"
            :disabled="base.status !== 'ready'"
            @click="publish"
          />
          <AppButton
            v-else
            size="sm"
            variant="ghost"
            icon="pi pi-ban"
            label="Withdraw"
            :loading="busy === 'unpublish'"
            @click="unpublish"
          />
          <AppButton
            size="sm"
            variant="ghost"
            icon="pi pi-eye"
            label="Open source"
            :to="{ name: 'knowledge-base-viewer', params: { knowledgeBaseId } }"
          />
          <AppButton
            size="sm"
            variant="danger"
            icon="pi pi-trash"
            label="Delete"
            @click="confirmDelete = true"
          />
        </div>
      </header>

      <p v-if="base.status === 'ready'" class="surface kbd__callout kbd__callout--warning">
        Validated with no blocking findings. It is not retrievable until it is published.
      </p>
      <p v-else-if="base.status === 'published'" class="surface kbd__callout kbd__callout--success">
        Published. Projects governed by this edition can cite its text.
      </p>
      <p v-else-if="base.status === 'quarantined'" class="surface kbd__callout kbd__callout--danger">
        Validation found blocking issues in the text that would be retrieved. Publication is refused
        until they are resolved.
      </p>
      <p v-else-if="base.build_error" class="surface kbd__callout kbd__callout--danger">
        {{ base.build_error }}
      </p>

      <section class="surface kbd__section">
        <p class="section-title">Source processing</p>
        <ProcessingStages :processing="base.processing" />
        <div v-if="canManage" class="kbd__replace">
          <label class="text-xs text-muted" for="kbd-replace">
            Replace the source file and rebuild
          </label>
          <input
            id="kbd-replace"
            class="field-input"
            type="file"
            accept="application/pdf"
            :disabled="busy === 'replace'"
            @change="onReplace"
          />
        </div>
      </section>

      <section v-if="report" class="surface kbd__section">
        <p class="section-title">Validation</p>
        <dl class="kbd__facts">
          <div><dt>Retained</dt><dd>{{ report.retained_chunks }} passage(s)</dd></div>
          <div><dt>Quarantined</dt><dd>{{ report.quarantined_chunks }}</dd></div>
          <div><dt>Embedded</dt><dd>{{ report.embedded_chunks }}</dd></div>
          <div>
            <dt>Page furniture removed</dt>
            <dd>{{ report.running_lines_removed ?? 0 }} repeated line(s)</dd>
          </div>
        </dl>
        <p class="text-sm">{{ report.summary }}</p>

        <div v-if="report.blocking_issues && report.blocking_issues.length" class="kbd__issues">
          <p class="text-overline">Blocking</p>
          <p
            v-for="(issue, index) in report.blocking_issues"
            :key="index"
            class="kbd__issue"
          >
            {{ issue.message }}
          </p>
        </div>

        <div v-if="report.issues && report.issues.length" class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">Severity</th>
                <th scope="col">Finding</th>
                <th scope="col" class="cell-numeric">Passages</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(issue, index) in report.issues" :key="index">
                <td class="text-mono">{{ issue.rule }}</td>
                <td>
                  <StatusBadge
                    :tone="issue.severity === 'error' ? 'danger' : issue.severity === 'warning' ? 'warning' : 'neutral'"
                    :label="issue.severity"
                    size="sm"
                  />
                </td>
                <td>{{ issue.message }}</td>
                <td class="cell-numeric">{{ issue.chunk_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="surface kbd__section">
        <div class="row between gap-3 wrap">
          <p class="section-title">Clauses ({{ clauses.length }})</p>
          <input
            v-model="clauseFilter"
            class="field-input kbd__filter"
            type="search"
            placeholder="Filter by number or title"
            aria-label="Filter clauses"
          />
        </div>
        <p v-if="!clauses.length" class="text-sm text-muted">
          No clause structure was detected in the retained text.
        </p>
        <div v-else class="kbd__clauses">
          <RouterLink
            v-for="clause in filteredClauses"
            :key="clause.clause_number"
            class="kbd__clause"
            :to="{
              name: 'knowledge-base-viewer',
              params: { knowledgeBaseId },
              query: { page: String(clause.page_number) },
            }"
          >
            <span class="text-mono">{{ clause.clause_number }}</span>
            <span class="truncate">{{ clause.title }}</span>
            <span class="text-xs text-muted">p.{{ clause.page_number }}</span>
          </RouterLink>
        </div>
      </section>

      <section class="surface kbd__section">
        <div class="row between gap-3 wrap">
          <p class="section-title">Passages</p>
          <select
            v-model="chunkFilter"
            class="field-input kbd__filter"
            aria-label="Passage filter"
            @change="loadChunks"
          >
            <option value="quarantined">Quarantined — excluded from retrieval</option>
            <option value="retained">Retained — citable</option>
          </select>
        </div>
        <p v-if="!chunks.length" class="text-sm text-muted">None in this category.</p>
        <ul v-else class="kbd__chunks">
          <li v-for="chunk in chunks" :key="chunk.id" class="kbd__chunk">
            <div class="row gap-2 wrap">
              <span v-if="chunk.clause_number" class="text-mono text-xs">
                {{ chunk.clause_number }}
              </span>
              <span class="text-xs text-muted">p.{{ chunk.page_number }}</span>
              <StatusBadge
                v-if="chunk.quarantine_reason"
                tone="warning"
                :label="chunk.quarantine_reason"
                size="sm"
              />
              <StatusBadge
                v-if="!chunk.has_embedding"
                tone="neutral"
                label="No embedding"
                size="sm"
              />
            </div>
            <p class="kbd__chunk-text">{{ chunk.text }}</p>
          </li>
        </ul>
      </section>
    </template>

    <ConfirmDialog
      v-model="confirmDelete"
      title="Delete this knowledge base?"
      message="The edition stops being retrievable and its source document is removed. This frees the edition so it can be ingested again."
      confirm-label="Delete"
      :loading="busy === 'delete'"
      @confirm="remove"
    />
  </div>
</template>

<style scoped>
.kbd__pad {
  padding: var(--space-4);
}

.kbd__title {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}

.kbd__callout {
  padding: var(--space-3);
  font-size: var(--text-sm);
}

.kbd__callout--warning {
  border-left: 3px solid var(--color-warning);
}

.kbd__callout--success {
  border-left: 3px solid var(--color-success);
}

.kbd__callout--danger {
  border-left: 3px solid var(--color-danger);
  color: var(--color-danger);
}

.kbd__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.kbd__replace {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  max-width: 420px;
}

.kbd__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-3);
  margin: 0;
}

.kbd__facts dt {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.kbd__facts dd {
  margin: 0;
  font-size: var(--text-sm);
}

.kbd__issue {
  font-size: var(--text-xs);
  color: var(--color-danger);
}

.kbd__filter {
  width: 260px;
}

.kbd__clauses {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 320px;
  overflow: auto;
}

.kbd__clause {
  display: grid;
  grid-template-columns: 90px 1fr 48px;
  gap: var(--space-2);
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
}

.kbd__clause:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
  text-decoration: none;
}

.kbd__chunks {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 420px;
  overflow: auto;
}

.kbd__chunk {
  padding: var(--space-2);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-md);
}

.kbd__chunk-text {
  margin-top: var(--space-1);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  white-space: pre-wrap;
}
</style>
