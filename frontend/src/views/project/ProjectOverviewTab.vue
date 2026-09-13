<script setup>
import { computed, onMounted } from 'vue'

import * as projectsApi from '@/api/projects'
import AppCard from '@/components/common/AppCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { EM_DASH, formatDate, formatNumber } from '@/utils/format'

const props = defineProps({
  project: { type: Object, required: true },
})

const summary = useAsyncData(({ signal }) =>
  projectsApi.fetchProjectSummary(props.project.id, { signal })
)

/**
 * Counts render only when the API supplied them. A tile whose value is
 * missing shows an em dash, never a zero — "no documents" and "we do not
 * know how many documents" are different answers.
 */
const tiles = computed(() => {
  const data = summary.data.value
  if (!data) return []
  return [
    { label: 'Documents', value: data.document_count, icon: 'pi pi-file' },
    { label: 'Claims', value: data.claim_count, icon: 'pi pi-briefcase' },
    { label: 'Correspondence', value: data.correspondence_count, icon: 'pi pi-envelope' },
    { label: 'Evidence items', value: data.evidence_count, icon: 'pi pi-paperclip' },
  ]
})

const parties = computed(() => [
  { label: 'Employer', value: props.project.employer_name },
  { label: 'Contractor', value: props.project.contractor_name },
  { label: 'Engineer', value: props.project.engineer_name },
  { label: 'Sub-contractors', value: props.project.subcontractor_names },
])

const contract = computed(() => [
  { label: 'Reference', value: props.project.contract_reference },
  { label: 'Form of contract', value: props.project.contract_form },
  { label: 'Governing law', value: props.project.governing_law },
  { label: 'Commencement', value: formatDate(props.project.commencement_date) },
  { label: 'Completion', value: formatDate(props.project.completion_date) },
])

onMounted(() => summary.execute())
</script>

<template>
  <div class="page">
    <div v-if="summary.loading.value">
      <LoadingSkeleton variant="metrics" :columns="4" />
    </div>

    <ErrorState
      v-else-if="summary.error.value"
      compact
      :error="summary.error.value"
      title="Could not load the project summary"
      @retry="summary.execute()"
    />

    <div v-else-if="tiles.length > 0" class="grid-metrics">
      <div v-for="tile in tiles" :key="tile.label" class="tile">
        <div class="row between">
          <span class="text-overline">{{ tile.label }}</span>
          <i :class="tile.icon" class="tile__icon" aria-hidden="true" />
        </div>
        <p class="tile__value">
          {{ tile.value === null || tile.value === undefined ? EM_DASH : formatNumber(tile.value) }}
        </p>
      </div>
    </div>

    <AppCard v-else title="Project metrics">
      <EmptyState
        compact
        icon="pi pi-chart-bar"
        title="No summary available"
        description="The project summary endpoint returned no counts. Figures appear here once documents and claims exist in this project."
      />
    </AppCard>

    <div class="overview__columns">
      <AppCard title="Contract">
        <dl class="facts">
          <div v-for="row in contract" :key="row.label" class="facts__row">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value || EM_DASH }}</dd>
          </div>
        </dl>
      </AppCard>

      <AppCard title="Parties">
        <dl class="facts">
          <div v-for="row in parties" :key="row.label" class="facts__row">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value || EM_DASH }}</dd>
          </div>
        </dl>
      </AppCard>
    </div>

    <AppCard v-if="project.description" title="Description">
      <p class="overview__description">{{ project.description }}</p>
    </AppCard>
  </div>
</template>

<style scoped>
.tile {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.tile__icon {
  font-size: 13px;
  color: var(--color-text-muted);
}

.tile__value {
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  letter-spacing: var(--tracking-tight);
}

.overview__columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--space-4);
  align-items: start;
}

.facts {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.facts__row {
  display: grid;
  grid-template-columns: 140px 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.facts__row dt {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.facts__row dd {
  margin: 0;
  font-size: var(--text-sm);
}

.overview__description {
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  max-width: 84ch;
}
</style>
