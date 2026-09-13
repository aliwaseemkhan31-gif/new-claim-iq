<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { useRouter } from 'vue-router'

import * as projectsApi from '@/api/projects'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useAuthStore } from '@/stores/auth'
import { EM_DASH, formatDate, formatRelative } from '@/utils/format'

const router = useRouter()
const auth = useAuthStore()

const query = ref('')
const statusFilter = ref('')
const page = ref(1)
const PAGE_SIZE = 25

const canCreate = computed(() => auth.hasPermission('org.projects.manage'))

const request = useAsyncData(({ signal }) =>
  projectsApi.listProjects(
    {
      search: query.value || undefined,
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: PAGE_SIZE,
      ordering: '-updated_at',
    },
    { signal }
  )
)

const rows = computed(() => {
  const value = request.data.value
  if (!value) return []
  return Array.isArray(value) ? value : (value.results ?? [])
})

const total = computed(() => {
  const value = request.data.value
  if (!value) return null
  return Array.isArray(value) ? value.length : (value.count ?? rows.value.length)
})

const pageCount = computed(() => {
  const value = request.data.value
  if (!value || Array.isArray(value)) return 1
  return value.pages ?? Math.max(1, Math.ceil((value.count ?? 0) / PAGE_SIZE))
})

const hasFilters = computed(() => Boolean(query.value || statusFilter.value))

let debounce = null
watch([query, statusFilter], () => {
  page.value = 1
  clearTimeout(debounce)
  debounce = setTimeout(() => request.execute(), 250)
})

watch(page, () => request.execute())

function clearFilters() {
  query.value = ''
  statusFilter.value = ''
}

function openProject(project) {
  router.push({ name: 'project-overview', params: { projectId: project.id } })
}

onMounted(() => request.execute())
</script>

<template>
  <div class="page">
    <PageHeader
      title="Projects"
      :count="total"
      description="Each project scopes a contract, its document corpus, and the claims raised under it."
    >
      <template #actions>
        <AppButton
          v-if="canCreate"
          variant="primary"
          icon="pi pi-plus"
          label="New project"
          disabled
          title="Project creation requires the projects API"
        />
      </template>
    </PageHeader>

    <div class="toolbar">
      <div class="projects__search">
        <i class="pi pi-search" aria-hidden="true" />
        <input
          v-model="query"
          class="field-input projects__search-input"
          type="search"
          placeholder="Filter by name or contract reference"
          aria-label="Filter projects"
        />
      </div>

      <select v-model="statusFilter" class="field-input projects__status" aria-label="Status">
        <option value="">All statuses</option>
        <option value="active">Active</option>
        <option value="on_hold">On hold</option>
        <option value="closed">Closed</option>
        <option value="archived">Archived</option>
      </select>

      <AppButton
        v-if="hasFilters"
        variant="ghost"
        size="sm"
        label="Clear"
        icon="pi pi-times"
        @click="clearFilters"
      />
    </div>

    <div class="surface projects__panel">
      <div v-if="request.loading.value" class="projects__pad">
        <LoadingSkeleton variant="table" :rows="6" :columns="5" />
      </div>

      <ErrorState
        v-else-if="request.error.value"
        :error="request.error.value"
        title="Could not load projects"
        @retry="request.execute()"
      />

      <EmptyState
        v-else-if="request.isEmpty.value && hasFilters"
        icon="pi pi-filter-slash"
        title="No projects match these filters"
        description="Nothing in this organization matches the current search and status filter."
        secondary-action-label="Clear filters"
        @secondary-action="clearFilters"
      />

      <EmptyState
        v-else-if="request.isEmpty.value"
        icon="pi pi-folder-open"
        title="No projects yet"
        description="A project is the container for a contract, its documents, its correspondence and the claims raised under it. Everything else in ClaimIQ hangs off one."
        :hint="canCreate ? null : 'Creating projects requires the “Manage projects” permission.'"
      />

      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Project</th>
              <th scope="col">Contract</th>
              <th scope="col">Status</th>
              <th scope="col" class="cell-numeric">Documents</th>
              <th scope="col" class="cell-numeric">Claims</th>
              <th scope="col">Updated</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="project in rows"
              :key="project.id"
              class="projects__row"
              tabindex="0"
              @click="openProject(project)"
              @keydown.enter="openProject(project)"
            >
              <td>
                <p class="projects__name">{{ project.name }}</p>
                <p v-if="project.client_name" class="projects__sub">{{ project.client_name }}</p>
              </td>
              <td>
                <span v-if="project.contract_reference" class="text-mono">
                  {{ project.contract_reference }}
                </span>
                <span v-else class="text-muted">{{ EM_DASH }}</span>
                <p v-if="project.contract_form" class="projects__sub">{{ project.contract_form }}</p>
              </td>
              <td><StatusBadge :status="project.status" size="sm" /></td>
              <td class="cell-numeric">
                {{ project.document_count ?? EM_DASH }}
              </td>
              <td class="cell-numeric">
                {{ project.claim_count ?? EM_DASH }}
              </td>
              <td>
                <span :title="formatDate(project.updated_at, { dateStyle: 'full' })">
                  {{ formatRelative(project.updated_at) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <footer v-if="!request.loading.value && rows.length > 0 && pageCount > 1" class="projects__pager">
        <span class="text-xs text-muted">Page {{ page }} of {{ pageCount }}</span>
        <div class="row gap-2">
          <AppButton
            size="sm"
            label="Previous"
            icon="pi pi-angle-left"
            :disabled="page <= 1"
            @click="page -= 1"
          />
          <AppButton
            size="sm"
            label="Next"
            icon-right="pi pi-angle-right"
            :disabled="page >= pageCount"
            @click="page += 1"
          />
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.projects__panel {
  overflow: hidden;
}

.projects__pad {
  padding: var(--space-4);
}

.projects__search {
  position: relative;
  flex: 1 1 320px;
  max-width: 420px;
}

.projects__search .pi {
  position: absolute;
  top: 50%;
  left: var(--space-3);
  transform: translateY(-50%);
  font-size: 12px;
  color: var(--color-text-muted);
  pointer-events: none;
}

.projects__search-input {
  padding-left: var(--space-8);
  height: 32px;
}

.projects__status {
  width: 160px;
  height: 32px;
  padding-block: 0;
}

.projects__row {
  cursor: pointer;
}

.projects__name {
  font-weight: var(--weight-medium);
  color: var(--color-text);
}

.projects__sub {
  margin-top: 1px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.projects__pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border);
  background: var(--color-surface-sunken);
}
</style>
