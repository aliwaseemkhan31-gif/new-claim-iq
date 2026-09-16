<script setup>
import { computed, onMounted, ref } from 'vue'

import * as aiApi from '@/api/ai'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import ConfidenceBadge from '@/components/domain/ConfidenceBadge.vue'
import FindingCard from '@/components/domain/FindingCard.vue'
import { usePolling } from '@/composables/usePolling'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { EM_DASH, formatDateTime } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * AI analysis of one claim: seven strands, each with its own status,
 * confidence and findings.
 *
 * Findings start unreviewed and say so. Deterministic strands — evidence gaps
 * and notice timing — are computed, not generated, and their computed output is
 * shown as such.
 */
const props = defineProps({
  claimId: { type: String, required: true },
  projectId: { type: String, required: true },
})

const auth = useAuthStore()
const ui = useUiStore()

const analysis = ref(null)
const loading = ref(true)
const error = ref(null)
const starting = ref(false)

const canAnalyse = computed(() => auth.canInProject(props.projectId, 'ai.analyse'))
const canReview = computed(() => auth.canInProject(props.projectId, 'ai.override'))
const isRunning = computed(() => ['queued', 'running'].includes(analysis.value?.status))

const totals = computed(() => {
  const strands = analysis.value?.strands ?? []
  const findings = strands.flatMap((strand) => strand.findings ?? [])
  return {
    findings: findings.length,
    unreviewed: findings.filter((finding) => (finding.review?.state ?? 'unreviewed') === 'unreviewed')
      .length,
  }
})

