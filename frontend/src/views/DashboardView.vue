<script setup>
import { computed, onMounted } from 'vue'

import * as healthApi from '@/api/health'
import * as projectsApi from '@/api/projects'
import AppCard from '@/components/common/AppCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useAuthStore } from '@/stores/auth'
import { formatRelative } from '@/utils/format'

const auth = useAuthStore()

/**
 * Two independent reads. They fail independently too — a dead health endpoint
 * must not blank the project list, and vice versa.
 *
 * Nothing on this page is invented. Where the backend has no endpoint yet the
 * card says so; it does not show a plausible-looking number.
 */
const projects = useAsyncData(
  ({ signal }) => projectsApi.listProjects({ ordering: '-updated_at', page_size: 6 }, { signal }),
  { isEmpty: (data) => (Array.isArray(data) ? data : (data?.results ?? [])).length === 0 }
)

const health = useAsyncData(({ signal }) => healthApi.fetchReadiness({ signal }))

const recentProjects = computed(() => {
  const value = projects.data.value
  if (!value) return []
  return Array.isArray(value) ? value : (value.results ?? [])
})

/**
 * Readiness returns:
 *   { status, checks: { database: { ok, latency_ms }, pgvector: { ok, version }, ... } }
 *
 * Each check reports a boolean `ok` plus check-specific detail. An unreadable
 * shape maps to 'unknown' rather than to 'ok' — a check we cannot parse has not
 * passed, and defaulting it green would hide a real outage behind a healthy
 * dashboard.
 */
const checks = computed(() => {
  const payload = health.data.value
  if (!payload?.checks || typeof payload.checks !== 'object') return []
  return Object.entries(payload.checks).map(([name, value]) => {
    if (typeof value !== 'object' || value === null) {
      return { name, status: 'unknown', detail: null }
    }
    const status = value.ok === true ? 'ok' : value.ok === false ? 'failed' : 'unknown'
    const detail =
      value.error ??
      (value.version ? `v${value.version}` : null) ??
      (typeof value.latency_ms === 'number' ? `${value.latency_ms} ms` : null) ??
      (typeof value.pending === 'number' && value.pending > 0
        ? `${value.pending} pending`
        : null)
    return { name, status, detail }
  })
})

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
})

function load() {
  projects.execute()
  health.execute()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader
      :title="`${greeting}${auth.user?.first_name ? ', ' + auth.user.first_name : ''}`"
      description="Recent activity across your projects, and the health of this installation."
    >
      <template #actions>
        <RouterLink :to="{ name: 'projects' }" class="dashboard__link">
          All projects
          <i class="pi pi-arrow-right" aria-hidden="true" />
        </RouterLink>
      </template>
    </PageHeader>

    <div class="dashboard__grid">
      <!-- Recent projects -->
      <AppCard title="Recent projects" subtitle="Ordered by last activity" flush>
        <template #actions>
          <button
            type="button"
            class="dashboard__refresh"
            aria-label="Refresh projects"
            @click="projects.execute()"
          >
            <i class="pi pi-refresh" aria-hidden="true" />
          </button>
        </template>

        <div v-if="projects.loading.value" class="dashboard__pad">
          <LoadingSkeleton variant="text" :rows="4" />
        </div>

        <ErrorState
          v-else-if="projects.error.value"
          compact
          :error="projects.error.value"
          title="Could not load projects"
          @retry="projects.execute()"
        />

        <EmptyState
          v-else-if="projects.isEmpty.value"
          compact
          icon="pi pi-folder-open"
          title="No projects yet"
          description="A project holds a contract, its documents and the claims raised under it. Create one to begin."
          :hint="auth.hasPermission('org.projects.manage') ? null : 'Requires the “Manage projects” permission.'"
        />

        <ul v-else class="dashboard__list">
          <li v-for="project in recentProjects" :key="project.id">
            <RouterLink
              :to="{ name: 'project-overview', params: { projectId: project.id } }"
              class="dashboard__row"
            >
              <div class="grow">
                <p class="dashboard__row-title truncate">{{ project.name }}</p>
                <p class="dashboard__row-meta truncate">
                  <span v-if="project.contract_reference">{{ project.contract_reference }}</span>
                  <span v-if="project.contract_reference && project.updated_at"> · </span>
                  <span v-if="project.updated_at">Updated {{ formatRelative(project.updated_at) }}</span>
                </p>
              </div>
              <StatusBadge v-if="project.status" :status="project.status" size="sm" />
            </RouterLink>
          </li>
        </ul>
      </AppCard>

      <!-- System status -->
      <AppCard title="System status" subtitle="This installation">
        <div v-if="health.loading.value">
          <LoadingSkeleton variant="text" :rows="4" />
        </div>

        <ErrorState
          v-else-if="health.error.value"
          compact
          :error="health.error.value"
          title="Health checks unavailable"
          @retry="health.execute()"
        />

        <div v-else-if="checks.length === 0" class="dashboard__note">
          <p>The readiness endpoint returned no subsystem checks.</p>
        </div>

        <ul v-else class="dashboard__checks">
          <li v-for="check in checks" :key="check.name" class="dashboard__check">
            <span class="dashboard__check-name">{{ check.name }}</span>
            <StatusBadge :status="check.status" size="sm" />
          </li>
        </ul>
      </AppCard>
    </div>

    <!--
      Portfolio metrics deliberately show no numbers until the aggregate
      endpoint exists. A dashboard that guesses is worse than one that admits
      it does not know.
    -->
    <AppCard title="Portfolio metrics">
      <EmptyState
        compact
        icon="pi pi-chart-bar"
        title="Metrics not available yet"
        description="Claim value, exposure and cycle-time aggregates will appear here once the analytics endpoint is implemented. No figures are shown until they can be computed from your data."
      />
    </AppCard>
  </div>
</template>

<style scoped>
.dashboard__grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr);
  gap: var(--space-4);
  align-items: start;
}

@media (max-width: 1000px) {
  .dashboard__grid {
    grid-template-columns: 1fr;
  }
}

.dashboard__link {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.dashboard__refresh {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  font-size: 12px;
  color: var(--color-text-muted);
  border-radius: var(--radius-sm);
}

.dashboard__refresh:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.dashboard__pad {
  padding: var(--space-4);
}

.dashboard__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.dashboard__list li + li {
  border-top: 1px solid var(--color-border-subtle);
}

.dashboard__row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  color: inherit;
}

.dashboard__row:hover {
  background: var(--color-surface-hover);
  text-decoration: none;
}

.dashboard__row-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.dashboard__row-meta {
  margin-top: 1px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.dashboard__checks {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.dashboard__check {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.dashboard__check-name {
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  text-transform: capitalize;
}

.dashboard__note {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
</style>
