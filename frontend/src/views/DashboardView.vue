<script setup>
import { computed, onMounted } from 'vue'

import * as dashboardApi from '@/api/dashboard'
import * as healthApi from '@/api/health'
import AppCard from '@/components/common/AppCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useAuthStore } from '@/stores/auth'
import { EM_DASH, formatNumber, formatRelative } from '@/utils/format'

/**
 * What is happening across the projects this user can see.
 *
 * Every figure is a count the API computed from records; a figure the user may
 * not see is absent rather than zero. Notice deadlines are computed from
 * recorded dates, not predicted.
 */
const auth = useAuthStore()

const dashboard = useAsyncData(({ signal }) => dashboardApi.fetchDashboard({ signal }))
const health = useAsyncData(({ signal }) => healthApi.fetchReadiness({ signal }))

const data = computed(() => dashboard.data.value)

const tiles = computed(() => {
  const value = data.value
  if (!value) return []
  return [
    { label: 'Projects', value: value.projects.total, icon: 'pi pi-folder', to: { name: 'projects' } },
    { label: 'Documents', value: value.documents.total, icon: 'pi pi-file', to: { name: 'documents' } },
    { label: 'Claims', value: value.claims.total, icon: 'pi pi-briefcase', to: { name: 'claims' } },
    {
      label: 'Findings to review',
      value: value.analysis.findings_unreviewed,
      icon: 'pi pi-sparkles',
      tone: value.analysis.findings_unreviewed > 0 ? 'warning' : null,
    },
  ]
})

const deadlines = computed(() => data.value?.notices?.items ?? [])

/** Why the list is empty, which is not the same as "nothing is due". */
const deadlineBasis = computed(() => {
  const notices = data.value?.notices ?? {}
  const computedCount = notices.claims_computed ?? 0
  const skipped = notices.claims_without_edition ?? 0
  const parts = [`Computed for ${computedCount} claim(s) with a recorded awareness date.`]
  if (skipped) {
    parts.push(
      `${skipped} open claim(s) were not computed because their project has not declared ` +
        'which conditions of contract govern it.',
    )
  }
  return parts.join(' ')
})
const activity = computed(() => data.value?.recent_activity ?? [])
const knowledge = computed(() => data.value?.knowledge ?? null)

const documentsProcessing = computed(() => {
  const rows = data.value?.documents?.by_status ?? []
  return rows.filter((row) => ['queued', 'processing', 'pending'].includes(row.value))
})

const documentsFailed = computed(
  () => (data.value?.documents?.by_status ?? []).find((row) => row.value === 'failed')?.count ?? 0
)

const checks = computed(() => {
  const payload = health.data.value
  if (!payload?.checks || typeof payload.checks !== 'object') return []
  return Object.entries(payload.checks).map(([name, value]) => {
    if (typeof value !== 'object' || value === null) return { name, status: 'unknown', detail: null }
    const status = value.ok === true ? 'ok' : value.ok === false ? 'failed' : 'unknown'
    const detail =
      value.error ??
      (value.version ? 'v' + value.version : null) ??
      (typeof value.latency_ms === 'number' ? value.latency_ms + ' ms' : null)
    return { name, status, detail }
  })
})

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
})

function deadlineTone(days) {
  if (days < 0) return 'danger'
  if (days <= 7) return 'warning'
  return 'neutral'
}

function deadlineLabel(days) {
  if (days < 0) return Math.abs(days) + ' day(s) overdue'
  if (days === 0) return 'Due today'
  return 'Due in ' + days + ' day(s)'
}

const ACTIVITY_ICONS = {
  document_uploaded: 'pi pi-file',
  claim_created: 'pi pi-briefcase',
  report_generated: 'pi pi-chart-bar',
  analysis_completed: 'pi pi-sparkles',
  analysis_partial: 'pi pi-sparkles',
  analysis_failed: 'pi pi-exclamation-triangle',
}

