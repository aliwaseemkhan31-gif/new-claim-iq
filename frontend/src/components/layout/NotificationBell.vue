<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import * as notificationsApi from '@/api/notifications'
import { formatRelative } from '@/utils/format'

/**
 * The notification bell.
 *
 * The badge shows a real unread count from the server, polled quietly. It is
 * never a placeholder number: an empty panel says so.
 */
const POLL_MS = 60000

const open = ref(false)
const root = ref(null)
const items = ref([])
const unread = ref(0)
const loading = ref(false)
let timer = null

const SEVERITY_ICONS = {
  info: 'pi pi-info-circle',
  success: 'pi pi-check-circle',
  warning: 'pi pi-exclamation-circle',
  danger: 'pi pi-exclamation-triangle',
}

const recent = computed(() => items.value.slice(0, 6))

async function refreshCount() {
  try {
    const data = await notificationsApi.fetchUnreadCount()
    unread.value = data.unread ?? 0
  } catch {
    // A failing count must not break the shell.
  }
}

async function loadItems() {
  loading.value = true
  try {
    const data = await notificationsApi.listNotifications({ page_size: 10 })
    items.value = data.results ?? []
    unread.value = data.unread ?? unread.value
  } catch {
    items.value = []
  } finally {
    loading.value = false
  }
}

async function toggle() {
  open.value = !open.value
  if (open.value) await loadItems()
}

async function markRead(notification) {
  if (notification.read_at) return
  try {
    const data = await notificationsApi.markNotificationRead(notification.id)
    notification.read_at = new Date().toISOString()
    unread.value = data.unread ?? unread.value
  } catch {
    // Leave it unread; the list will correct itself on reload.
  }
}

function onDocumentClick(event) {
  if (open.value && root.value && !root.value.contains(event.target)) open.value = false
}

function onKeydown(event) {
  if (event.key === 'Escape') open.value = false
}

onMounted(() => {
  refreshCount()
  timer = setInterval(refreshCount, POLL_MS)
  document.addEventListener('click', onDocumentClick)
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  clearInterval(timer)
  document.removeEventListener('click', onDocumentClick)
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div ref="root" class="bell">
    <button
      type="button"
      class="topbar__icon-button"
      :aria-expanded="open"
      aria-label="Notifications"
      @click="toggle"
    >
      <i class="pi pi-bell" aria-hidden="true" />
      <span v-if="unread > 0" class="topbar__badge">{{ unread }}</span>
    </button>

    <div v-if="open" class="bell__panel">
      <header class="bell__header">
        <span class="text-semibold text-sm">Notifications</span>
        <RouterLink :to="{ name: 'notifications' }" class="text-xs" @click="open = false">
          See all
        </RouterLink>
      </header>

      <div class="bell__body">
        <p v-if="loading" class="bell__empty">Loading…</p>
        <p v-else-if="!recent.length" class="bell__empty">
          Nothing to report. Notifications appear when a document finishes, a knowledge base is
          ready, an analysis completes, or a notice deadline approaches.
        </p>
        <ul v-else class="bell__list">
          <li
            v-for="notification in recent"
            :key="notification.id"
            class="bell__item"
            :class="{ 'bell__item--unread': !notification.read_at }"
          >
            <i
              :class="SEVERITY_ICONS[notification.severity] || SEVERITY_ICONS.info"
              class="bell__icon"
              aria-hidden="true"
            />
            <div class="grow">
              <component
                :is="notification.link && notification.link.name ? 'RouterLink' : 'span'"
                :to="notification.link && notification.link.name ? notification.link : undefined"
                class="bell__title"
                @click="markRead(notification), (open = false)"
              >
                {{ notification.title }}
              </component>
              <p class="bell__time">{{ formatRelative(notification.created_at) }}</p>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bell {
  position: relative;
}

.bell__panel {
  position: absolute;
  top: calc(100% + var(--space-2));
  right: 0;
  z-index: var(--z-dropdown);
  width: 340px;
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.bell__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3);
  border-bottom: 1px solid var(--color-border-subtle);
}

.bell__body {
  max-height: 360px;
  overflow: auto;
}

.bell__empty {
  padding: var(--space-4);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.bell__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.bell__item {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-3);
  border-bottom: 1px solid var(--color-border-subtle);
}

.bell__item--unread {
  background: var(--color-surface-selected);
}

.bell__icon {
  margin-top: 2px;
  font-size: 12px;
  color: var(--color-text-muted);
}

.bell__title {
  font-size: var(--text-sm);
}

.bell__time {
  font-size: var(--text-2xs);
  color: var(--color-text-muted);
}
</style>
