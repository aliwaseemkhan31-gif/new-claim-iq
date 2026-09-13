<script setup>
import { computed, onBeforeUnmount, watch } from 'vue'

import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { PROJECT_TABS } from '@/constants/navigation'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'
import { EM_DASH, formatDate } from '@/utils/format'

const props = defineProps({
  projectId: { type: String, required: true },
})

const projects = useProjectsStore()
const auth = useAuthStore()

const project = computed(() => projects.activeProject)
const loading = computed(() => projects.activeProjectLoading)
const error = computed(() => projects.activeProjectError)

/** Hide tabs the user cannot open. */
const tabs = computed(() =>
  PROJECT_TABS.filter((tab) => !tab.permission || auth.hasPermission(tab.permission))
)

async function load() {
  try {
    await projects.loadProject(props.projectId)
  } catch {
    // Surfaced through the store's error state below.
  }
}

watch(() => props.projectId, load, { immediate: true })

// The active project is ambient context for the AI workspace; clearing it on
// exit prevents a query being grounded in a project the user has left.
onBeforeUnmount(() => projects.clearActiveProject())
</script>

<template>
  <div class="workspace">
    <div v-if="loading && !project" class="workspace__loading">
      <LoadingSkeleton variant="text" :rows="2" />
    </div>

    <ErrorState
      v-else-if="error"
      :error="error"
      title="Could not open this project"
      @retry="load"
    />

    <template v-else-if="project">
      <header class="workspace__header">
        <div class="workspace__identity">
          <div class="row gap-3">
            <h1 class="workspace__title">{{ project.name }}</h1>
            <StatusBadge v-if="project.status" :status="project.status" />
          </div>

          <dl class="workspace__facts">
            <div class="workspace__fact">
              <dt>Contract</dt>
              <dd>{{ project.contract_reference || EM_DASH }}</dd>
            </div>
            <div class="workspace__fact">
              <dt>Form</dt>
              <dd>{{ project.contract_form || EM_DASH }}</dd>
            </div>
            <div class="workspace__fact">
              <dt>Employer</dt>
              <dd>{{ project.employer_name || EM_DASH }}</dd>
            </div>
            <div class="workspace__fact">
              <dt>Contractor</dt>
              <dd>{{ project.contractor_name || EM_DASH }}</dd>
            </div>
            <div class="workspace__fact">
              <dt>Commenced</dt>
              <dd>{{ formatDate(project.commencement_date) }}</dd>
            </div>
          </dl>
        </div>
      </header>

      <nav class="workspace__tabs" aria-label="Project sections">
        <RouterLink
          v-for="tab in tabs"
          :key="tab.name"
          :to="{ name: tab.name, params: { projectId } }"
          class="workspace__tab"
          active-class="is-active"
        >
          {{ tab.label }}
        </RouterLink>
      </nav>

      <div class="workspace__body scroll-y">
        <RouterView v-slot="{ Component, route }">
          <component :is="Component" :key="route.name" :project="project" />
        </RouterView>
      </div>
    </template>
  </div>
</template>

<style scoped>
.workspace {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.workspace__loading {
  padding: var(--space-6);
}

.workspace__header {
  padding: var(--space-5) var(--space-6) var(--space-4);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.workspace__title {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.workspace__facts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-6);
  margin-top: var(--space-3);
}

.workspace__fact {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.workspace__fact dt {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.workspace__fact dd {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-text);
}

.workspace__tabs {
  display: flex;
  gap: var(--space-1);
  padding: 0 var(--space-6);
  overflow-x: auto;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.workspace__tab {
  position: relative;
  padding: var(--space-3) var(--space-3);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.workspace__tab:hover {
  color: var(--color-text);
  text-decoration: none;
}

.workspace__tab.is-active {
  color: var(--color-accent);
}

.workspace__tab.is-active::after {
  content: '';
  position: absolute;
  left: var(--space-2);
  right: var(--space-2);
  bottom: -1px;
  height: 2px;
  background: var(--color-accent);
  border-radius: var(--radius-full) var(--radius-full) 0 0;
}

.workspace__body {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
