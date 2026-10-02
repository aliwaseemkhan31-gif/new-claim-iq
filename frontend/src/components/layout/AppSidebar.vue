<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useRoute } from 'vue-router'

import { NAV_GROUPS } from '@/constants/navigation'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const ui = useUiStore()
const auth = useAuthStore()
const projects = useProjectsStore()

const collapsed = computed(() => ui.sidebarCollapsed)

/**
 * Below the drawer breakpoint the sidebar is an overlay, open only when the
 * user asks for it.
 *
 * It used to be forced to the collapsed width by a media query, which
 * silently overrode the store: the topbar's toggle flipped a flag that
 * changed nothing on screen, and the labels stayed hidden. A user on a narrow
 * window was left with unlabelled icons, no breadcrumb trail and no working
 * control — no way to read where they were or to get anywhere else.
 */
const DRAWER_QUERY = '(max-width: 900px)'

const isDrawer = ref(false)
const panel = ref(null)

const drawerOpen = computed(() => isDrawer.value && ui.mobileNavOpen)

/**
 * Icons only, no labels.
 *
 * Never in the drawer, whatever the remembered desktop preference says. The
 * labels are rendered conditionally, so CSS alone could not bring them back:
 * someone who had collapsed the sidebar on a wide screen would open the
 * drawer on a narrow one and find the same unreadable strip of icons.
 */
const compact = computed(() => !isDrawer.value && collapsed.value)

/**
 * Closed, the drawer is off-screen but still in the document, so Tab would
 * walk into navigation nobody can see. `inert` takes it out of the tab order
 * and off the accessibility tree without removing it, which keeps the slide
 * transition.
 */
const hidden = computed(() => isDrawer.value && !ui.mobileNavOpen)

let media = null

function onMediaChange(event) {
  isDrawer.value = event.matches
  // Widening past the breakpoint turns the drawer back into a sidebar; a
  // flag left set would reopen it on the next narrow resize.
  if (!event.matches) ui.setMobileNavOpen(false)
}

function onWindowKeydown(event) {
  // Not bound to the panel: the drawer is opened from the topbar, so focus is
  // still out there and a key handler on the panel would never see Escape.
  if (event.key === 'Escape' && drawerOpen.value) ui.setMobileNavOpen(false)
}

onMounted(() => {
  if (typeof window.matchMedia === 'function') {
    media = window.matchMedia(DRAWER_QUERY)
    isDrawer.value = media.matches
    media.addEventListener('change', onMediaChange)
  }
  window.addEventListener('keydown', onWindowKeydown)
})

onBeforeUnmount(() => {
  media?.removeEventListener('change', onMediaChange)
  window.removeEventListener('keydown', onWindowKeydown)
})

// A drawer that survives the navigation it started covers the screen the user
// asked for.
watch(
  () => route.fullPath,
  () => ui.setMobileNavOpen(false)
)

// Opening it moves focus in, so the keyboard reaches the links it just
// revealed rather than staying on the toggle behind the scrim.
watch(drawerOpen, (open) => {
  if (open) panel.value?.querySelector('.sidebar__item')?.focus()
})

/** Hide what the user cannot use rather than showing a locked door. */
const groups = computed(() =>
  NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => !item.permission || auth.hasPermission(item.permission)),
  })).filter((group) => group.items.length > 0)
)

function isActive(item) {
  if (item.match) return route.path === item.match || route.path.startsWith(`${item.match}/`)
  return route.name === item.to.name
}

const activeProject = computed(() => projects.activeProject)
</script>

