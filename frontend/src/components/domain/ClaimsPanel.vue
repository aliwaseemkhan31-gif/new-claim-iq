<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { useRouter } from 'vue-router'

import * as claimsApi from '@/api/claims'
import * as projectsApi from '@/api/projects'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatCurrency, formatRelative } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * The claims register, for one project or across the user's projects.
 *
 * The awareness date is asked for at creation because every notice period runs
 * from it; it is optional, and a claim without one is shown as such rather
 * than having a date assumed for it.
 */
const props = defineProps({
  projectId: { type: String, default: null },
  showProject: { type: Boolean, default: false },
})

const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()

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

const query = ref('')
const statusFilter = ref('')
const typeFilter = ref('')
const rows = ref([])
const parties = ref([])
const loading = ref(true)
const error = ref(null)

const canCreate = computed(
  () => Boolean(props.projectId) && auth.canInProject(props.projectId, 'claim.create')
)

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await claimsApi.listClaims({
      project: props.projectId || undefined,
      search: query.value || undefined,
      status: statusFilter.value || undefined,
      claim_type: typeFilter.value || undefined,
      page_size: 100,
      ordering: '-created_at',
    })
    rows.value = rowsOf(data)
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
      search: query.value || undefined,
      status: statusFilter.value || undefined,
      claim_type: typeFilter.value || undefined,
    })
    screening.value = data.results ?? {}
  } catch {
    screening.value = {}
  }
}

let debounce = null
watch([query, statusFilter, typeFilter], () => {
  clearTimeout(debounce)
  debounce = setTimeout(load, 250)
})

function open(claim) {
  router.push({ name: 'claim-detail', params: { claimId: claim.id } })
}

// -- Creation ---------------------------------------------------------------

const createOpen = ref(false)
const saving = ref(false)
const fieldErrors = ref({})

const EMPTY = {
  title: '',
  reference: '',
  claim_type: 'eot',
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

async function openCreate() {
  form.value = { ...EMPTY }
  fieldErrors.value = {}
  createOpen.value = true
  if (parties.value.length || !props.projectId) return
  try {
    parties.value = await projectsApi.listParties(props.projectId)
  } catch {
    parties.value = []
  }
}

async function submitCreate() {
  if (saving.value || !form.value.title.trim()) return
  saving.value = true
  fieldErrors.value = {}
  try {
    const payload = {
      project: props.projectId,
      title: form.value.title.trim(),
      reference: form.value.reference.trim(),
      claim_type: form.value.claim_type,
      description: form.value.description.trim(),
      contractual_basis: form.value.contractual_basis
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean),
    }
    for (const field of [
      'claimant',
      'respondent',
      'event_date',
      'awareness_date',
      'notice_date',
      'submission_date',
      'amount_claimed',
      'time_claimed_days',
    ]) {
      if (form.value[field]) payload[field] = form.value[field]
    }
    if (form.value.currency) payload.currency = form.value.currency.trim().toUpperCase()

    const claim = await claimsApi.createClaim(payload)
    ui.notifySuccess('Claim created')
    createOpen.value = false
    router.push({ name: 'claim-detail', params: { claimId: claim.id } })
  } catch (err) {
    fieldErrors.value = err?.fieldErrors ?? {}
    if (!Object.keys(fieldErrors.value).length) ui.notifyError(err, 'Could not create the claim')
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
          v-model="query"
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
        v-else-if="!rows.length && (query || statusFilter || typeFilter)"
        icon="pi pi-filter-slash"
        title="No claims match"
        description="Nothing here matches the current filter."
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
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <AppDialog
      v-model="createOpen"
      title="New claim"
      description="Record what is claimed and the dates it turns on. Notice periods are computed from the awareness date, so it is worth establishing early."
      size="lg"
      :busy="saving"
    >
      <div class="stack gap-4">
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
          label="Create claim"
          :loading="saving"
          :disabled="!form.title.trim()"
          @click="submitCreate"
        />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
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
</style>
