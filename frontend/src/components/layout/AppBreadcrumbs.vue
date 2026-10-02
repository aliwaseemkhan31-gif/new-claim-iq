<script setup>
import { computed } from 'vue'

import { useRoute } from 'vue-router'

import { useProjectsStore } from '@/stores/projects'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const projects = useProjectsStore()
const ui = useUiStore()

/**
 * Crumbs come from the matched route chain, so adding a route adds its crumb.
 *
 * Two things a route cannot say for itself:
 *
 *   - its parent. A detail route is a direct child of the shell, so the
 *     matched chain alone yields `Home › Document` and the register the
 *     document belongs to never appears. `meta.parent` supplies it.
 *   - its leaf's name. The route can only offer "Document"; the title belongs
 *     to a record that loads later. The view puts the real name in the store
 *     and it replaces the generic label on the last crumb.
 */
const crumbs = computed(() => {
  const items = [{ label: 'Home', to: { name: 'dashboard' } }]

  for (const record of route.matched) {
    const meta = record.meta || {}
    if (!meta.title && !meta.breadcrumb) continue

    for (const parent of [meta.parent ?? []].flat()) {
      items.push({
        label: parent.label,
        // Params are passed through so a parent on a dynamic path resolves;
        // vue-router drops the ones its path does not use.
        to: { name: parent.name, params: route.params },
      })
    }

    // The project workspace labels itself with the project's name, which is
    // only known once the project has loaded.
    if (record.path === '/projects/:projectId') {
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

  // Drop a crumb that repeats the one before it — a register that is also
  // its own declared parent, which would otherwise read "Documents ›
  // Documents".
  const deduped = items.filter((item, index) => index === 0 || item.label !== items[index - 1].label)

  const leaf = ui.breadcrumbLeaf
  return deduped.map((item, index) => {
    const current = index === deduped.length - 1
    return {
      ...item,
      current,
      label: current && leaf ? leaf : item.label,
    }
  })
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

/*
 * Narrow screens keep the parent as well as the leaf. Trimming to the leaf
 * alone left nothing in the trail to click, on exactly the widths where the
 * sidebar is a drawer and there is no other way up.
 */
@media (max-width: 780px) {
  .crumbs__item:nth-last-child(n + 3) {
    display: none;
  }
}
</style>
