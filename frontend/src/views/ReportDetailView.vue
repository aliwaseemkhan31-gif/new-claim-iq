<script setup>
import { onMounted, ref } from 'vue'

import * as reportsApi from '@/api/reports'
import AppButton from '@/components/common/AppButton.vue'
import BackLink from '@/components/common/BackLink.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import ReportDocumentView from '@/components/domain/ReportDocumentView.vue'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/utils/format'

/**
 * One generated report, as it was when it was generated.
 */
const props = defineProps({
  reportId: { type: String, required: true },
})

const ui = useUiStore()

const report = ref(null)
const loading = ref(true)
const error = ref(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    report.value = await reportsApi.fetchReport(props.reportId)
    ui.setBreadcrumbLeaf(report.value.title || report.value.report_type_label)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div v-if="loading" class="stack gap-4">
      <LoadingSkeleton variant="text" :rows="2" />
      <LoadingSkeleton variant="block" height="420px" />
    </div>

    <ErrorState v-else-if="error" :error="error" title="Could not open this report" @retry="load" />

    <template v-else-if="report">
      <header class="row between gap-3 wrap">
        <div>
          <div class="row gap-2 wrap">
            <BackLink :fallback-to="{ name: 'reports' }" fallback-label="Reports" />
            <h1 class="report__title">{{ report.title || report.report_type_label }}</h1>
          </div>
          <p class="text-xs text-muted">
            Version {{ report.version_number }} · generated {{ formatDateTime(report.created_at) }}
            <template v-if="report.generated_by">by {{ report.generated_by }}</template>
          </p>
        </div>

        <div class="row gap-2">
          <AppButton
            v-for="format in report.formats"
            :key="format"
            size="sm"
            variant="ghost"
            icon="pi pi-download"
            :label="format.toUpperCase()"
            :href="reportsApi.reportDownloadUrl(report.id, format)"
          />
        </div>
      </header>

      <div class="surface report__sheet">
        <ReportDocumentView :document="report.document" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.report__title {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
}

.report__sheet {
  padding: var(--space-6);
}
</style>