<template>
  <div
    v-if="drawerOpen"
    class="sidebar__scrim"
    aria-hidden="true"
    @click="ui.setMobileNavOpen(false)"
  />

  <aside
    ref="panel"
    class="sidebar"
    :class="{ 'sidebar--collapsed': compact, 'sidebar--drawer-open': drawerOpen }"
    aria-label="Primary"
    :inert="hidden || undefined"
  >
    <div class="sidebar__brand">
      <div class="sidebar__mark" aria-hidden="true">CQ</div>
      <div v-if="!compact" class="sidebar__wordmark">
        <span class="sidebar__product">ClaimIQ</span>
        <span class="sidebar__edition">Enterprise</span>
      </div>
    </div>

    <nav class="sidebar__nav scroll-y">
      <div v-for="group in groups" :key="group.id" class="sidebar__group">
        <p v-if="group.label && !compact" class="sidebar__group-label">{{ group.label }}</p>
        <div v-else-if="group.label" class="sidebar__group-rule" />

        <RouterLink
          v-for="item in group.items"
          :key="item.label"
          :to="item.to"
          class="sidebar__item"
          :class="{ 'is-active': isActive(item) }"
          :title="compact ? item.label : undefined"
        >
          <i :class="item.icon" class="sidebar__icon" aria-hidden="true" />
          <span v-if="!compact" class="sidebar__label">{{ item.label }}</span>
        </RouterLink>
      </div>
    </nav>

    <div v-if="activeProject && !compact" class="sidebar__context">
      <p class="text-overline">Active project</p>
      <RouterLink
        :to="{ name: 'project-overview', params: { projectId: activeProject.id } }"
        class="sidebar__context-name truncate"
      >
        {{ activeProject.name }}
      </RouterLink>
    </div>

    <button
      type="button"
      class="sidebar__collapse"
      :aria-label="collapsed ? 'Expand sidebar' : 'Collapse sidebar'"
      :aria-expanded="!collapsed"
      @click="ui.toggleSidebar()"
    >
      <i :class="collapsed ? 'pi pi-angle-double-right' : 'pi pi-angle-double-left'" aria-hidden="true" />
      <span v-if="!collapsed">Collapse</span>
    </button>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: var(--sidebar-width);
  height: 100%;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  transition: width var(--duration-normal) var(--ease-standard);
  flex-shrink: 0;
}

.sidebar--collapsed {
  width: var(--sidebar-width-collapsed);
}

/* Brand */
.sidebar__brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: var(--topbar-height);
  padding: 0 var(--space-3);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.sidebar__mark {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  font-size: var(--text-2xs);
  font-weight: var(--weight-bold);
  letter-spacing: 0.02em;
  color: var(--color-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.sidebar__wordmark {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
  overflow: hidden;
}

.sidebar__product {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.sidebar__edition {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

/* Nav */
.sidebar__nav {
  flex: 1 1 auto;
  min-height: 0;
  padding: var(--space-3) var(--space-2);
}

.sidebar__group + .sidebar__group {
  margin-top: var(--space-4);
}

.sidebar__group-label {
  padding: 0 var(--space-2);
  margin-bottom: var(--space-1);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.sidebar__group-rule {
  height: 1px;
  margin: var(--space-2) var(--space-2) var(--space-2);
  background: var(--color-border-subtle);
}

.sidebar__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 30px;
  padding: 0 var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
}

.sidebar__item:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
  text-decoration: none;
}

.sidebar__item.is-active {
  color: var(--color-accent);
  background: var(--color-surface-selected);
}

.sidebar__icon {
  width: 16px;
  font-size: 14px;
  text-align: center;
  flex-shrink: 0;
}

.sidebar--collapsed .sidebar__item {
  justify-content: center;
  padding: 0;
}

/* Active project context */
.sidebar__context {
  padding: var(--space-3);
  margin: 0 var(--space-2) var(--space-2);
  background: var(--color-surface-sunken);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-md);
}

.sidebar__context-name {
  display: block;
  margin-top: 2px;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--color-text);
}

/* Collapse control */
.sidebar__collapse {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 34px;
  padding: 0 var(--space-4);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
}

.sidebar--collapsed .sidebar__collapse {
  justify-content: center;
  padding: 0;
}

.sidebar__collapse:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.sidebar__scrim {
  display: none;
}

/*
 * Drawer, not a narrower sidebar. Labels stay: the point of opening it is to
 * read where things are, and at this width it is the only navigation there
 * is.
 */
@media (max-width: 900px) {
  .sidebar__scrim {
    display: block;
    position: fixed;
    inset: 0;
    z-index: var(--z-dropdown);
    background: rgb(0 0 0 / 45%);
  }

  /* `sidebar--collapsed` is never set at this width, so plain `.sidebar` is
     the whole story here. */
  .sidebar {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: var(--z-modal);
    width: var(--sidebar-width);
    transform: translateX(-100%);
    transition: transform var(--duration-normal) var(--ease-standard);
    box-shadow: var(--shadow-lg);
  }

  .sidebar--drawer-open {
    transform: translateX(0);
  }

  /* The desktop width preference has no meaning for a drawer. */
  .sidebar__collapse {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .sidebar {
    transition: none;
  }
}
</style>
