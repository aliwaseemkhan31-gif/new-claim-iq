<script setup>
import { computed, onMounted, ref } from 'vue'

import { useRouter } from 'vue-router'

import * as reportsApi from '@/api/reports'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * Generated reports.
 *
 * Each report is a snapshot: it freezes the claim, its evidence, the computed
 * notice timing and the AI findings with their review state at the moment it
 * was generated. Regenerating produces a new version rather than changing an
 * existing one.
 */
const props = defineProps({
  projectId: { type: String, default: null },
  claimId: { type: String, default: null },
  showProject: { type: Boolean, default: false },
})

const router = useRouter()
const auth = useAuthStore()
const ui = useUiStore()

const rows = ref([])
const loading = ref(true)
const error = ref(null)
const generating = ref('')

const canGenerate = computed(
  () => Boolean(props.projectId) && auth.canInProject(props.projectId, 'report.generate')
)

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await reportsApi.listReports({
      project: props.projectId || undefined,
      claim: props.claimId || undefined,
      page_size: 50,
    })
    rows.value = rowsOf(data)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

async function generate(reportType) {
  generating.value = reportType
  try {
    const report = await reportsApi.generateReport({
      reportType,
      projectId: props.projectId,
      claimId: reportType === 'claim_assessment' ? props.claimId : null,
    })
    ui.notifySuccess('Report generated', 'Version ' + report.version_number + '.')
    router.push({ name: 'report-detail', params: { reportId: report.id } })
  } catch (err) {
    ui.notifyError(err, 'Could not generate the report')
  } finally {
    generating.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="stack gap-4">
    <div v-if="canGenerate" class="toolbar between">
      <p class="text-xs text-muted">
        A report freezes what the record says now, including which AI findings have been reviewed.
      </p>
      <div class="row gap-2">
        <AppButton
          v-if="claimId"
          variant="primary"
          size="sm"
          icon="pi pi-file-export"
          label="Claim assessment"
          :loading="generating === 'claim_assessment'"
          @click="generate('claim_assessment')"
        />
        <AppButton
          v-if="!claimId"
          variant="primary"
          size="sm"
          icon="pi pi-file-export"
          label="Claims register"
          :loading="generating === 'claims_register'"
          @click="generate('claims_register')"
        />
      </div>
    </div>

    <div class="surface reports__panel">
      <div v-if="loading" class="reports__pad">
        <LoadingSkeleton variant="table" :rows="4" :columns="4" />
      </div>

      <ErrorState v-else-if="error" :error="error" title="Could not load reports" @retry="load" />

      <EmptyState
        v-else-if="!rows.length"
        icon="pi pi-chart-bar"
        title="No reports generated"
        description="A claim assessment sets out the notice position, the evidence, the analysis and the determination in the order a formal response follows."
      />

      <div v-else class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th scope="col">Report</th>
              <th v-if="showProject" scope="col">Project</th>
              <th scope="col" class="cell-numeric">Version</th>
              <th scope="col">Generated</th>
              <th scope="col">By</th>
              <th scope="col"><span class="sr-only">Download</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="report in rows" :key="report.id">
              <td>
                <RouterLink :to="{ name: 'report-detail', params: { reportId: report.id } }">
                  {{ report.title }}
                </RouterLink>
                <p class="text-xs text-muted">{{ report.report_type_label }}</p>
              </td>
              <td v-if="showProject" class="text-xs">{{ report.project_name }}</td>
              <td class="cell-numeric">{{ report.version_number }}</td>
              <td>{{ formatDateTime(report.created_at) }}</td>
              <td class="text-xs">{{ report.generated_by }}</td>
              <td>
                <div class="row gap-1">
                  <AppButton
                    v-for="format in report.formats"
                    :key="format"
                    size="sm"
                    variant="ghost"
                    :label="format.toUpperCase()"
                    icon="pi pi-download"
                    :href="reportsApi.reportDownloadUrl(report.id, format)"
                  />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reports__panel {
  overflow: hidden;
}

.reports__pad {
  padding: var(--space-4);
}
</style>
