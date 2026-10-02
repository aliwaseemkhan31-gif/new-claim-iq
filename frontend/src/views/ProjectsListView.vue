<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { useRouter } from 'vue-router'

import * as knowledgeApi from '@/api/knowledge'
import * as projectsApi from '@/api/projects'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import Pager from '@/components/common/Pager.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useListQuery } from '@/composables/useListQuery'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatDate, formatRelative } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()

const PAGE_SIZE = 25

const {
  q,
  status: statusFilter,
  page,
  isFiltered: hasFilters,
  clear: clearFilters,
  snapshot,
} = useListQuery({ q: '', status: '', page: 1 })

// Local while it is being typed, settling into the URL once it stops.
const query = ref(q.value)

const canCreate = computed(() => auth.hasPermission('org.projects.manage'))

const request = useAsyncData(({ signal }) =>
  projectsApi.listProjects(
    {
      search: q.value || undefined,
      status: statusFilter.value || undefined,
      page: page.value,
      page_size: PAGE_SIZE,
      ordering: '-updated_at',
    },
    { signal }
  )
)

const rows = computed(() => rowsOf(request.data.value))

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

// Typing settles into the URL; the URL is what triggers a fetch, so the
// filter and page survive a reload and come back on the way back from a
// project.
let debounce = null
watch(query, (value) => {
  clearTimeout(debounce)
  debounce = setTimeout(() => {
    q.value = value.trim()
  }, 250)
})

watch(q, (value) => {
  if (value !== query.value.trim()) query.value = value
})

watch(snapshot, () => request.execute())

function openProject(project) {
  router.push({ name: 'project-overview', params: { projectId: project.id } })
}

// -- Creation ---------------------------------------------------------------

const editions = ref([])
const createOpen = ref(false)
const saving = ref(false)
const fieldErrors = ref({})

const EMPTY_FORM = {
  name: '',
  code: '',
  description: '',
  contract_edition: '',
  currency: '',
  contract_value: '',
  commencement_date: '',
  completion_date: '',
  location: '',
}

const form = ref({ ...EMPTY_FORM })

const selectedEdition = computed(() =>
  editions.value.find((edition) => edition.code === form.value.contract_edition)
)

async function openCreate() {
  form.value = { ...EMPTY_FORM }
  fieldErrors.value = {}
  createOpen.value = true
  if (editions.value.length) return
  try {
    const data = await knowledgeApi.listEditions()
    editions.value = data.editions ?? []
  } catch (error) {
    ui.notifyError(error, 'Could not load contract editions')
  }
}