function load() {
  dashboard.execute()
  health.execute()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader
      :title="greeting + (auth.user?.full_name ? ', ' + auth.user.full_name.split(' ')[0] : '')"
      description="Across the projects you can see. Notice deadlines are computed from recorded dates."
    >
      <template #actions>
        <RouterLink :to="{ name: 'projects' }" class="text-sm">All projects</RouterLink>
      </template>
    </PageHeader>

    <div v-if="dashboard.loading.value && !data" class="grid-metrics">
      <LoadingSkeleton variant="metrics" :columns="4" />
    </div>

    <ErrorState
      v-else-if="dashboard.error.value"
      :error="dashboard.error.value"
      title="Could not load the dashboard"
      @retry="load"
    />

    <template v-else-if="data">
      <div class="grid-metrics">
        <component
          :is="tile.to ? 'RouterLink' : 'div'"
          v-for="tile in tiles"
          :key="tile.label"
          :to="tile.to"
          class="tile surface"
        >
          <div class="row between">
            <span class="text-overline">{{ tile.label }}</span>
            <i :class="tile.icon" class="tile__icon" aria-hidden="true" />
          </div>
          <p class="tile__value" :class="tile.tone ? 'tile__value--' + tile.tone : null">
            {{ tile.value === null || tile.value === undefined ? EM_DASH : formatNumber(tile.value) }}
          </p>
        </component>
      </div>

      <div class="dashboard__grid">
        <AppCard title="Notice deadlines" subtitle="Requirements with no recorded notice">
          <EmptyState
            v-if="!deadlines.length"
            compact
            icon="pi pi-calendar"
            title="No deadlines within 30 days"
            :description="deadlineBasis"
          />
          <ul v-else class="dashboard__list">
            <li v-for="item in deadlines" :key="item.claim_id + item.clause_number" class="dashboard__row">
              <RouterLink :to="{ name: 'claim-detail', params: { claimId: item.claim_id }, query: { tab: 'notices' } }" class="grow truncate">
                {{ item.claim_reference || item.claim_title }} — Clause {{ item.clause_number }}
              </RouterLink>
              <StatusBadge :tone="deadlineTone(item.days_remaining)" :label="deadlineLabel(item.days_remaining)" size="sm" />
            </li>
          </ul>
          <template #footer>
            <p class="text-xs text-muted">{{ data.notices.basis }}</p>
          </template>
        </AppCard>

        <AppCard title="Documents" subtitle="Ingestion across your projects">
          <dl class="dashboard__facts">
            <div><dt>Pages</dt><dd>{{ formatNumber(data.documents.pages) }}</dd></div>
            <div><dt>OCR pages</dt><dd>{{ formatNumber(data.documents.ocr_pages) }}</dd></div>
            <div><dt>Processing</dt><dd>{{ documentsProcessing.reduce((sum, row) => sum + row.count, 0) }}</dd></div>
            <div><dt>Failed</dt><dd :class="documentsFailed ? 'text-danger' : null">{{ documentsFailed }}</dd></div>
            <div><dt>Low quality</dt><dd>{{ data.documents.low_quality }}</dd></div>
          </dl>
        </AppCard>

        <AppCard title="Standard forms" subtitle="What AI answers can cite">
          <ul v-if="knowledge && knowledge.bases.length" class="dashboard__list">
            <li v-for="base in knowledge.bases" :key="base.id" class="dashboard__row">
              <RouterLink :to="{ name: 'knowledge-base-detail', params: { knowledgeBaseId: base.id } }" class="grow truncate">
                {{ base.edition_label }}
              </RouterLink>
              <StatusBadge :status="base.status" size="sm" />
            </li>
          </ul>
          <EmptyState
            v-else
            compact
            icon="pi pi-book"
            title="No standard form ingested"
            description="Questions cannot cite contract text until an edition is published."
          />
          <p v-if="knowledge && knowledge.projects_without_standard_form_text" class="text-xs text-warning">
            {{ knowledge.projects_without_standard_form_text }} project(s) declare an edition with no
            published text.
          </p>
        </AppCard>

        <AppCard title="Claims" subtitle="By status">
          <ul v-if="data.claims.total" class="dashboard__list">
            <li v-for="row in data.claims.by_status" :key="row.value" class="dashboard__row">
              <span class="grow">{{ row.label }}</span>
              <span class="text-numeric">{{ row.count }}</span>
            </li>
          </ul>
          <EmptyState v-else compact icon="pi pi-briefcase" title="No claims yet" />
          <template v-if="data.claims.amount_claimed.length" #footer>
            <p class="text-xs text-muted">
              Claimed:
              <span v-for="row in data.claims.amount_claimed" :key="row.currency || 'none'">
                {{ row.currency }} {{ formatNumber(Number(row.total)) }}
              </span>
            </p>
          </template>
        </AppCard>

        <AppCard title="Recent activity" flush>
          <EmptyState v-if="!activity.length" compact icon="pi pi-clock" title="Nothing recent" />
          <ul v-else class="dashboard__activity">
            <li v-for="(item, index) in activity" :key="index" class="dashboard__row">
              <i :class="ACTIVITY_ICONS[item.kind] || 'pi pi-circle'" aria-hidden="true" />
              <RouterLink :to="item.link" class="grow truncate">{{ item.title }}</RouterLink>
              <span class="text-xs text-muted">{{ formatRelative(item.at) }}</span>
            </li>
          </ul>
        </AppCard>

        <AppCard title="System" subtitle="This installation">
          <ul class="dashboard__list">
            <li v-for="check in checks" :key="check.name" class="dashboard__row">
              <span class="grow">{{ check.name }}</span>
              <span class="text-xs text-muted">{{ check.detail }}</span>
              <StatusBadge :status="check.status" size="sm" />
            </li>
          </ul>
        </AppCard>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dashboard__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--space-4);
}

.tile {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
}

.tile__icon {
  color: var(--color-text-muted);
}

.tile__value {
  font-size: var(--text-2xl);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.tile__value--warning {
  color: var(--color-warning);
}

.dashboard__list,
.dashboard__activity {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.dashboard__row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  font-size: var(--text-sm);
  border-bottom: 1px solid var(--color-border-subtle);
}

.dashboard__activity .dashboard__row {
  padding: var(--space-2) var(--space-4);
  border-bottom: 1px solid var(--color-border-subtle);
}

.dashboard__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: var(--space-3);
  margin: 0;
}

.dashboard__facts dt {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.dashboard__facts dd {
  margin: 0;
  font-size: var(--text-lg);
  font-variant-numeric: tabular-nums;
}
</style>
