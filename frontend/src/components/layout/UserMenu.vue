<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const open = ref(false)
const root = ref(null)
const signingOut = ref(false)

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

function onDocumentClick(event) {
  if (open.value && root.value && !root.value.contains(event.target)) close()
}

function onKeydown(event) {
  if (event.key === 'Escape') close()
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocumentClick)
  document.removeEventListener('keydown', onKeydown)
})

async function signOut() {
  signingOut.value = true
  try {
    await auth.logout()
    close()
    await router.push({ name: 'login' })
  } finally {
    signingOut.value = false
  }
}

function go(name) {
  close()
  router.push({ name })
}
</script>

<template>
  <div ref="root" class="user-menu">
    <button
      type="button"
      class="user-menu__trigger"
      :aria-expanded="open"
      aria-haspopup="menu"
      :aria-label="`Account menu for ${auth.displayName}`"
      @click="toggle"
    >
      <span class="user-menu__avatar" aria-hidden="true">{{ auth.initials }}</span>
      <i class="pi pi-angle-down user-menu__caret" aria-hidden="true" />
    </button>

    <div v-if="open" class="user-menu__panel" role="menu">
      <div class="user-menu__identity">
        <p class="user-menu__name truncate">{{ auth.displayName }}</p>
        <p class="user-menu__email truncate">{{ auth.user?.email || '—' }}</p>
        <p v-if="auth.organization?.name" class="user-menu__org truncate">
          {{ auth.organization.name }}
        </p>
      </div>

      <div class="user-menu__divider" />

      <button type="button" class="user-menu__item" role="menuitem" @click="go('settings')">
        <i class="pi pi-cog" aria-hidden="true" />
        <span>Settings</span>
      </button>

      <button
        v-if="auth.hasPermission('org.manage')"
        type="button"
        class="user-menu__item"
        role="menuitem"
        @click="go('administration')"
      >
        <i class="pi pi-shield" aria-hidden="true" />
        <span>Administration</span>
      </button>

      <div class="user-menu__divider" />

      <button
        type="button"
        class="user-menu__item user-menu__item--danger"
        role="menuitem"
        :disabled="signingOut"
        @click="signOut"
      >
        <i class="pi pi-sign-out" aria-hidden="true" />
        <span>{{ signingOut ? 'Signing out…' : 'Sign out' }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.user-menu {
  position: relative;
}

.user-menu__trigger {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  height: 30px;
  padding: 0 var(--space-1) 0 2px;
  border-radius: var(--radius-md);
}

.user-menu__trigger:hover {
  background: var(--color-surface-hover);
}

.user-menu__avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: var(--color-accent);
  background: var(--color-accent-subtle);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-full);
}

.user-menu__caret {
  font-size: 10px;
  color: var(--color-text-muted);
}

.user-menu__panel {
  position: absolute;
  top: calc(100% + var(--space-2));
  right: 0;
  z-index: var(--z-dropdown);
  min-width: 220px;
  padding: var(--space-1);
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.user-menu__identity {
  padding: var(--space-2) var(--space-3) var(--space-3);
}

.user-menu__name {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.user-menu__email,
.user-menu__org {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.user-menu__divider {
  height: 1px;
  margin: var(--space-1) 0;
  background: var(--color-border-subtle);
}

.user-menu__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  height: 30px;
  padding: 0 var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
  text-align: left;
}

.user-menu__item:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.user-menu__item--danger:hover:not(:disabled) {
  color: var(--color-danger);
  background: var(--color-danger-subtle);
}

.user-menu__item:disabled {
  opacity: 0.6;
}
</style>
