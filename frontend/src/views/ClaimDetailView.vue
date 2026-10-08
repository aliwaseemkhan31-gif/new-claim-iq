<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { useRoute, useRouter } from 'vue-router'

import * as claimsApi from '@/api/claims'
import * as projectsApi from '@/api/projects'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import BackLink from '@/components/common/BackLink.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import AnalysisPanel from '@/components/domain/AnalysisPanel.vue'
import ClaimChecklist from '@/components/domain/ClaimChecklist.vue'
import ClaimFilesPanel from '@/components/domain/ClaimFilesPanel.vue'
import ScreeningPanel from '@/components/domain/ScreeningPanel.vue'
import CorrespondencePanel from '@/components/domain/CorrespondencePanel.vue'
import EvidencePanel from '@/components/domain/EvidencePanel.vue'
import ReportsPanel from '@/components/domain/ReportsPanel.vue'
import TimelinePanel from '@/components/domain/TimelinePanel.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatCurrency, formatDate } from '@/utils/format'

/**
 * One claim: what is claimed, whether notice was given, what the record
 * establishes, what the AI found, and what a person determined.
 *
 * The order of the tabs follows the order a claim is actually assessed in.
 */
const props = defineProps({
  claimId: { type: String, required: true },
})

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()

const TABS = [
  // Screening leads because it is the first question asked of a claim, and it
  // is answerable before any of the others have anything to show.
  ['screening', 'Screening'],
  // The checklist and the documents behind it sit next to screening: they are
  // where most of what screening asks for is put right.
  ['checklist', 'Checklist'],
  ['documents', 'Documents'],
  ['overview', 'Overview'],
  ['notices', 'Notice'],
  ['evidence', 'Evidence'],
  ['timeline', 'Chronology'],
  ['analysis', 'AI analysis'],
  ['reports', 'Reports'],
]

const SCREENING_TONE = {
  barred: 'danger',
  lapsed: 'danger',
  not_ready: 'warning',
  ready_with_queries: 'info',
  ready: 'success',
}

const OUTCOMES = [
  ['substantiated', 'Substantiated — accept'],
  ['partially_substantiated', 'Partially substantiated'],
  ['unsubstantiated', 'Unsubstantiated — reject'],
  ['insufficient_evidence', 'Insufficient evidence — request information'],
  ['not_assessed', 'Not assessed'],
]

// Mirrors ClaimIssue.Category. Each one addresses an element the evidence-gap
// engine looks for; evidence is tied to an element by the issue it is attached
// to, so a category missing here makes that element unprovable in the UI.
const ISSUE_CATEGORIES = [
  ['entitlement', 'Contractual entitlement'],
  ['event', 'The triggering event'],
  ['notice_compliance', 'Notice compliance'],
  ['causation', 'Causation'],
  ['responsibility', 'Responsibility'],
  ['instruction', 'The instruction'],
  ['time_impact', 'Time impact'],
  ['cost_impact', 'Cost incurred'],
  ['quantum', 'Quantum'],
  ['mitigation', 'Mitigation'],
  ['other', 'Other'],
]

//: Mirrors ClaimType on the model, as ClaimsPanel does for the create form.
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

const claim = ref(null)
const parties = ref([])
const project = ref(null)
const gaps = ref(null)
const notice = ref(null)
const screening = ref(null)
const checklist = ref(null)
const filesPanel = ref(null)
const loading = ref(true)
const error = ref(null)

/**
 * The tab to show when none is named.
 *
 * A claim that cannot yet be assessed opens on screening, which says why. One
 * that is assessable opens on the overview, because the screening has nothing
 * to stop anyone with.
 */
const defaultTab = computed(() =>
  ['barred', 'lapsed', 'not_ready'].includes(screening.value?.outcome) ? 'screening' : 'overview',
)
const tab = computed(() =>
  TABS.some(([id]) => id === route.query.tab) ? route.query.tab : defaultTab.value,
)
const projectId = computed(() => claim.value?.project ?? null)
const canAssess = computed(() => auth.canInProject(projectId.value, 'claim.assess'))
const canEdit = computed(() => auth.canInProject(projectId.value, 'claim.edit'))

