import { computed, ref, watch } from 'vue'

import { defineStore } from 'pinia'

const THEME_KEY = 'claimiq.theme'
const SIDEBAR_KEY = 'claimiq.sidebar.collapsed'
const DEFAULT_TOAST_TTL = 6000

function readStorage(key) {
  try {
    return localStorage.getItem(key)
  } catch {
    return null // private mode, or storage disabled by policy
  }
}

function writeStorage(key, value) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Persisting a preference is best-effort; never break the UI over it.
  }
}

function prefersDark() {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )
}

function initialTheme() {
  const stored = readStorage(THEME_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return prefersDark() ? 'dark' : 'light'
}

let toastSeq = 0

export const useUiStore = defineStore('ui', () => {
  const theme = ref(initialTheme())
  const sidebarCollapsed = ref(readStorage(SIDEBAR_KEY) === 'true')
  // Separate from `sidebarCollapsed`: that is a remembered desktop
  // preference, this is a drawer that is open right now and should not
  // outlive the screen the user opened it on.
  const mobileNavOpen = ref(false)
  const breadcrumbLeaf = ref(null)
  const toasts = ref([])
  const timers = new Map()

  const isDark = computed(() => theme.value === 'dark')

  function applyTheme(value) {
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', value)
    }
  }

  function setTheme(value) {
    theme.value = value === 'dark' ? 'dark' : 'light'
  }

  function toggleTheme() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  watch(
    theme,
    (value) => {
      applyTheme(value)
      writeStorage(THEME_KEY, value)
    },
    { immediate: true }
  )

  function setSidebarCollapsed(value) {
    sidebarCollapsed.value = Boolean(value)
  }

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  watch(sidebarCollapsed, (value) => writeStorage(SIDEBAR_KEY, String(value)))

  function setMobileNavOpen(value) {
    mobileNavOpen.value = Boolean(value)
  }

  function toggleMobileNav() {
    mobileNavOpen.value = !mobileNavOpen.value
  }

  /**
   * The name of the record the current screen is showing.
   *
   * A route can only describe its leaf generically — "Document", "Claim" —
   * because the title belongs to a record that has not loaded when the route
   * resolves. A detail view sets this once it knows, and the breadcrumb
   * reads it. Cleared on every navigation, so a stale name never labels the
   * next screen.
   */
  function setBreadcrumbLeaf(label) {
    breadcrumbLeaf.value = label || null
  }

  function clearBreadcrumbLeaf() {
    breadcrumbLeaf.value = null
  }

  /**
   * Queue a notification.
   *
   * `severity` is one of success | info | warn | error. Errors are sticky by
   * default — an error that vanishes before it is read is a bug report the
   * support team never receives.
   */
  function notify({ severity = 'info', summary, detail, ttl, requestId } = {}) {
    const id = ++toastSeq
    const sticky = severity === 'error' && ttl === undefined
    toasts.value.push({ id, severity, summary, detail, requestId, sticky })

    if (!sticky) {
      const timeout = ttl ?? DEFAULT_TOAST_TTL
      const handle = setTimeout(() => dismiss(id), timeout)
      timers.set(id, handle)
    }
    return id
  }

  /** Convenience for the common `catch (err) { notifyError(err) }` shape. */
  function notifyError(error, summary = 'Request failed') {
    return notify({
      severity: 'error',
      summary,
      detail: error?.message || 'An unexpected error occurred.',
      requestId: error?.requestId ?? null,
    })
  }

  function notifySuccess(summary, detail) {
    return notify({ severity: 'success', summary, detail })
  }

  function dismiss(id) {
    const handle = timers.get(id)
    if (handle) {
      clearTimeout(handle)
      timers.delete(id)
    }
    toasts.value = toasts.value.filter((toast) => toast.id !== id)
  }

  function clearToasts() {
    for (const handle of timers.values()) clearTimeout(handle)
    timers.clear()
    toasts.value = []
  }

  return {
    theme,
    isDark,
    sidebarCollapsed,
    mobileNavOpen,
    breadcrumbLeaf,
    toasts,
    setTheme,
    toggleTheme,
    setSidebarCollapsed,
    toggleSidebar,
    setMobileNavOpen,
    toggleMobileNav,
    setBreadcrumbLeaf,
    clearBreadcrumbLeaf,
    notify,
    notifyError,
    notifySuccess,
    dismiss,
    clearToasts,
  }
})
