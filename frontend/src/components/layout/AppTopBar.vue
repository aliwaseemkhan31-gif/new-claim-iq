<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { useRouter } from 'vue-router'

import AppBreadcrumbs from './AppBreadcrumbs.vue'
import ThemeToggle from './ThemeToggle.vue'
import UserMenu from './UserMenu.vue'
import { useUiStore } from '@/stores/ui'

const router = useRouter()
const ui = useUiStore()

const notificationsOpen = ref(false)
const notificationsRoot = ref(null)

/**
 * Notifications are not wired to a backend feed yet. The bell shows the
 * honest state rather than a fabricated unread count — a fake "3" here would
 * be a lie the user acts on.
 */
const notifications = ref([])
const unreadCount = computed(() => notifications.value.filter((n) => !n.read).length)

const shortcutKey = computed(() =>
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '') ? '⌘' : 'Ctrl'
)

function openSearch() {
  router.push({ name: 'search' })
}

function onGlobalKeydown(event) {
  const isSearchShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k'
  if (isSearchShortcut) {
    event.preventDefault()
    openSearch()
  }
  if (event.key === 'Escape') notificationsOpen.value = false
}

function onDocumentClick(event) {
  if (
    notificationsOpen.value &&
    notificationsRoot.value &&
    !notificationsRoot.value.contains(event.target)
  ) {
    notificationsOpen.value = false
  }
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)
  document.addEventListener('click', onDocumentClick)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
  document.removeEventListener('click', onDocumentClick)
})
</script>

<template>
  <header class="topbar">
    <button
      type="button"
      class="topbar__icon-button topbar__sidebar-toggle"
      aria-label="Toggle sidebar"
      @click="ui.toggleSidebar()"
    >
      <i class="pi pi-bars" aria-hidden="true" />
    </button>

    <AppBreadcrumbs />

    <div class="topbar__spacer" />

    <button type="button" class="topbar__search" @click="openSearch">
      <i class="pi pi-search" aria-hidden="true" />
      <span class="topbar__search-label">Search documents, clauses, claims</span>
      <kbd class="topbar__kbd">{{ shortcutKey }} K</kbd>
    </button>

    <div ref="notificationsRoot" class="topbar__notifications">
      <button
        type="button"
        class="topbar__icon-button"
        :aria-expanded="notificationsOpen"
        aria-label="Notifications"
        @click="notificationsOpen = !notificationsOpen"
      >
        <i class="pi pi-bell" aria-hidden="true" />
        <span v-if="unreadCount > 0" class="topbar__badge">{{ unreadCount }}</span>
      </button>

      <div v-if="notificationsOpen" class="topbar__panel">
        <header class="topbar__panel-header">
          <span class="text-semibold text-sm">Notifications</span>
        </header>
        <div class="topbar__panel-body">
          <p class="topbar__panel-empty">
            No notifications. The notification service is not connected in this build.
          </p>
        </div>
      </div>
    </div>

    <ThemeToggle />

    <div class="divider-v topbar__divider" />

    <UserMenu />
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: var(--topbar-height);
  padding: 0 var(--space-4);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.topbar__spacer {
  flex: 1 1 auto;
}

.topbar__icon-button {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  font-size: 14px;
  color: var(--color-text-secondary);
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.topbar__icon-button:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.topbar__sidebar-toggle {
  display: none;
}

.topbar__search {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 320px;
  height: 30px;
  padding: 0 var(--space-2) 0 var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: var(--color-surface-sunken);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.topbar__search:hover {
  border-color: var(--color-border-strong);
}

.topbar__search-label {
  flex: 1 1 auto;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.topbar__kbd {
  flex-shrink: 0;
}

.topbar__badge {
  position: absolute;
  top: 2px;
  right: 2px;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  font-size: 9px;
  font-weight: var(--weight-semibold);
  line-height: 15px;
  color: var(--color-on-accent);
  background: var(--color-danger);
  border-radius: var(--radius-full);
}

.topbar__notifications {
  position: relative;
}

.topbar__panel {
  position: absolute;
  top: calc(100% + var(--space-2));
  right: 0;
  z-index: var(--z-dropdown);
  width: 300px;
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.topbar__panel-header {
  padding: var(--space-3);
  border-bottom: 1px solid var(--color-border-subtle);
}

.topbar__panel-body {
  padding: var(--space-4);
}

.topbar__panel-empty {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.topbar__divider {
  height: 20px;
  align-self: center;
}

@media (max-width: 1100px) {
  .topbar__search {
    width: 200px;
  }

  .topbar__kbd {
    display: none;
  }
}

@media (max-width: 900px) {
  .topbar__sidebar-toggle {
    display: flex;
  }

  .topbar__search-label {
    display: none;
  }

  .topbar__search {
    width: 30px;
    padding: 0;
    justify-content: center;
  }
}
</style>
