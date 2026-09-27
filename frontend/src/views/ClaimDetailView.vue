<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { useRoute, useRouter } from 'vue-router'

import * as claimsApi from '@/api/claims'
import * as projectsApi from '@/api/projects'
import AppButton from '@/components/common/AppButton.vue'
import AppDialog from '@/components/common/AppDialog.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import FormField from '@/components/common/FormField.vue'
import ScreeningPanel from '@/components/domain/ScreeningPanel.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import AnalysisPanel from '@/components/domain/AnalysisPanel.vue'
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
  ['overview', 'Overview'],
  ['notices', 'Notice'],
  ['evidence', 'Evidence'],
  ['timeline', 'Chronology'],
  ['analysis', 'AI analysis'],
  ['reports', 'Reports'],
]

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

const claim = ref(null)
const project = ref(null)
const gaps = ref(null)
const notice = ref(null)
const screening = ref(null)
const loading = ref(true)
const error = ref(null)

const tab = computed(() => (TABS.some(([id]) => id === route.query.tab) ? route.query.tab : 'overview'))
const projectId = computed(() => claim.value?.project ?? null)
const canAssess = computed(() => auth.canInProject(projectId.value, 'claim.assess'))
const canEdit = computed(() => auth.canInProject(projectId.value, 'claim.edit'))

function setTab(next) {
  router.replace({ query: { ...route.query, tab: next } })
}

async function loadComputed() {
  const [gapResult, noticeResult, screeningResult] = await Promise.allSettled([
    claimsApi.fetchEvidenceGaps(props.claimId),
    claimsApi.fetchNoticeCompliance(props.claimId),
    claimsApi.fetchScreening(props.claimId),
  ])
  gaps.value = gapResult.status === 'fulfilled' ? gapResult.value : null
  screening.value = screeningResult.status === 'fulfilled' ? screeningResult.value : null
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
    await auth.loadProjectPermissions(claim.value.project)
    project.value = await projectsApi.fetchProject(claim.value.project)
    await loadComputed()
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

watch(() => props.claimId, load)

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
          <AppButton
            size="sm"
            variant="ghost"
            icon="pi pi-arrow-left"
            label="Claims"
            :to="{ name: 'project-claims', params: { projectId: claim.project } }"
          />
          <h1 class="claim__title">{{ claim.title }}</h1>
          <StatusBadge :status="claim.status" />
          <StatusBadge :status="claim.human_outcome" size="sm" />
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
            v-if="canAssess"
            size="sm"
            variant="primary"
            icon="pi pi-check-square"
            label="Record determination"
            @click="openAssess"
          />
        </div>
      </header>

      <nav class="claim__tabs" aria-label="Claim sections">
        <button
          v-for="[id, label] in TABS"
          :key="id"
          type="button"
          class="claim__tab"
          :class="{ 'is-active': tab === id }"
          @click="setTab(id)"
        >
          {{ label }}
        </button>
      </nav>

      <!-- Overview -->
      <template v-if="tab === 'overview'">
        <ScreeningPanel v-if="screening" :screening="screening" />

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
                <tr v-for="finding in notice.findings" :key="finding.clause_number">
                  <td class="text-mono">{{ finding.clause_number }}</td>
                  <td>
                    {{ finding.description }}
                    <span v-if="finding.is_condition_precedent" class="text-xs text-muted">
                      condition precedent
                    </span>
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