/**
 * Where an outstanding check can be put right.
 *
 * A checklist whose every item ends in "go and find the screen that does this"
 * is a worse experience than no checklist, so each remedy that corresponds to
 * something this application can actually do links to it. Checks with no
 * destination — the informational ones, and anything needing work off-system —
 * are deliberately absent rather than linked somewhere unhelpful.
 */
const SCREENING_ACTIONS = {
  CB1: { kind: 'route', label: 'Open the project', icon: 'pi pi-arrow-up-right' },
  CB2: { kind: 'edit', label: 'Record the clauses', icon: 'pi pi-pencil' },
  CB4: { kind: 'edit', label: 'Record the parties', icon: 'pi pi-pencil' },
  NC1: { kind: 'edit', label: 'Record the awareness date', icon: 'pi pi-pencil' },
  NC3: { kind: 'file', role: 'notice', label: 'Add the notice', icon: 'pi pi-upload' },
  NC6: { kind: 'tab', tab: 'notices', label: 'Go to Notice', icon: 'pi pi-arrow-right' },
  NC7: { kind: 'tab', tab: 'notices', label: 'Go to Notice', icon: 'pi pi-arrow-right' },
  NC8: { kind: 'tab', tab: 'notices', label: 'Go to Notice', icon: 'pi pi-arrow-right' },
  EV1: { kind: 'edit', label: 'Record the event date', icon: 'pi pi-pencil' },
  EV2: { kind: 'edit', label: 'Record the account', icon: 'pi pi-pencil' },
  EV3: { kind: 'edit', label: 'Correct the dates', icon: 'pi pi-pencil' },
  EV4: { kind: 'edit', label: 'Correct the dates', icon: 'pi pi-pencil' },
  EV5: { kind: 'tab', tab: 'notices', label: 'Record the instruction', icon: 'pi pi-arrow-right' },
  RC1: { kind: 'tab', tab: 'documents', label: 'Add supporting documents', icon: 'pi pi-upload' },
  DC1: { kind: 'file', role: 'claim_submission', label: 'Add the claim document', icon: 'pi pi-upload' },
  QR1: { kind: 'edit', label: 'Record the relief', icon: 'pi pi-pencil' },
  QR2: { kind: 'edit', label: 'Record the currency', icon: 'pi pi-pencil' },
  QR3: { kind: 'edit', label: 'Correct the figure', icon: 'pi pi-pencil' },
  QR4: { kind: 'edit', label: 'Check the claim type', icon: 'pi pi-pencil' },
}

const screeningActions = computed(() => (canEdit.value ? SCREENING_ACTIONS : {}))

function actOnScreening(action) {
  if (action.kind === 'edit') openEdit()
  else if (action.kind === 'file') fileDocument(action.role, action.element)
  else if (action.kind === 'tab') setTab(action.tab)
  else if (action.kind === 'route' && claim.value) {
    router.push({ name: 'project-overview', params: { projectId: claim.value.project } })
  }
}

function setTab(next) {
  router.replace({ query: { ...route.query, tab: next } })
}

/**
 * Open the filing dialog for a role, from the checklist or screening.
 *
 * Through the URL when the documents panel is not on screen, so the panel
 * opens the dialog as it mounts; directly when it already is.
 */
function fileDocument(role, element = '') {
  if (tab.value === 'documents' && filesPanel.value) {
    filesPanel.value.open(role, element)
    return
  }
  const query = { ...route.query, tab: 'documents', add: role }
  if (element) query.element = element
  else delete query.element
  router.replace({ query })
}

/** The dialog has opened; drop the request so a reload does not reopen it. */
function onFilesOpened() {
  if (!route.query.add && !route.query.element) return
  const query = { ...route.query }
  delete query.add
  delete query.element
  router.replace({ query })
}

