<script setup>
import { computed, ref, watch } from 'vue'

import { useRouter } from 'vue-router'

import * as claimsApi from '@/api/claims'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import ClaimChecklist from '@/components/domain/ClaimChecklist.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import { useProjectSelection } from '@/composables/useProjectSelection'
import { useAuthStore } from '@/stores/auth'

/**
 * Every claim in a project, checked: is the event recorded, was each notice
 * given and given in time, is the claim document filed and was it in time,
 * and is each element of the claim supported?
 *
 * The register a claims manager works down. Each row that can be fixed by
 * filing a document links straight to the place it is filed.
 */
const router = useRouter()
const auth = useAuthStore()
const { projectId } = useProjectSelection()

const results = ref([])
const loading = ref(false)
const error = ref(null)
const onlyOutstanding = ref(false)
const expanded = ref(new Set())

const SUMMARY = [
  ['ok', 'done', 'success'],
  ['late', 'late', 'warning'],
  ['barred', 'time bar', 'danger'],
  ['missing', 'missing', 'danger'],
  ['due', 'due', 'info'],
  ['unknown', 'cannot tell', 'warning'],
  ['contested', 'contested', 'warning'],
]

const visible = computed(() =>
  onlyOutstanding.value ? results.value.filter((item) => item.outstanding > 0) : results.value,
)

const totals = computed(() => {
  const counts = {}
  for (const item of results.value) {
    for (const [status, n] of Object.entries(item.counts)) counts[status] = (counts[status] || 0) + n
  }
  return counts
})

async function load() {
  if (!projectId.value) {
    results.value = []
    return
  }
  loading.value = true
  error.value = null
  try {
    await auth.loadProjectPermissions(projectId.value)
    const data = await claimsApi.fetchChecklists({ project: projectId.value })
    results.value = data.results ?? []
    // One claim: open it. Several: open the ones with work outstanding.
    expanded.value = new Set(
      results.value
        .filter((item) => results.value.length === 1 || item.outstanding > 0)
        .slice(0, 3)
        .map((item) => item.claim.id),
    )
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

function toggle(id) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

function act(item, action) {
  const query = { tab: 'documents' }
  if (action.kind === 'edit') {
    router.push({ name: 'claim-detail', params: { claimId: item.claim.id }, query: { tab: 'overview', edit: '1' } })
    return
  }
  query.add = action.role
  if (action.element) query.element = action.element
  router.push({ name: 'claim-detail', params: { claimId: item.claim.id }, query })
}

watch(projectId, load, { immediate: true })
</script>

<template>
  <div class="page">
    <PageHeader
      title="Claim checklist"
      description="For each claim: the event, the notice, the claim submission and the supporting evidence — whether each is on record, and whether it was given in time under this contract."
    >
      <template #actions>
        <ProjectPicker v-model="projectId" />
      </template>
    </PageHeader>

    <EmptyState
      v-if="!projectId"
      icon="pi pi-folder-open"
      title="Choose a project"
      description="Deadlines come from the project's conditions of contract, so the checklist works one project at a time."
    />

    <div v-else-if="loading" class="stack gap-3">
      <LoadingSkeleton variant="block" height="64px" />
      <LoadingSkeleton variant="block" height="240px" />
    </div>

    <ErrorState v-else-if="error" :error="error" title="Could not build the checklist" @retry="load" />

    <EmptyState
      v-else-if="!results.length"
      icon="pi pi-briefcase"
      title="No claims in this project"
      description="Create a claim first, then file its notice, claim document and supporting records."
    />

    <template v-else>
      <div class="surface checklist-page__summary">
        <div class="row gap-4 wrap">
          <p class="text-sm">
            <strong>{{ results.length }}</strong> claim{{ results.length === 1 ? '' : 's' }}
          </p>
          <template v-for="[status, label, tone] in SUMMARY" :key="status">
            <span v-if="totals[status]" class="checklist-page__chip" :class="`is-${tone}`">
              {{ totals[status] }} {{ label }}
            </span>
          </template>
        </div>
        <label class="row gap-2 text-sm">
          <input v-model="onlyOutstanding" type="checkbox" />
          Only claims with something to do
        </label>
      </div>

      <article v-for="item in visible" :key="item.claim.id" class="surface checklist-page__claim">
        <button
          type="button"
          class="checklist-page__toggle"
          :aria-expanded="expanded.has(item.claim.id)"
          @click="toggle(item.claim.id)"
        >
          <i
            :class="expanded.has(item.claim.id) ? 'pi pi-chevron-down' : 'pi pi-chevron-right'"
            aria-hidden="true"
          />
          <span class="checklist-page__title">
            <span v-if="item.claim.reference" class="text-muted">{{ item.claim.reference }} · </span>
            {{ item.claim.title }}
          </span>
          <span class="text-xs text-muted">{{ item.claim.claim_type_label }}</span>
          <span class="checklist-page__spacer" />
          <span v-if="item.outstanding" class="checklist-page__chip is-danger">
            {{ item.outstanding }} to do
          </span>
          <span v-else-if="item.counts.due" class="checklist-page__chip is-info">
            {{ item.counts.due }} due
          </span>
          <span v-else class="checklist-page__chip is-success">
            <i class="pi pi-check" aria-hidden="true" /> complete
          </span>
        </button>

        <div v-if="expanded.has(item.claim.id)" class="checklist-page__body">
          <ClaimChecklist
            :checklist="item"
            :can-edit="auth.canInProject(item.claim.project, 'claim.edit')"
            @act="act(item, $event)"
          />
          <div class="row gap-3">
            <RouterLink
              class="text-sm"
              :to="{ name: 'claim-detail', params: { claimId: item.claim.id }, query: { tab: 'documents' } }"
            >
              Open the claim's documents <i class="pi pi-arrow-right" aria-hidden="true" />
            </RouterLink>
          </div>
        </div>
      </article>

      <p v-if="!visible.length" class="text-sm text-muted">Every claim in this project is complete.</p>
    </template>
  </div>
</template>

<style scoped>
.checklist-page__summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
}

.checklist-page__claim {
  overflow: hidden;
}

.checklist-page__toggle {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  text-align: left;
}

.checklist-page__toggle:hover {
  background: var(--color-surface-hover);
}

.checklist-page__title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  overflow-wrap: anywhere;
}

.checklist-page__spacer {
  flex: 1 1 auto;
}

.checklist-page__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-2) var(--space-4) var(--space-4);
  border-top: 1px solid var(--color-border-subtle);
}

.checklist-page__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  white-space: nowrap;
  border-radius: var(--radius-full);
}

.checklist-page__chip.is-success {
  color: var(--color-success);
  background: var(--color-success-subtle);
}

.checklist-page__chip.is-warning {
  color: var(--color-warning);
  background: var(--color-warning-subtle);
}

.checklist-page__chip.is-danger {
  color: var(--color-danger);
  background: var(--color-danger-subtle);
}

.checklist-page__chip.is-info {
  color: var(--color-info);
  background: var(--color-info-subtle);
}
</style>
