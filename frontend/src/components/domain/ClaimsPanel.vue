<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import { useRouter } from 'vue-router'

import * as claimsApi from '@/api/claims'
import * as projectsApi from '@/api/projects'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import Pager from '@/components/common/Pager.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useListQuery } from '@/composables/useListQuery'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatCurrency, formatRelative } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * The claims register, for one project or across the user's projects.
 *
 * The awareness date is asked for at creation because every notice period runs
 * from it; it is optional, and a claim without one is shown as such rather
 * than having a date assumed for it.
 *
 * One form serves creation and editing. They ask for the same facts, and a
 * separate edit form is where the two drift apart — a field added to one and
 * forgotten in the other. Which project the claim belongs to is part of that
 * form: across the register a claim has to be *given* a project, and a claim
 * raised against the wrong one has to be moved rather than retyped.
 */
const props = defineProps({
  projectId: { type: String, default: null },
  showProject: { type: Boolean, default: false },
})

const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()
const projectsStore = useProjectsStore()

const CLAIM_TYPES = [
  ['eot', 'Extension of Time'],
  ['variation', 'Variation'],
  ['cost', 'Additional Cost'],
  ['delay', 'Delay'],
  ['disruption', 'Disruption'],
  ['acceleration', 'Acceleration'],
  ['payment', 'Payment'],
  ['compensation_event', 'Compensation Event'],
  ['other', 'Other'],
]

const CLAIM_STATUSES = [
  ['draft', 'Draft'],
  ['notified', 'Notified'],
  ['submitted', 'Submitted'],
  ['under_review', 'Under review'],
  ['determined', 'Determined'],
  ['agreed', 'Agreed'],
  ['rejected', 'Rejected'],
  ['disputed', 'Disputed'],
  ['withdrawn', 'Withdrawn'],
]

const PAGE_SIZE = 25

const {
  q,
  status: statusFilter,
  type: typeFilter,
  page,
  snapshot,
  isFiltered,
  clear,
} = useListQuery({ q: '', status: '', type: '', page: 1 })

// Local while it is being typed, settling into the URL once it stops.
const search = ref(q.value)

const rows = ref([])
const total = ref(null)
const parties = ref([])
const loading = ref(true)
const error = ref(null)

const pageCount = computed(() =>
  total.value == null ? 1 : Math.max(1, Math.ceil(total.value / PAGE_SIZE))
)

// Inside a project, the question is whether this project may be written to.
// Across projects, whether any may be — the project is then chosen in the
// form, and the server checks it against that choice.
const canCreate = computed(() =>
  props.projectId
    ? auth.canInProject(props.projectId, 'claim.create')
    : auth.hasPermission('claim.create')
)