function actOnChecklist(action) {
  if (action.kind === 'edit') openEdit()
  else if (action.kind === 'file') fileDocument(action.role, action.element)
}

async function loadComputed() {
  const [gapResult, noticeResult, screeningResult, checklistResult] = await Promise.allSettled([
    claimsApi.fetchEvidenceGaps(props.claimId),
    claimsApi.fetchNoticeCompliance(props.claimId),
    claimsApi.fetchScreening(props.claimId),
    claimsApi.fetchClaimChecklist(props.claimId),
  ])
  gaps.value = gapResult.status === 'fulfilled' ? gapResult.value : null
  screening.value = screeningResult.status === 'fulfilled' ? screeningResult.value : null
  checklist.value = checklistResult.status === 'fulfilled' ? checklistResult.value : null
  notice.value =
    noticeResult.status === 'fulfilled'
      ? noticeResult.value
      : { findings: [], note: noticeResult.reason?.message }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    claim.value = await claimsApi.fetchClaim(props.claimId)
    ui.setBreadcrumbLeaf(claim.value.title)
    await auth.loadProjectPermissions(claim.value.project)
    project.value = await projectsApi.fetchProject(claim.value.project)
    try {
      parties.value = await projectsApi.listParties(claim.value.project)
    } catch {
      parties.value = []
    }
    await loadComputed()
    // Sent here from the checklist to record a date.
    if (route.query.edit === '1' && canEdit.value) {
      const query = { ...route.query }
      delete query.edit
      router.replace({ query })
      openEdit()
    }
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

watch(() => props.claimId, load)

// -- Editing ----------------------------------------------------------------
//
// Added with screening: almost every outstanding check is "record this on the
// claim", and until now a claim could be created but never corrected. A
// checklist pointing at a field nobody can edit is just a complaint.

const editOpen = ref(false)
const editSaving = ref(false)
const editErrors = ref({})
const editForm = ref({})

function openEdit() {
  const c = claim.value
  editForm.value = {
    reference: c.reference || '',
    title: c.title || '',
    claim_type: c.claim_type || 'other',
    claimant: c.claimant || '',
    respondent: c.respondent || '',
    event_date: c.event_date || '',
    awareness_date: c.awareness_date || '',
    notice_date: c.notice_date || '',
    submission_date: c.submission_date || '',
    amount_claimed: c.amount_claimed ?? '',
    currency: c.currency || '',
    time_claimed_days: c.time_claimed_days ?? '',
    contractual_basis: (c.contractual_basis || []).join(', '),
    description: c.description || '',
  }
  editErrors.value = {}
  editOpen.value = true
}

async function submitEdit() {
  if (editSaving.value || !editForm.value.title.trim()) return
  editSaving.value = true
  editErrors.value = {}
  try {
    const form = editForm.value
    const payload = {
      reference: form.reference.trim(),
      title: form.title.trim(),
      claim_type: form.claim_type,
      description: form.description.trim(),
      currency: form.currency.trim(),
      contractual_basis: form.contractual_basis
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean),
    }
    // An empty field means "not recorded", and must clear the value rather
    // than be dropped from the payload and silently left as it was.
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
      payload[field] = form[field] === '' ? null : form[field]
    }

    claim.value = await claimsApi.updateClaim(props.claimId, payload)
    ui.notifySuccess('Claim updated', 'Screening has been recomputed.')
    editOpen.value = false
    await loadComputed()
  } catch (err) {
    editErrors.value = err?.fieldErrors ?? {}
    if (!Object.keys(editErrors.value).length) ui.notifyError(err, 'Could not update the claim')
  } finally {
    editSaving.value = false
  }
}

// -- Determination ----------------------------------------------------------

const assessOpen = ref(false)
const assessForm = ref({ outcome: 'not_assessed', assessment: '' })
const assessSaving = ref(false)

function openAssess() {
  assessForm.value = {
    outcome: claim.value.human_outcome || 'not_assessed',
    assessment: claim.value.human_assessment || '',
  }
  assessOpen.value = true
}