async function loadLatest() {
  loading.value = true
  error.value = null
  try {
    const list = await aiApi.listAnalyses({ claim: props.claimId, page_size: 1 })
    const latest = rowsOf(list)[0]
    analysis.value = latest ? await aiApi.fetchAnalysis(latest.id) : null
    if (isRunning.value) polling.start({ immediate: false })
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

const polling = usePolling(() => aiApi.fetchAnalysis(analysis.value.id), {
  interval: 4000,
  isDone: (data) => !['queued', 'running'].includes(data.status),
  onData: (data) => {
    analysis.value = data
  },
})

async function start() {
  starting.value = true
  try {
    analysis.value = await aiApi.startClaimAnalysis({
      claimId: props.claimId,
      projectId: props.projectId,
    })
    ui.notifySuccess('Analysis started', 'It runs in the background; this page follows along.')
    polling.start({ immediate: false })
  } catch (err) {
    ui.notifyError(err, 'Could not start the analysis')
  } finally {
    starting.value = false
  }
}

async function cancel() {
  try {
    analysis.value = await aiApi.cancelAnalysis(analysis.value.id)
    polling.stop()
  } catch (err) {
    ui.notifyError(err, 'Could not cancel the analysis')
  }
}

function onReviewed(strand, updated) {
  const index = strand.findings.findIndex((finding) => finding.id === updated.id)
  if (index !== -1) strand.findings.splice(index, 1, updated)
}

onMounted(loadLatest)
</script>

<template>
  <div class="stack gap-4">
    <div v-if="loading" class="surface analysis__pad"><LoadingSkeleton variant="text" :rows="6" /></div>

    <ErrorState v-else-if="error" :error="error" title="Could not load the analysis" @retry="loadLatest" />

    <template v-else>
      <div class="toolbar between">
        <div v-if="analysis" class="row gap-2 wrap">
          <StatusBadge :status="analysis.status" />
          <span v-if="isRunning" class="text-xs text-muted">{{ analysis.progress_percent }}%</span>
          <span v-if="analysis.finished_at" class="text-xs text-muted">
            {{ formatDateTime(analysis.finished_at) }}
          </span>
          <span v-if="analysis.llm_model" class="text-xs text-muted">{{ analysis.llm_model }}</span>
          <StatusBadge
            v-if="totals.unreviewed"
            tone="warning"
            :label="totals.unreviewed + ' of ' + totals.findings + ' findings unreviewed'"
            size="sm"
          />
        </div>
        <span v-else />

        <div class="row gap-2">
          <AppButton
            v-if="isRunning"
            size="sm"
            variant="ghost"
            icon="pi pi-times"
            label="Cancel"
            @click="cancel"
          />
          <AppButton
            v-if="canAnalyse"
            variant="primary"
            size="sm"
            icon="pi pi-sparkles"
            :label="analysis ? 'Run again' : 'Run analysis'"
            :loading="starting"
            :disabled="isRunning"
            @click="start"
          />
        </div>
      </div>

      <EmptyState
        v-if="!analysis"
        icon="pi pi-sparkles"
        title="No analysis has been run"
        description="The analysis reads this claim against its contract: evidence gaps and notice timing are computed, and entitlement, causation, time, quantum and counterarguments are answered from cited sources."
        :hint="canAnalyse ? null : 'Running an analysis requires the Run AI analysis permission.'"
      />

      <template v-else>
        <p class="surface analysis__advisory">{{ analysis.advisory }}</p>

        <p v-if="analysis.error_message" class="surface analysis__error">
          {{ analysis.error_message }}
        </p>

        <section v-for="strand in analysis.strands" :key="strand.strand" class="surface analysis__strand">
          <header class="row between gap-2 wrap">
            <div class="row gap-2 wrap">
              <h3 class="analysis__title">{{ strand.label }}</h3>
              <StatusBadge :status="strand.status" size="sm" />
              <StatusBadge
                v-if="!strand.uses_model && strand.status === 'completed'"
                tone="info"
                label="Computed, not generated"
                size="sm"
              />
            </div>
            <ConfidenceBadge
              v-if="strand.confidence"
              :level="strand.confidence"
              :reasons="strand.confidence_reasons"
            />
          </header>

          <p v-if="strand.status === 'skipped'" class="text-sm text-muted">
            {{ strand.skip_reason }}
          </p>

          <p v-else-if="strand.status === 'failed'" class="analysis__failed">
            {{ strand.error_message }}
          </p>

          <template v-else>
            <p v-if="strand.summary" class="analysis__summary">{{ strand.summary }}</p>

            <div v-if="strand.computed && strand.computed.findings" class="table-scroll">
              <table class="data-table">
                <thead>
                  <tr>
                    <th scope="col">Clause</th>
                    <th scope="col">Requirement</th>
                    <th scope="col">Outcome</th>
                    <th scope="col">Deadline</th>
                    <th scope="col">Caveats</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(finding, index) in strand.computed.findings" :key="index">
                    <td class="text-mono">{{ finding.clause_number }}</td>
                    <td>{{ finding.description }}</td>
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

            <div v-if="strand.computed && strand.computed.elements" class="table-scroll">
              <table class="data-table">
                <thead>
                  <tr>
                    <th scope="col">What must be established</th>
                    <th scope="col">Status</th>
                    <th scope="col">What would establish it</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="element in strand.computed.elements" :key="element.code">
                    <td>{{ element.label }}</td>
                    <td><StatusBadge :status="element.status" size="sm" /></td>
                    <td class="text-xs text-muted">{{ element.suggestion }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="strand.findings && strand.findings.length" class="analysis__findings">
              <FindingCard
                v-for="finding in strand.findings"
                :key="finding.id"
                :finding="finding"
                :can-review="canReview"
                @reviewed="onReviewed(strand, $event)"
              />
            </div>

            <p v-else-if="strand.uses_model && strand.insufficient_evidence" class="text-xs text-muted">
              No findings: the sources did not answer this question.
            </p>
          </template>
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.analysis__pad {
  padding: var(--space-4);
}

.analysis__advisory {
  padding: var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.analysis__error {
  padding: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-danger);
  border-left: 3px solid var(--color-danger);
}

.analysis__strand {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
}

.analysis__title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.analysis__summary {
  font-size: var(--text-sm);
  line-height: 1.55;
}

.analysis__failed {
  font-size: var(--text-sm);
  color: var(--color-danger);
}

.analysis__findings {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
</style>
