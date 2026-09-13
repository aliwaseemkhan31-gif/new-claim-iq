<script setup>
import { computed, onMounted } from 'vue'

import * as healthApi from '@/api/health'
import AppCard from '@/components/common/AppCard.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { EM_DASH } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const auth = useAuthStore()

const version = useAsyncData(({ signal }) => healthApi.fetchVersion({ signal }))

const buildRows = computed(() => {
  const data = version.data.value
  if (!data) return []
  return Object.entries(data).map(([key, value]) => ({
    label: key.replace(/_/g, ' '),
    value: typeof value === 'object' ? JSON.stringify(value) : String(value),
  }))
})

onMounted(() => version.execute())
</script>

<template>
  <div class="page">
    <PageHeader title="Settings" description="Preferences for your account on this installation." />

    <AppCard title="Appearance" subtitle="Stored in this browser only">
      <div class="setting">
        <div>
          <p class="setting__label">Theme</p>
          <p class="setting__hint">Applies to this browser and persists between sessions.</p>
        </div>
        <div class="setting__choices" role="radiogroup" aria-label="Theme">
          <button
            type="button"
            class="setting__choice"
            :class="{ 'is-on': !ui.isDark }"
            role="radio"
            :aria-checked="!ui.isDark"
            @click="ui.setTheme('light')"
          >
            <i class="pi pi-sun" aria-hidden="true" />
            Light
          </button>
          <button
            type="button"
            class="setting__choice"
            :class="{ 'is-on': ui.isDark }"
            role="radio"
            :aria-checked="ui.isDark"
            @click="ui.setTheme('dark')"
          >
            <i class="pi pi-moon" aria-hidden="true" />
            Dark
          </button>
        </div>
      </div>

      <div class="divider setting__rule" />

      <div class="setting">
        <div>
          <p class="setting__label">Collapsed sidebar</p>
          <p class="setting__hint">Start with the navigation rail collapsed.</p>
        </div>
        <button
          type="button"
          class="setting__switch"
          :class="{ 'is-on': ui.sidebarCollapsed }"
          role="switch"
          :aria-checked="ui.sidebarCollapsed"
          aria-label="Collapsed sidebar"
          @click="ui.toggleSidebar()"
        >
          <span class="setting__knob" />
        </button>
      </div>
    </AppCard>

    <AppCard title="Account">
      <dl class="facts">
        <div class="facts__row">
          <dt>Name</dt>
          <dd>{{ auth.displayName || EM_DASH }}</dd>
        </div>
        <div class="facts__row">
          <dt>Username</dt>
          <dd>{{ auth.user?.username || EM_DASH }}</dd>
        </div>
        <div class="facts__row">
          <dt>Email</dt>
          <dd>{{ auth.user?.email || EM_DASH }}</dd>
        </div>
        <div class="facts__row">
          <dt>Organization</dt>
          <dd>{{ auth.organization?.name || EM_DASH }}</dd>
        </div>
        <div class="facts__row">
          <dt>Permissions</dt>
          <dd>{{ auth.permissions.length }} granted</dd>
        </div>
      </dl>
      <p class="setting__hint setting__note">
        Password changes and profile edits are handled by the accounts API, which is not wired up
        in this build.
      </p>
    </AppCard>

    <AppCard title="Installation">
      <p v-if="version.loading.value" class="text-sm text-muted">Reading build information…</p>
      <p v-else-if="version.error.value" class="text-sm text-muted">
        Build information is unavailable: {{ version.error.value.message }}
      </p>
      <dl v-else-if="buildRows.length > 0" class="facts">
        <div v-for="row in buildRows" :key="row.label" class="facts__row">
          <dt>{{ row.label }}</dt>
          <dd class="text-mono">{{ row.value }}</dd>
        </div>
      </dl>
      <p v-else class="text-sm text-muted">The version endpoint returned no fields.</p>
    </AppCard>
  </div>
</template>

<style scoped>
.setting {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-6);
}

.setting__rule {
  margin: var(--space-4) 0;
}

.setting__label {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.setting__hint {
  margin-top: 1px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.setting__note {
  margin-top: var(--space-4);
}

.setting__choices {
  display: flex;
  gap: var(--space-1);
  padding: 2px;
  background: var(--color-surface-sunken);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.setting__choice {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 26px;
  padding: 0 var(--space-3);
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
}

.setting__choice.is-on {
  color: var(--color-text);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}

.setting__switch {
  position: relative;
  width: 36px;
  height: 20px;
  background: var(--color-border-strong);
  border-radius: var(--radius-full);
  transition: background-color var(--duration-fast) var(--ease-standard);
  flex-shrink: 0;
}

.setting__switch.is-on {
  background: var(--color-accent);
}

.setting__knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  background: #fff;
  border-radius: 50%;
  transition: transform var(--duration-fast) var(--ease-standard);
}

.setting__switch.is-on .setting__knob {
  transform: translateX(16px);
}

.facts {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.facts__row {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: var(--space-3);
  align-items: baseline;
}

.facts__row dt {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  text-transform: capitalize;
}

.facts__row dd {
  margin: 0;
  font-size: var(--text-sm);
}
</style>