async function submitAssess() {
  assessSaving.value = true
  try {
    claim.value = await claimsApi.assessClaim(props.claimId, {
      outcome: assessForm.value.outcome,
      assessment: assessForm.value.assessment,
    })
    ui.notifySuccess('Determination recorded')
    assessOpen.value = false
  } catch (err) {
    ui.notifyError(err, 'Could not record the determination')
  } finally {
    assessSaving.value = false
  }
}

// -- Issues -----------------------------------------------------------------

const issueOpen = ref(false)
const issueForm = ref({ title: '', category: 'entitlement', description: '' })
const issueSaving = ref(false)

async function addIssue() {
  if (!issueForm.value.title.trim()) return
  issueSaving.value = true
  try {
    await claimsApi.createClaimIssue(props.claimId, {
      title: issueForm.value.title.trim(),
      category: issueForm.value.category,
      description: issueForm.value.description.trim(),
    })
    issueOpen.value = false
    issueForm.value = { title: '', category: 'entitlement', description: '' }
    claim.value = await claimsApi.fetchClaim(props.claimId)
  } catch (err) {
    ui.notifyError(err, 'Could not add the issue')
  } finally {
    issueSaving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div v-if="loading" class="stack gap-4">
      <LoadingSkeleton variant="text" :rows="3" />
      <LoadingSkeleton variant="block" height="320px" />
    </div>

    <ErrorState v-else-if="error" :error="error" title="Could not open this claim" @retry="load" />

    <template v-else-if="claim">
      <header class="claim__header">
        <div class="row gap-2 wrap">
          <BackLink :fallback-to="{ name: 'claims' }" fallback-label="Claims" />
          <h1 class="claim__title">{{ claim.title }}</h1>
          <StatusBadge :status="claim.status" />
          <StatusBadge :status="claim.human_outcome" size="sm" />
          <!-- Screening state follows the claim onto every tab: a possible
               time bar should not be something you have to go back to find. -->
          <button
            v-if="screening"
            type="button"
            class="claim__screening"
            :class="`is-${SCREENING_TONE[screening.outcome]}`"
            :title="screening.summary"
            @click="setTab('screening')"
          >
            <i class="pi pi-shield" aria-hidden="true" />
            {{ screening.outcome_label }}
          </button>
        </div>

        <dl class="claim__facts">
          <div><dt>Reference</dt><dd>{{ claim.reference || EM_DASH }}</dd></div>
          <div><dt>Type</dt><dd>{{ claim.claim_type_label || claim.claim_type }}</dd></div>
          <div>
            <dt>Amount claimed</dt>
            <dd>
              {{
                claim.amount_claimed
                  ? formatCurrency(Number(claim.amount_claimed), claim.currency || 'USD')
                  : EM_DASH
              }}
            </dd>
          </div>
          <div>
            <dt>Time claimed</dt>
            <dd>{{ claim.time_claimed_days ? claim.time_claimed_days + ' days' : EM_DASH }}</dd>
          </div>
          <div>
            <dt>Awareness date</dt>
            <dd>
              <span v-if="claim.awareness_date">{{ formatDate(claim.awareness_date) }}</span>
              <span v-else class="text-warning">Not recorded</span>
            </dd>
          </div>
          <div>
            <dt>Conditions</dt>
            <dd>{{ project?.edition_label || 'Not declared' }}</dd>
          </div>
        </dl>

        <div class="row gap-2">
          <AppButton
            v-if="canEdit"
            size="sm"
            variant="ghost"
            icon="pi pi-pencil"
            label="Edit claim"
            @click="openEdit"
          />
          <AppButton
            v-if="canAssess"
            size="sm"
            variant="primary"
            icon="pi pi-check-square"
            label="Record determination"
            @click="openAssess"
          />
        </div>
      </header>

      <div class="claim__tabs" role="tablist" aria-label="Claim sections">
        <button
          v-for="[id, label] in TABS"
          :key="id"
          type="button"
          role="tab"
          class="claim__tab"
          :class="{ 'is-active': tab === id }"
          :aria-selected="tab === id"
          @click="setTab(id)"
        >
          {{ label }}
        </button>
      </div>

      <!-- Screening -->
      <template v-if="tab === 'screening'">
        <ScreeningPanel
          v-if="screening"
          :screening="screening"
          :actions="screeningActions"
          @act="actOnScreening"
        />
        <EmptyState
          v-else
          icon="pi pi-shield"
          title="Screening is unavailable"
          description="The checks could not be run for this claim. The rest of the claim is unaffected."
        />
      </template>

      <!-- Checklist -->
      <template v-if="tab === 'checklist'">
        <section class="surface claim__section">
          <div class="row between gap-2 wrap">
            <p class="section-title">Claim checklist</p>
            <span v-if="checklist" class="text-xs text-muted">
              {{ checklist.outstanding ? `${checklist.outstanding} to do` : 'Nothing outstanding' }}
            </span>
          </div>
          <p class="text-xs text-muted">
            Whether the event, each notice, the claim document and each element of the claim are
            on record, and whether each was given within the period this contract sets. Deadlines
            are worked out from the recorded dates, not by a model.
          </p>
          <ClaimChecklist
            v-if="checklist"
            :checklist="checklist"
            :can-edit="canEdit"
            @act="actOnChecklist"
          />
          <p v-else class="text-sm text-muted">The checklist could not be built for this claim.</p>
        </section>
      </template>

      <!-- Documents -->
      <ClaimFilesPanel
        v-if="tab === 'documents' && projectId"
        ref="filesPanel"
        :claim-id="claimId"
        :project-id="projectId"
        :can-edit="canEdit"
        :findings="notice?.findings || []"
        :elements="gaps?.elements || []"
        :open-role="String(route.query.add || '')"
        :open-element="String(route.query.element || '')"
        @opened="onFilesOpened"
        @changed="loadComputed"
      />

      <!-- Overview -->
      <template v-if="tab === 'overview'">
        <section v-if="claim.description" class="surface claim__section">
          <p class="section-title">The claimant’s account</p>
          <p class="claim__description">{{ claim.description }}</p>
          <p class="text-xs text-muted">
            As recorded from the claim. It is not evidence, and is not treated as established.
          </p>
        </section>

        <section class="surface claim__section">
          <p class="section-title">Contractual basis</p>
          <p v-if="claim.contractual_basis.length" class="row gap-2 wrap">
            <span v-for="clause in claim.contractual_basis" :key="clause" class="claim__clause">
              Clause {{ clause }}
            </span>
          </p>
          <p v-else class="text-sm text-muted">No clauses recorded as relied on.</p>
        </section>

        <section class="surface claim__section">
          <div class="row between gap-2 wrap">
            <p class="section-title">Issues</p>
            <AppButton
              v-if="canEdit"
              size="sm"
              variant="ghost"
              icon="pi pi-plus"
              label="Add an issue"
              @click="issueOpen = true"
            />
          </div>
          <p class="text-xs text-muted">
            A claim is rarely one question. Splitting it lets entitlement succeed while notice fails.
          </p>
          <ul v-if="claim.issues.length" class="claim__issues">
            <li v-for="issue in claim.issues" :key="issue.id" class="row between gap-2">
              <span>{{ issue.title }} <span class="text-xs text-muted">({{ issue.category }})</span></span>
              <StatusBadge :status="issue.human_outcome" size="sm" />
            </li>
          </ul>
          <p v-else class="text-sm text-muted">No issues recorded.</p>
        </section>

        <section v-if="gaps" class="surface claim__section">
          <p class="section-title">What the claim must establish</p>
          <p class="text-xs text-muted">
            Computed from the evidence linked to this claim, which is a different question
            from screening: this asks what the record establishes, not whether the claim is
            in a fit state to work on.
          </p>
          <p class="text-sm">{{ gaps.summary }}</p>
          <div class="table-scroll">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">Element</th>
                  <th scope="col">Status</th>
                  <th scope="col">Essential</th>
                  <th scope="col">On record</th>
                  <th scope="col">What would establish it</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="element in gaps.elements" :key="element.code">
                  <td>{{ element.label }}</td>
                  <td><StatusBadge :status="element.status" size="sm" /></td>
                  <td>{{ element.is_essential ? 'Yes' : 'No' }}</td>
                  <td class="text-xs">
                    <!-- An element with only contradicting evidence is not
                         established, but it is not untouched either; showing
                         the counts keeps "missing" from reading as "nobody
                         has looked". -->
                    <span v-if="element.supporting">{{ element.supporting }} supporting</span>
                    <span v-if="element.contradicting" class="text-warning">
                      <template v-if="element.supporting">, </template>
                      {{ element.contradicting }} contradicting
                    </span>
                    <span v-if="!element.supporting && !element.contradicting" class="text-muted">
                      Nothing linked
                    </span>
                  </td>
                  <td class="text-xs text-muted">{{ element.suggestion }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="text-xs text-muted">
            Computed from the evidence linked to this claim. Evidence held elsewhere is not counted.
          </p>
        </section>

        <section v-if="claim.human_assessment" class="surface claim__section">
          <p class="section-title">Determination</p>
          <p class="claim__description">{{ claim.human_assessment }}</p>
        </section>
      </template>

      <!-- Notice -->
      <template v-else-if="tab === 'notices'">
        <section class="surface claim__section">
          <p class="section-title">Notice compliance</p>
          <p class="text-xs text-muted">
            Computed from the recorded awareness date and the notices on record — not by a model. A
            late or missing notice does not automatically defeat a claim; check the wording and the
            stated consequence.
          </p>

          <p v-if="notice && notice.note" class="text-sm text-warning">{{ notice.note }}</p>

          <div v-else-if="notice && notice.findings.length" class="table-scroll">
            <table class="data-table">
              <thead>
                <tr>
                  <th scope="col">Clause</th>
                  <th scope="col">Requirement</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Deadline</th>
                  <th scope="col">Assumptions and caveats</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="finding in notice.findings"
                  :key="`${finding.clause_number}:${finding.obligation}`"
                >
                  <td class="text-mono">{{ finding.clause_number }}</td>
                  <td>
                    <strong>{{ finding.title || finding.description }}</strong>
                    <span class="text-xs text-muted">
                      · {{ finding.period_days }} days<template v-if="finding.runs_from === 'notice'">
                        after the notice under Clause {{ finding.runs_from_clause }}</template>
                    </span>
                    <span v-if="finding.is_condition_precedent" class="text-xs text-muted">
                      · condition precedent
                    </span>
                    <p v-if="finding.is_amended" class="text-xs text-info">
                      Amended by this contract: {{ finding.source }}
                    </p>
                    <p v-if="finding.notice" class="text-xs text-muted">
                      On record: {{ finding.notice.title }}
                    </p>
                  </td>
                  <td>
                    <StatusBadge :status="finding.status" size="sm" />
                    <span v-if="finding.is_time_barred" class="text-xs text-danger">Time bar</span>
                  </td>
                  <td>{{ finding.deadline || EM_DASH }}</td>
                  <td class="text-xs text-muted">
                    {{ [...finding.assumptions, ...finding.warnings].join(' ') || EM_DASH }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <CorrespondencePanel
          v-if="projectId"
          :project-id="projectId"
          :claim-id="claimId"
          @changed="loadComputed"
        />
      </template>

      <!-- Evidence -->
      <EvidencePanel
        v-else-if="tab === 'evidence'"
        :project-id="projectId"
        :claim-id="claimId"
        :issues="claim.issues"
        @changed="loadComputed"
      />

      <!-- Chronology -->
      <TimelinePanel v-else-if="tab === 'timeline'" :project-id="projectId" :claim-id="claimId" />

      <!-- Analysis -->
      <AnalysisPanel v-else-if="tab === 'analysis'" :claim-id="claimId" :project-id="projectId" />

      <!-- Reports -->
      <ReportsPanel v-else-if="tab === 'reports'" :project-id="projectId" :claim-id="claimId" />
    </template>

    <AppDialog
      v-model="assessOpen"
      title="Record a determination"
      description="This is the human assessment. It is recorded separately from any AI finding and never overwrites one."
      :busy="assessSaving"
    >
      <div class="stack gap-4">
        <FormField label="Outcome" for-id="assess-outcome" required>
          <select id="assess-outcome" v-model="assessForm.outcome" class="field-input">
            <option v-for="[value, label] in OUTCOMES" :key="value" :value="value">{{ label }}</option>
          </select>
        </FormField>
        <FormField
          label="Reasons"
          for-id="assess-text"
          hint="What the contract and the record establish, and what follows."
        >
          <textarea id="assess-text" v-model="assessForm.assessment" class="field-input" rows="6" />
        </FormField>
      </div>
      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="assessSaving" @click="assessOpen = false" />
        <AppButton variant="primary" label="Record" :loading="assessSaving" @click="submitAssess" />
      </template>
    </AppDialog>

    <AppDialog
      v-model="editOpen"
      title="Edit claim"
      description="What the record says about the claim. An empty field is recorded as not stated, never guessed."
      size="lg"
      :busy="editSaving"
    >
      <div class="stack gap-4">
        <FormField label="Title" for-id="edit-title" required :error="editErrors.title">
          <input id="edit-title" v-model="editForm.title" class="field-input" type="text" />
        </FormField>

        <div class="row gap-3 wrap">
          <FormField label="Reference" for-id="edit-reference" class="grow" :error="editErrors.reference">
            <input id="edit-reference" v-model="editForm.reference" class="field-input" type="text" />
          </FormField>
          <FormField label="Type" for-id="edit-type" class="grow" :error="editErrors.claim_type">
            <select id="edit-type" v-model="editForm.claim_type" class="field-input">
              <option v-for="[value, label] in CLAIM_TYPES" :key="value" :value="value">
                {{ label }}
              </option>
            </select>
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Claimant" for-id="edit-claimant" class="grow" :error="editErrors.claimant">
            <select id="edit-claimant" v-model="editForm.claimant" class="field-input">
              <option value="">Not recorded</option>
              <option v-for="party in parties" :key="party.id" :value="party.id">
                {{ party.name }} ({{ party.role }})
              </option>
            </select>
          </FormField>
          <FormField label="Respondent" for-id="edit-respondent" class="grow" :error="editErrors.respondent">
            <select id="edit-respondent" v-model="editForm.respondent" class="field-input">
              <option value="">Not recorded</option>
              <option v-for="party in parties" :key="party.id" :value="party.id">
                {{ party.name }} ({{ party.role }})
              </option>
            </select>
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Event date" for-id="edit-event" class="grow" :error="editErrors.event_date">
            <input id="edit-event" v-model="editForm.event_date" class="field-input" type="date" />
          </FormField>
          <FormField
            label="Awareness date"
            for-id="edit-awareness"
            class="grow"
            :error="editErrors.awareness_date"
            hint="When the claiming party knew, or should have known. Notice periods run from this."
          >
            <input id="edit-awareness" v-model="editForm.awareness_date" class="field-input" type="date" />
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Notice date" for-id="edit-notice" class="grow" :error="editErrors.notice_date">
            <input id="edit-notice" v-model="editForm.notice_date" class="field-input" type="date" />
          </FormField>
          <FormField label="Submitted" for-id="edit-submitted" class="grow" :error="editErrors.submission_date">
            <input id="edit-submitted" v-model="editForm.submission_date" class="field-input" type="date" />
          </FormField>
        </div>

        <div class="row gap-3 wrap">
          <FormField label="Amount claimed" for-id="edit-amount" class="grow" :error="editErrors.amount_claimed">
            <input id="edit-amount" v-model="editForm.amount_claimed" class="field-input" type="number" step="0.01" />
          </FormField>
          <FormField label="Currency" for-id="edit-currency" class="grow" :error="editErrors.currency">
            <input id="edit-currency" v-model="editForm.currency" class="field-input" type="text" maxlength="3" />
          </FormField>
          <FormField label="Time claimed (days)" for-id="edit-days" class="grow" :error="editErrors.time_claimed_days">
            <input id="edit-days" v-model="editForm.time_claimed_days" class="field-input" type="number" />
          </FormField>
        </div>

        <FormField
          label="Clauses relied on"
          for-id="edit-clauses"
          :error="editErrors.contractual_basis"
          hint="Comma separated, for example 44.1, 53.1"
        >
          <input id="edit-clauses" v-model="editForm.contractual_basis" class="field-input" type="text" />
        </FormField>

        <FormField label="The claimant's account" for-id="edit-description" :error="editErrors.description">
          <textarea id="edit-description" v-model="editForm.description" class="field-input" rows="4" />
        </FormField>
      </div>

      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="editSaving" @click="editOpen = false" />
        <AppButton
          variant="primary"
          label="Save claim"
          :loading="editSaving"
          :disabled="!editForm.title?.trim()"
          @click="submitEdit"
        />
      </template>
    </AppDialog>

    <AppDialog v-model="issueOpen" title="Add an issue" :busy="issueSaving">
      <div class="stack gap-4">
        <FormField label="Title" for-id="issue-title" required>
          <input id="issue-title" v-model="issueForm.title" class="field-input" type="text" />
        </FormField>
        <FormField label="Category" for-id="issue-category">
          <select id="issue-category" v-model="issueForm.category" class="field-input">
            <option v-for="[value, label] in ISSUE_CATEGORIES" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
        </FormField>
        <FormField label="Description" for-id="issue-description">
          <textarea id="issue-description" v-model="issueForm.description" class="field-input" rows="3" />
        </FormField>
      </div>
      <template #footer>
        <AppButton variant="ghost" label="Cancel" :disabled="issueSaving" @click="issueOpen = false" />
        <AppButton
          variant="primary"
          label="Add issue"
          :loading="issueSaving"
          :disabled="!issueForm.title.trim()"
          @click="addIssue"
        />
      </template>
    </AppDialog>
  </div>
</template>

<style scoped>
.claim__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.claim__title {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.claim__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--space-3);
  margin: 0;
}

.claim__facts dt {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.claim__facts dd {
  margin: 0;
  font-size: var(--text-sm);
}

.claim__tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--color-border);
  overflow-x: auto;
}

.claim__tab {
  position: relative;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.claim__tab:hover {
  color: var(--color-text);
}

.claim__tab.is-active {
  color: var(--color-accent);
}

.claim__tab.is-active::after {
  content: '';
  position: absolute;
  left: var(--space-2);
  right: var(--space-2);
  bottom: -1px;
  height: 2px;
  background: var(--color-accent);
}

.claim__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.claim__description {
  font-size: var(--text-sm);
  line-height: 1.6;
  white-space: pre-line;
}

.claim__clause {
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-full);
}

.claim__screening {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  border: 1px solid transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  background: var(--color-surface-sunken, transparent);
}

.claim__screening.is-danger {
  color: var(--color-danger);
  background: var(--color-danger-subtle);
  border-color: var(--color-danger);
}

.claim__screening.is-warning {
  color: var(--color-warning);
  background: var(--color-warning-subtle);
  border-color: var(--color-warning-border, var(--color-warning));
}

.claim__screening.is-info {
  color: var(--color-info);
  background: var(--color-info-subtle);
  border-color: var(--color-info-border, var(--color-info));
}

.claim__screening.is-success {
  color: var(--color-success);
  background: var(--color-success-subtle);
  border-color: var(--color-success-border, var(--color-success));
}

.claim__issues {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--text-sm);
}
</style>
