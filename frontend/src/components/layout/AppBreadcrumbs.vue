<script setup>
import { computed } from 'vue'

import { useRoute } from 'vue-router'

import { useProjectsStore } from '@/stores/projects'

const route = useRoute()
const projects = useProjectsStore()

/**
 * Crumbs come from the matched route chain, so adding a route adds its crumb.
 * The project workspace is the one special case: its label is the project
 * name, which is only known once the project has loaded.
 */
const crumbs = computed(() => {
  const items = [{ label: 'Home', to: { name: 'dashboard' } }]

  for (const record of route.matched) {
    const meta = record.meta || {}
    if (!meta.title && !meta.breadcrumb) continue

    const isProjectRoot = record.path === '/projects/:projectId'
    if (isProjectRoot) {
      items.push({ label: 'Projects', to: { name: 'projects' } })
      const name = projects.activeProject?.name
      items.push({
        label: name || `Project ${route.params.projectId}`,
        to: { name: 'project-overview', params: { projectId: route.params.projectId } },
        pending: !name,
      })
      continue
    }

    items.push({
      label: meta.breadcrumb || meta.title,
      to: record.name ? { name: record.name, params: route.params } : null,
    })
  }

  // De-duplicate consecutive repeats (e.g. "Home" then "Dashboard" at root).
  const deduped = items.filter((item, index) => index === 0 || item.label !== items[index - 1].label)
  return deduped.map((item, index) => ({ ...item, current: index === deduped.length - 1 }))
})
</script>

<template>
  <nav class="crumbs" aria-label="Breadcrumb">
    <ol class="crumbs__list">
      <li v-for="(crumb, index) in crumbs" :key="`${crumb.label}-${index}`" class="crumbs__item">
        <i v-if="index > 0" class="pi pi-angle-right crumbs__sep" aria-hidden="true" />
        <span v-if="crumb.current" class="crumbs__current" aria-current="page">
          {{ crumb.label }}
        </span>
        <RouterLink v-else-if="crumb.to" :to="crumb.to" class="crumbs__link">
          {{ crumb.label }}
        </RouterLink>
        <span v-else class="crumbs__link">{{ crumb.label }}</span>
      </li>
    </ol>
  </nav>
</template>

<style scoped>
.crumbs {
  min-width: 0;
  overflow: hidden;
}

.crumbs__list {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.crumbs__item {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
}

.crumbs__sep {
  font-size: 10px;
  color: var(--color-text-disabled);
}

.crumbs__link {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  white-space: nowrap;
}

.crumbs__link:hover {
  color: var(--color-text-secondary);
  text-decoration: none;
}

.crumbs__current {
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The trail is redundant on narrow screens where the page title is visible. */
@media (max-width: 780px) {
  .crumbs__item:not(:last-child) {
    display: none;
  }
}
</style>