async function submitCreate() {
  if (saving.value || !form.value.name.trim()) return
  saving.value = true
  fieldErrors.value = {}
  try {
    const payload = {
      name: form.value.name.trim(),
      code: form.value.code.trim(),
      description: form.value.description.trim(),
      contract_edition: form.value.contract_edition,
      contract_form: selectedEdition.value?.form_code ?? '',
      currency: form.value.currency.trim().toUpperCase(),
      location: form.value.location.trim(),
    }
    if (form.value.contract_value) payload.contract_value = form.value.contract_value
    if (form.value.commencement_date) payload.commencement_date = form.value.commencement_date
    if (form.value.completion_date) payload.completion_date = form.value.completion_date

    const project = await projectsApi.createProject(payload)
    ui.notifySuccess('Project created', 'You are its project manager.')
    createOpen.value = false
    router.push({ name: 'project-overview', params: { projectId: project.id } })
  } catch (error) {
    fieldErrors.value = error?.fieldErrors ?? {}
    if (!Object.keys(fieldErrors.value).length) {
      ui.notifyError(error, 'Could not create the project')
    }
  } finally {
    saving.value = false
  }
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
          @click="openCreate"
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
          placeholder="Filter by name or code"
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
        :action-label="canCreate ? 'Create a project' : null"
        action-icon="pi pi-plus"
        :hint="canCreate ? null : 'Creating projects requires the Manage projects permission.'"
        @action="openCreate"
      />

      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Project</th>
              <th scope="col">Conditions of contract</th>
              <th scope="col">Status</th>
              <th scope="col" class="cell-numeric">Documents</th>
              <th scope="col">Commenced</th>
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
                <p v-if="project.code" class="projects__sub text-mono">{{ project.code }}</p>
              </td>
              <td>
                <span v-if="project.edition_label">{{ project.edition_label }}</span>
                <span v-else class="text-warning">Not declared</span>
              </td>
              <td><StatusBadge :status="project.status" size="sm" /></td>
              <td class="cell-numeric">{{ project.document_count ?? EM_DASH }}</td>
              <td>{{ formatDate(project.commencement_date) }}</td>
              <td>
                <span :title="formatDate(project.updated_at, { dateStyle: 'full' })">
                  {{ formatRelative(project.updated_at) }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <Pager
        v-if="!request.loading.value && rows.length > 0"
        :page="page"
        :page-count="pageCount"
        :total="total"
        unit="projects"
        @update:page="page = $event"
      />
    </div>

    <AppDialog
      v-model="createOpen"
      title="New project"
      description="The conditions of contract decide which standard form may be cited. It can be set later, but no standard-form text is retrievable until it is."
      :busy="saving"
      size="lg"
    >
      <div class="stack gap-4">
        <FormField label="Name" for-id="project-name" required :error="fieldErrors.name">
          <input id="project-name" v-model="form.name" class="field-input" type="text" />
        </FormField>

        <div class="row gap-3 wrap">
          <FormField
            label="Code"
            for-id="project-code"
            class="grow"
            :error="fieldErrors.code"
            hint="Your internal reference."
          >
            <input id="project-code" v-model="form.code" class="field-input" type="text" />
          </FormField>
          <FormField
            label="Location"
            for-id="project-location"
            class="grow"
            :error="fieldErrors.location"
          >
            <input id="project-location" v-model="form.location" class="field-input" type="text" />
          </FormField>
        </div>

        <FormField
          label="Conditions of contract"
          for-id="project-edition"
          :error="fieldErrors.contract_edition"
          hint="Editions differ materially. Retrieval is scoped to the edition declared here and never mixed."
        >
          <select id="project-edition" v-model="form.contract_edition" class="field-input">
            <option value="">Not declared yet</option>
            <option v-for="edition in editions" :key="edition.code" :value="edition.code">
              {{ edition.label }}
            </option>
          </select>
        </FormField>

        <div class="row gap-3 wrap">
          <FormField
            label="Contract value"
            for-id="project-value"
            class="grow"
            :error="fieldErrors.contract_value"
          >
            <input
              id="project-value"
              v-model="form.contract_value"
              class="field-input"
              type="number"
              step="0.01"
            />
          </FormField>
          <FormField
            label="Currency"
            for-id="project-currency"
            class="grow"
            :error="fieldErrors.currency"
            hint="Three-letter code."
          >
            <input
              id="project-currency"
              v-model="form.currency"
              class="field-input"
              type="text"
              maxlength="3"
            />
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField
            label="Commencement"
            for-id="project-start"
            class="grow"
            :error="fieldErrors.commencement_date"
          >
            <input
              id="project-start"
              v-model="form.commencement_date"
              class="field-input"
              type="date"
            />
          </FormField>
          <FormField
            label="Time for completion"
            for-id="project-end"
            class="grow"
            :error="fieldErrors.completion_date"
          >
            <input id="project-end" v-model="form.completion_date" class="field-input" type="date" />
          </FormField>
        </div>

        <FormField label="Description" for-id="project-description" :error="fieldErrors.description">
          <textarea
            id="project-description"
            v-model="form.description"
            class="field-input"
            rows="3"
          />
        </FormField>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="saving" @click="createOpen = false" />
        <AppButton
          variant="primary"
          label="Create project"
          :loading="saving"
          :disabled="!form.name.trim()"
          @click="submitCreate"
        />
      </template>
    </AppDialog>
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
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1 1 260px;
}

.projects__search i {
  position: absolute;
  left: var(--space-3);
  font-size: 12px;
  color: var(--color-text-muted);
}

.projects__search-input {
  padding-left: var(--space-8);
}

.projects__status {
  width: 180px;
}

.projects__row {
  cursor: pointer;
}

.projects__name {
  font-weight: var(--weight-medium);
}

.projects__sub {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
