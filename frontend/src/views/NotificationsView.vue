<script setup>
import { computed, onMounted, ref } from 'vue'

import * as notificationsApi from '@/api/notifications'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useUiStore } from '@/stores/ui'
import { formatRelative } from '@/utils/format'

/**
 * Notifications raised by real events: a document finished or failed, a
 * knowledge base became ready, an analysis finished, or a computed notice
 * deadline is approaching or has passed.
 */
const ui = useUiStore()

const items = ref([])
const unread = ref(0)
const unreadOnly = ref(false)
const loading = ref(true)
const error = ref(null)

const SEVERITY_TONES = { info: 'info', success: 'success', warning: 'warning', danger: 'danger' }

const filtered = computed(() => items.value)

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = await notificationsApi.listNotifications({
      unread: unreadOnly.value ? 'true' : undefined,
      page_size: 50,
    })
    items.value = data.results ?? []
    unread.value = data.unread ?? 0
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

async function markRead(notification) {
  if (notification.read_at) return
  try {
    const data = await notificationsApi.markNotificationRead(notification.id)
    notification.read_at = new Date().toISOString()
    unread.value = data.unread ?? unread.value
    if (unreadOnly.value) items.value = items.value.filter((item) => item.id !== notification.id)
  } catch (err) {
    ui.notifyError(err, 'Could not mark it read')
  }
}

async function markAll() {
  try {
    await notificationsApi.markAllNotificationsRead()
    unread.value = 0
    await load()
  } catch (err) {
    ui.notifyError(err, 'Could not mark all read')
  }
}

function toggleFilter() {
  unreadOnly.value = !unreadOnly.value
  load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="Notifications" :count="unread" description="Raised by events on your projects. Nothing here is generated to fill the panel.">
      <template #actions>
        <AppButton
          size="sm"
          variant="ghost"
          :label="unreadOnly ? 'Show all' : 'Show unread only'"
          @click="toggleFilter"
        />
        <AppButton
          size="sm"
          variant="ghost"
          icon="pi pi-check"
          label="Mark all read"
          :disabled="!unread"
          @click="markAll"
        />
      </template>
    </PageHeader>

    <div v-if="loading" class="surface notifications__pad">
      <LoadingSkeleton variant="text" :rows="6" />
    </div>

    <ErrorState v-else-if="error" :error="error" title="Could not load notifications" @retry="load" />

    <EmptyState
      v-else-if="!filtered.length"
      icon="pi pi-bell"
      title="Nothing to report"
      :description="unreadOnly ? 'No unread notifications.' : 'Notifications appear when a document finishes processing, a knowledge base is ready, an analysis completes, or a notice deadline approaches.'"
    />

    <ul v-else class="notifications">
      <li
        v-for="notification in filtered"
        :key="notification.id"
        class="surface notification"
        :class="{ 'notification--unread': !notification.read_at }"
      >
        <div class="grow">
          <div class="row gap-2 wrap">
            <StatusBadge
              :tone="SEVERITY_TONES[notification.severity] || 'neutral'"
              :label="notification.title"
              size="sm"
            />
            <span class="text-xs text-muted">{{ formatRelative(notification.created_at) }}</span>
          </div>
          <p class="notification__message">{{ notification.message }}</p>
          <RouterLink v-if="notification.link && notification.link.name" :to="notification.link" class="text-xs">
            Open
          </RouterLink>
        </div>
        <AppButton
          v-if="!notification.read_at"
          size="sm"
          variant="ghost"
          icon="pi pi-check"
          aria-label="Mark read"
          @click="markRead(notification)"
        />
      </li>
    </ul>
  </div>
</template>

<style scoped>
.notifications {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.notification {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
}

.notification--unread {
  border-left: 3px solid var(--color-accent);
}

.notification__message {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.notifications__pad {
  padding: var(--space-4);
}
</style>