function canEdit(claim) {
  return auth.canInProject(claim.project, 'claim.edit')
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await claimsApi.listClaims({
      project: props.projectId || undefined,
      search: q.value || undefined,
      status: statusFilter.value || undefined,
      claim_type: typeFilter.value || undefined,
      page: page.value,
      page_size: PAGE_SIZE,
      ordering: '-created_at',
    })
    rows.value = rowsOf(data)
    total.value = Array.isArray(data) ? data.length : (data?.count ?? rows.value.length)
    loadScreening()
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

// -- Screening ---------------------------------------------------------------
//
// Loaded after the rows, not with them: the register is useful the moment it
// renders, and triage across claims is worth a second request rather than a
// slower first one. A failure leaves the column blank and the register intact.

const screening = ref({})

const SCREENING_TONE = {
  barred: 'danger',
  not_ready: 'warning',
  ready_with_queries: 'info',
  ready: 'success',
}

async function loadScreening() {
  screening.value = {}
  if (!rows.value.length) return
  try {
    const data = await claimsApi.fetchScreeningSummary({
      project: props.projectId || undefined,
      search: q.value || undefined,
      status: statusFilter.value || undefined,
      claim_type: typeFilter.value || undefined,
    })
    screening.value = data.results ?? {}
  } catch {
    screening.value = {}
  }
}

// Typing settles into the URL; the URL is what triggers a fetch, so Back and
// Forward restore the register exactly as it was left.
let debounce = null
watch(search, (value) => {
  clearTimeout(debounce)
  debounce = setTimeout(() => {
    q.value = value.trim()
  }, 250)
})

watch(q, (value) => {
  if (value !== search.value.trim()) search.value = value
})

watch(snapshot, load)

function open(claim) {
  router.push({ name: 'claim-detail', params: { claimId: claim.id } })
}

// -- Drafting from a document ------------------------------------------------
//
// A claim arrives as paper: a bound submission, or a photograph of one taken
// on a phone. Retyping it is the least valuable part of the job and the
// easiest place to mistype a figure. This reads the pages and proposes a
// claim, which then goes through the ordinary create form — so the review is
// the form the person already knows, and nothing is saved until they save it.

const draftOpen = ref(false)
const draftFiles = ref([])
const draftBusy = ref(false)
const draftResult = ref(null)
const draftError = ref(null)

function openDraft() {
  draftFiles.value = []
  draftResult.value = null
  draftError.value = null
  draftOpen.value = true
}

function onDraftFiles(event) {
  draftFiles.value = Array.from(event.target.files || [])
  draftResult.value = null
  draftError.value = null
}

async function runDraft() {
  if (!draftFiles.value.length || draftBusy.value) return
  draftBusy.value = true
  draftError.value = null
  try {
    draftResult.value = await claimsApi.draftClaimFromDocument(
      props.projectId,
      draftFiles.value,
    )
  } catch (err) {
    draftError.value = err
  } finally {
    draftBusy.value = false
  }
}

/** Match a party named on the document to one recorded on the project. */
function matchParty(name) {
  if (!name) return ''
  const wanted = String(name).trim().toLowerCase()
  const hit = parties.value.find((party) => {
    const known = (party.name || '').toLowerCase()
    return known === wanted || known.includes(wanted) || wanted.includes(known)
  })
  return hit ? hit.id : ''
}

/**
 * Carry the draft into the create form.
 *
 * Only fields the document actually stated are filled. A field it did not
 * state is left blank rather than defaulted, so the reviewer sees the same gap
 * the document has.
 */
async function acceptDraft() {
  const values = {}
  for (const field of draftResult.value?.draft?.fields ?? []) {
    if (!field.found) continue
    values[field.name] = Array.isArray(field.value)
      ? field.value.join(', ')
      : String(field.value)
  }

  await openCreate()

  const claimant = matchParty(values.claimant)
  const respondent = matchParty(values.respondent)
  delete values.claimant
  delete values.respondent

  form.value = { ...form.value, ...values }
  if (claimant) form.value.claimant = claimant
  if (respondent) form.value.respondent = respondent

  draftOpen.value = false
  ui.notifySuccess(
    'Draft ready for review',
    'Nothing is saved yet. Check every field against the document before saving.',
  )
}

// -- Creating and editing ----------------------------------------------------

const createOpen = ref(false)
const saving = ref(false)
const fieldErrors = ref({})

/** The claim being edited, or null when the form is creating a new one. */
const editing = ref(null)
const isEditing = computed(() => Boolean(editing.value))

const EMPTY = {
  project: '',
  title: '',
  reference: '',
  claim_type: 'eot',
  status: 'draft',
  claimant: '',
  respondent: '',
  event_date: '',
  awareness_date: '',
  notice_date: '',
  submission_date: '',
  amount_claimed: '',
  currency: '',
  time_claimed_days: '',
  contractual_basis: '',
  description: '',
}

const form = ref({ ...EMPTY })

// A register scoped to one project does not ask which project; across projects
// it must, both to create a claim and to move one.
const asksForProject = computed(() => !props.projectId)

const projectOptions = computed(() => rowsOf(projectsStore.items))

const movingProject = computed(
  () => isEditing.value && form.value.project && form.value.project !== editing.value.project
)

const destinationName = computed(
  () => projectOptions.value.find((project) => project.id === form.value.project)?.name ?? null
)

function ensureProjectOptions() {
  if (!asksForProject.value || projectsStore.loaded) return
  projectsStore.fetchProjects({ page_size: 100, ordering: 'name' }).catch(() => {})
}

/**
 * Parties for whichever project the form is pointed at.
 *
 * Parties belong to a project, so changing the project changes which claimant
 * and respondent exist. A reference that does not resolve in the new project
 * is cleared rather than carried, here and on the server.
 */
async function loadParties(projectId) {
  if (!projectId) {
    parties.value = []
    return
  }
  try {
    parties.value = await projectsApi.listParties(projectId)
  } catch {
    parties.value = []
  }
}

// True while the form is being filled from a claim or reset for a new one.
// Filling the form changes its project, which is not the user choosing a
// different one — and reacting to it as if it were would clear the claimant
// the form had just been given.
const populating = ref(false)

/** Load the form with `values` without the project watcher reading it as a move. */
async function populate(values) {
  populating.value = true
  form.value = values
  await nextTick()
  populating.value = false
  await loadParties(form.value.project)
}

watch(
  () => form.value.project,
  async (projectId, previous) => {
    if (populating.value || !createOpen.value || projectId === previous) return
    await loadParties(projectId)
    const known = new Set(parties.value.map((party) => party.id))
    if (!known.has(form.value.claimant)) form.value.claimant = ''
    if (!known.has(form.value.respondent)) form.value.respondent = ''
  }
)

async function openCreate() {
  editing.value = null
  fieldErrors.value = {}
  createOpen.value = true
  ensureProjectOptions()
  await populate({ ...EMPTY, project: props.projectId || '' })
}

/**
 * Open the register's form on an existing claim.
 *
 * The claim is re-read rather than edited from the row: a row carries a
 * summary, and saving a form built from a summary would blank every field the
 * register does not show.
 */
async function openEdit(claim) {
  fieldErrors.value = {}
  editing.value = claim
  createOpen.value = true
  ensureProjectOptions()
  try {
    const full = await claimsApi.fetchClaim(claim.id)
    editing.value = full
    await populate({
      ...EMPTY,
      project: full.project ?? '',
      title: full.title ?? '',
      reference: full.reference ?? '',
      claim_type: full.claim_type ?? 'eot',
      status: full.status ?? 'draft',
      claimant: full.claimant ?? '',
      respondent: full.respondent ?? '',
      event_date: full.event_date ?? '',
      awareness_date: full.awareness_date ?? '',
      notice_date: full.notice_date ?? '',
      submission_date: full.submission_date ?? '',
      amount_claimed: full.amount_claimed ?? '',
      currency: full.currency ?? '',
      time_claimed_days: full.time_claimed_days ?? '',
      contractual_basis: (full.contractual_basis ?? []).join(', '),
      description: full.description ?? '',
    })
  } catch (err) {
    createOpen.value = false
    editing.value = null
    ui.notifyError(err, 'Could not open the claim')
  }
}

/** Fields that are sent as null when cleared, rather than omitted. */
const NULLABLE = [
  'claimant',
  'respondent',
  'event_date',
  'awareness_date',
  'notice_date',
  'submission_date',
  'amount_claimed',
  'time_claimed_days',
]

function buildPayload() {
  const payload = {
    project: form.value.project || props.projectId,
    title: form.value.title.trim(),
    reference: form.value.reference.trim(),
    claim_type: form.value.claim_type,
    description: form.value.description.trim(),
    contractual_basis: form.value.contractual_basis
      .split(',')
      .map((value) => value.trim())
      .filter(Boolean),
  }
  if (isEditing.value) payload.status = form.value.status

  for (const field of NULLABLE) {
    const value = form.value[field]
    if (value) payload[field] = value
    // On an edit, a field the user emptied is an instruction to clear it. On a
    // create there is nothing to clear, so it is simply left out.
    else if (isEditing.value) payload[field] = null
  }
  if (form.value.currency) payload.currency = form.value.currency.trim().toUpperCase()
  else if (isEditing.value) payload.currency = ''
  return payload
}

async function submitForm() {
  if (saving.value || !form.value.title.trim()) return
  if (!form.value.project && !props.projectId) {
    fieldErrors.value = { project: 'Choose the project this claim belongs to.' }
    return
  }
  saving.value = true
  fieldErrors.value = {}
  const moved = movingProject.value
  const destination = destinationName.value
  try {
    if (isEditing.value) {
      await claimsApi.updateClaim(editing.value.id, buildPayload())
      ui.notifySuccess(
        'Claim saved',
        moved
          ? `Moved to ${destination}, together with its events and evidence. A claimant or respondent recorded on the previous project has been cleared.`
          : null
      )
      createOpen.value = false
      editing.value = null
      load()
    } else {
      const claim = await claimsApi.createClaim(buildPayload())
      ui.notifySuccess('Claim created')
      createOpen.value = false
      router.push({ name: 'claim-detail', params: { claimId: claim.id } })
    }
  } catch (err) {
    fieldErrors.value = err?.fieldErrors ?? {}
    if (!Object.keys(fieldErrors.value).length) {
      ui.notifyError(
        err,
        isEditing.value ? 'Could not save the claim' : 'Could not create the claim'
      )
    }
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="stack gap-4">
    <div class="toolbar between">
      <div class="toolbar">
        <input
          v-model="search"
          class="field-input claims__search"
          type="search"
          placeholder="Filter by title or reference"
          aria-label="Filter claims"
        />
        <select v-model="statusFilter" class="field-input claims__filter" aria-label="Status">
          <option value="">All statuses</option>
          <option v-for="[value, label] in CLAIM_STATUSES" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
        <select v-model="typeFilter" class="field-input claims__filter" aria-label="Claim type">
          <option value="">All types</option>
          <option v-for="[value, label] in CLAIM_TYPES" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </div>

      <AppButton
        v-if="canCreate && projectId"
        variant="ghost"
        icon="pi pi-camera"
        label="From a document"
        @click="openDraft"
      />
      <AppButton
        v-if="canCreate"
        variant="primary"
        icon="pi pi-plus"
        label="New claim"
        @click="openCreate"
      />
    </div>

    <div class="surface claims__panel">
      <div v-if="loading" class="claims__pad">
        <LoadingSkeleton variant="table" :rows="6" :columns="6" />
      </div>

      <ErrorState v-else-if="error" :error="error" title="Could not load claims" @retry="load" />

      <EmptyState
        v-else-if="!rows.length && isFiltered"
        icon="pi pi-filter-slash"
        title="No claims match"
        description="Nothing here matches the current filter."
        action-label="Clear the filter"
        action-icon="pi pi-filter-slash"
        @action="clear"
      />

      <EmptyState
        v-else-if="!rows.length"
        icon="pi pi-briefcase"
        title="No claims yet"
        description="A claim records what is being asked for, on what contractual basis, and from what date — the record every notice check, evidence test and analysis works from."
        :action-label="canCreate ? 'Create a claim' : null"
        action-icon="pi pi-plus"
        :hint="canCreate ? null : 'Creating claims requires the Create claims permission on this project.'"
        @action="openCreate"
      />

      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Claim</th>
              <th v-if="showProject" scope="col">Project</th>
              <th scope="col">Type</th>
              <th scope="col">Status</th>
              <th scope="col">Screening</th>
              <th scope="col" class="cell-numeric">Claimed</th>
              <th scope="col" class="cell-numeric">Time</th>
              <th scope="col">Assessment</th>
              <th scope="col">Updated</th>
              <th scope="col"><span class="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="claim in rows"
              :key="claim.id"
              class="claims__row"
              tabindex="0"
              @click="open(claim)"
              @keydown.enter="open(claim)"
            >
              <td>
                <p class="claims__title">{{ claim.title }}</p>
                <p class="claims__sub">
                  <span v-if="claim.reference" class="text-mono">{{ claim.reference }}</span>
                  <span v-if="!claim.has_awareness_date" class="text-warning">
                    No awareness date
                  </span>
                </p>
              </td>
              <td v-if="showProject" class="text-xs">{{ claim.project_name || EM_DASH }}</td>
              <td>{{ claim.claim_type_label || claim.claim_type }}</td>
              <td><StatusBadge :status="claim.status" size="sm" /></td>
              <td>
                <StatusBadge
                  v-if="screening[claim.id]"
                  :tone="SCREENING_TONE[screening[claim.id].outcome]"
                  :label="screening[claim.id].outcome_label"
                  size="sm"
                  :title="screening[claim.id].summary"
                />
                <span v-else class="text-xs text-muted">{{ EM_DASH }}</span>
              </td>
              <td class="cell-numeric">
                {{
                  claim.amount_claimed
                    ? formatCurrency(Number(claim.amount_claimed), claim.currency || 'USD')
                    : EM_DASH
                }}
              </td>
              <td class="cell-numeric">
                {{ claim.time_claimed_days ? claim.time_claimed_days + ' d' : EM_DASH }}
              </td>
              <td><StatusBadge :status="claim.human_outcome" size="sm" /></td>
              <td>{{ formatRelative(claim.updated_at) }}</td>
              <td class="cell-actions">
                <AppButton
                  v-if="canEdit(claim)"
                  variant="ghost"
                  size="sm"
                  icon="pi pi-pencil"
                  label="Edit"
                  :title="`Edit ${claim.title}`"
                  @click.stop="openEdit(claim)"
                  @keydown.enter.stop
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <Pager
        v-if="!loading && !error && rows.length"
        :page="page"
        :page-count="pageCount"
        :total="total"
        unit="claims"
        @update:page="page = $event"
      />
    </div>

    <AppDialog
      v-model="draftOpen"
      title="Draft a claim from a document"
      description="Reads the pages and proposes a claim. Nothing is saved: the draft goes into the claim form for you to check against the document."
      size="lg"
      :busy="draftBusy"
    >
      <div class="stack gap-4">
        <FormField
          label="Claim document"
          for-id="draft-files"
          hint="Photographs, a scan or a PDF. The first few pages are read — the cover and the summary carry the fields."
        >
          <input
            id="draft-files"
            class="field-input"
            type="file"
            multiple
            accept="image/*,application/pdf"
            @change="onDraftFiles"
          />
        </FormField>

        <div v-if="draftBusy" class="stack gap-2">
          <LoadingSkeleton variant="text" :rows="3" />
          <p class="text-xs text-muted">
            Reading the pages and drafting. This runs on this machine and takes a
            few minutes on a CPU.
          </p>
        </div>

        <ErrorState
          v-else-if="draftError"
          :error="draftError"
          title="The document could not be read"
          :show-retry="false"
        />

        <template v-else-if="draftResult">
          <div class="surface draft__summary">
            <p class="text-sm">{{ draftResult.draft.summary }}</p>
            <p class="text-xs text-muted">
              Read by {{ draftResult.model }} in {{ draftResult.seconds }}s from
              {{ draftResult.pages.length }} page(s). Every value below is a proposal to
              check against the document, not a fact.
            </p>
          </div>

          <ul v-if="draftResult.draft.notes.length" class="draft__notes">
            <li v-for="note in draftResult.draft.notes" :key="note" class="text-sm">
              {{ note }}
            </li>
          </ul>

          <div class="table-scroll">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">Field</th>
                  <th scope="col">Proposed</th>
                  <th scope="col">Read from the document</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="field in draftResult.draft.fields" :key="field.name">
                  <td class="text-sm">{{ field.label }}</td>
                  <td>
                    <span v-if="field.found" class="draft__value">
                      {{ Array.isArray(field.value) ? field.value.join(', ') : field.value }}
                    </span>
                    <span v-else class="text-xs text-muted">Not stated</span>
                    <p v-if="field.note" class="text-xs text-warning">{{ field.note }}</p>
                  </td>
                  <td class="text-xs text-muted">
                    <template v-if="field.quote">
                      <span :class="{ 'text-danger': !field.quote_verified }">
                        “{{ field.quote }}”
                      </span>
                      <span v-if="!field.quote_verified" class="text-danger">
                        — these words are not in the document; check this one against the page.
                      </span>
                    </template>
                    <span v-else-if="field.found">No quotation given.</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <details class="draft__text">
            <summary class="text-sm">What was read off the pages</summary>
            <pre class="draft__ocr">{{ draftResult.document_text }}</pre>
          </details>
        </template>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="draftBusy" @click="draftOpen = false" />
        <AppButton
          v-if="!draftResult"
          variant="primary"
          label="Read the document"
          :loading="draftBusy"
          :disabled="!draftFiles.length"
          @click="runDraft"
        />
        <AppButton
          v-else
          variant="primary"
          icon="pi pi-check"
          label="Review in the claim form"
          @click="acceptDraft"
        />
      </template>
    </AppDialog>

    <AppDialog
      v-model="createOpen"
      :title="isEditing ? 'Edit claim' : 'New claim'"
      description="Record what is claimed and the dates it turns on. Notice periods are computed from the awareness date, so it is worth establishing early."
      size="lg"
      :busy="saving"
    >
      <div class="stack gap-4">
        <FormField
          v-if="asksForProject"
          label="Project"
          for-id="claim-project"
          required
          :error="fieldErrors.project"
          :hint="
            isEditing
              ? 'Moving the claim takes its events and evidence with it.'
              : 'The project this claim is raised under.'
          "
        >
          <select id="claim-project" v-model="form.project" class="field-input">
            <option value="">Choose a project</option>
            <option v-for="project in projectOptions" :key="project.id" :value="project.id">
              {{ project.name }}
            </option>
          </select>
        </FormField>

        <p v-if="movingProject" class="claims__warning">
          This claim will move from {{ editing.project_name }} to {{ destinationName }}. Its
          events and evidence move with it. A claimant or respondent recorded on
          {{ editing.project_name }} does not exist on the new project and will be cleared.
        </p>

        <FormField label="Title" for-id="claim-title" required :error="fieldErrors.title">
          <input id="claim-title" v-model="form.title" class="field-input" type="text" />
        </FormField>

        <div class="row gap-3 wrap">
          <FormField label="Reference" for-id="claim-reference" class="grow" :error="fieldErrors.reference">
            <input id="claim-reference" v-model="form.reference" class="field-input" type="text" />
          </FormField>
          <FormField label="Type" for-id="claim-type" class="grow" :error="fieldErrors.claim_type">
            <select id="claim-type" v-model="form.claim_type" class="field-input">
              <option v-for="[value, label] in CLAIM_TYPES" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </FormField>
          <!-- A new claim starts as a draft; the status is only a question once
               the claim exists and has moved on. -->
          <FormField
            v-if="isEditing"
            label="Status"
            for-id="claim-status"
            class="grow"
            :error="fieldErrors.status"
          >
            <select id="claim-status" v-model="form.status" class="field-input">
              <option v-for="[value, label] in CLAIM_STATUSES" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Claimant" for-id="claim-claimant" class="grow" :error="fieldErrors.claimant">
            <select id="claim-claimant" v-model="form.claimant" class="field-input">
              <option value="">Not recorded</option>
              <option v-for="party in parties" :key="party.id" :value="party.id">
                {{ party.name }} ({{ party.role }})
              </option>
            </select>
          </FormField>
          <FormField label="Respondent" for-id="claim-respondent" class="grow" :error="fieldErrors.respondent">
            <select id="claim-respondent" v-model="form.respondent" class="field-input">
              <option value="">Not recorded</option>
              <option v-for="party in parties" :key="party.id" :value="party.id">
                {{ party.name }} ({{ party.role }})
              </option>
            </select>
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Event date" for-id="claim-event" class="grow" :error="fieldErrors.event_date">
            <input id="claim-event" v-model="form.event_date" class="field-input" type="date" />
          </FormField>
          <FormField
            label="Awareness date"
            for-id="claim-awareness"
            class="grow"
            :error="fieldErrors.awareness_date"
            hint="When the claiming party knew, or should have known. Notice periods run from this."
          >
            <input id="claim-awareness" v-model="form.awareness_date" class="field-input" type="date" />
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Notice date" for-id="claim-notice" class="grow" :error="fieldErrors.notice_date">
            <input id="claim-notice" v-model="form.notice_date" class="field-input" type="date" />
          </FormField>
          <FormField label="Submitted" for-id="claim-submitted" class="grow" :error="fieldErrors.submission_date">
            <input id="claim-submitted" v-model="form.submission_date" class="field-input" type="date" />
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Amount claimed" for-id="claim-amount" class="grow" :error="fieldErrors.amount_claimed">
            <input id="claim-amount" v-model="form.amount_claimed" class="field-input" type="number" step="0.01" />
          </FormField>
          <FormField label="Currency" for-id="claim-currency" class="grow" :error="fieldErrors.currency">
            <input id="claim-currency" v-model="form.currency" class="field-input" type="text" maxlength="3" />
          </FormField>
          <FormField label="Time claimed (days)" for-id="claim-days" class="grow" :error="fieldErrors.time_claimed_days">
            <input id="claim-days" v-model="form.time_claimed_days" class="field-input" type="number" />
          </FormField>
        </div>

        <FormField
          label="Clauses relied on"
          for-id="claim-basis"
          :error="fieldErrors.contractual_basis"
          hint="Comma separated, for example 8.5, 20.2.1"
        >
          <input id="claim-basis" v-model="form.contractual_basis" class="field-input" type="text" />
        </FormField>

        <FormField label="Description" for-id="claim-description" :error="fieldErrors.description">
          <textarea id="claim-description" v-model="form.description" class="field-input" rows="3" />
        </FormField>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="saving" @click="createOpen = false" />
        <AppButton
          variant="primary"
          :label="isEditing ? 'Save claim' : 'Create claim'"
          :loading="saving"
          :disabled="!form.title.trim()"
          @click="submitForm"
        />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.draft__summary {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.draft__notes {
  margin: 0;
  padding-left: var(--space-4);
  color: var(--color-warning);
}

.draft__value {
  font-weight: var(--weight-medium);
}

.draft__ocr {
  max-height: 260px;
  overflow: auto;
  padding: var(--space-2);
  font-size: var(--text-xs);
  white-space: pre-wrap;
  background: var(--color-surface-sunken, transparent);
  border-radius: var(--radius-sm);
}

.claims__panel {
  overflow: hidden;
}

.claims__pad {
  padding: var(--space-4);
}

.claims__search {
  width: 260px;
}

.claims__filter {
  width: 170px;
}

.claims__row {
  cursor: pointer;
}

.claims__title {
  font-weight: var(--weight-medium);
}

.claims__sub {
  display: flex;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.cell-actions {
  width: 1%;
  white-space: nowrap;
  text-align: right;
}

.claims__warning {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-warning);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-sm);
}
</style>
