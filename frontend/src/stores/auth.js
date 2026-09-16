import { computed, ref } from 'vue'

import { defineStore } from 'pinia'

import * as authApi from '@/api/auth'
import { ApiError } from '@/api/client'

/**
 * Session state.
 *
 * The server is the only authority on who you are. Nothing here is persisted:
 * on reload `bootstrap()` re-asks the API. A cached identity in localStorage
 * is a way to show a signed-in shell to a signed-out user.
 */
export const useAuthStore = defineStore('auth', () => {
  const user = ref(null)
  const permissions = ref([])
  /** Permissions held on at least one project — for navigation, not for actions. */
  const projectPermissions = ref([])
  /** projectId -> permission codes on that project, loaded when a project opens. */
  const perProject = ref({})
  const organization = ref(null)
  const loading = ref(false)
  const bootstrapped = ref(false)
  const error = ref(null)

  const isAuthenticated = computed(() => user.value !== null)
  const isSystemAdmin = computed(() => user.value?.is_system_admin === true)

  /**
   * The backend User model has `full_name` and `email` — no username, and no
   * split first/last name. `first_name`/`last_name` are still read first so a
   * future split does not need a frontend change.
   */
  const displayName = computed(() => {
    if (!user.value) return ''
    const split = [user.value.first_name, user.value.last_name].filter(Boolean).join(' ').trim()
    return split || user.value.full_name || user.value.email || 'User'
  })

  const initials = computed(() => {
    const name = displayName.value.trim()
    if (!name) return '?'
    const parts = name.split(/\s+/).filter(Boolean)
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
  })

  const permissionSet = computed(() => new Set(permissions.value))
  const projectPermissionSet = computed(() => new Set(projectPermissions.value))

  /**
   * A single permission check, for navigation and page access.
   *
   * True when the user holds the permission through their organization role
   * or on any project. A system administrator passes everything — that
   * exception is explicit here, matching the backend's permission model.
   * Actions on a specific project use `canInProject`.
   */
  function hasPermission(code) {
    if (!isAuthenticated.value) return false
    if (isSystemAdmin.value) return true
    if (!code) return true
    return permissionSet.value.has(code) || projectPermissionSet.value.has(code)
  }

  /** Whether the user may do `code` on one project. Load it first with `loadProjectPermissions`. */
  function canInProject(projectId, code) {
    if (!isAuthenticated.value) return false
    if (isSystemAdmin.value) return true
    if (!code) return true
    const codes = perProject.value[projectId]
    if (!codes) return permissionSet.value.has(code)
    return codes.includes(code)
  }

  async function loadProjectPermissions(projectId, { force = false } = {}) {
    if (!projectId) return []
    if (perProject.value[projectId] && !force) return perProject.value[projectId]
    const data = await authApi.fetchProjectPermissions(projectId)
    perProject.value = { ...perProject.value, [projectId]: data?.permissions ?? [] }
    return perProject.value[projectId]
  }

  /** True when the user holds every code. An empty list is vacuously true. */
  function hasAllPermissions(codes = []) {
    return codes.every((code) => hasPermission(code))
  }

  /** True when the user holds at least one code. An empty list means "no gate". */
  function hasAnyPermission(codes = []) {
    if (codes.length === 0) return true
    return codes.some((code) => hasPermission(code))
  }

  function applySession(payload) {
    user.value = payload?.user ?? null
    permissions.value = Array.isArray(payload?.permissions) ? payload.permissions : []
    projectPermissions.value = Array.isArray(payload?.project_permissions)
      ? payload.project_permissions
      : []
    perProject.value = {}
    organization.value = payload?.organization ?? null
  }

  function clearSession() {
    user.value = null
    permissions.value = []
    projectPermissions.value = []
    perProject.value = {}
    organization.value = null
  }

  async function login(credentials) {
    loading.value = true
    error.value = null
    try {
      // Prime the CSRF cookie; the login POST itself is an unsafe method.
      await authApi.fetchCsrfToken().catch(() => null)
      const session = await authApi.login(credentials)
      applySession(session)
      bootstrapped.value = true
      return session
    } catch (err) {
      clearSession()
      error.value = err instanceof ApiError ? err : new ApiError({ cause: err })
      throw error.value
    } finally {
      loading.value = false
    }
  }

  async function logout() {
    try {
      await authApi.logout()
    } catch {
      // A failed logout must still clear the client. The cookie may already
      // be gone, which is exactly the state we are trying to reach.
    } finally {
      clearSession()
      bootstrapped.value = true
    }
  }

  /**
   * Resolve the session once at startup and before the first guarded route.
   * A 401 is a normal outcome, not an error to surface.
   */
  async function bootstrap({ force = false } = {}) {
    if (bootstrapped.value && !force) return user.value
    loading.value = true
    try {
      const session = await authApi.fetchCurrentUser()
      applySession(session)
      error.value = null
    } catch (err) {
      clearSession()
      if (!(err instanceof ApiError && (err.isAuthError || err.status === 403))) {
        error.value = err
      }
    } finally {
      loading.value = false
      bootstrapped.value = true
    }
    return user.value
  }

  /** Merge project-scoped permissions in when a project workspace opens. */
  function mergePermissions(codes = []) {
    const merged = new Set([...permissions.value, ...codes])
    permissions.value = [...merged]
  }

  return {
    user,
    permissions,
    organization,
    loading,
    bootstrapped,
    error,
    isAuthenticated,
    isSystemAdmin,
    displayName,
    initials,
    projectPermissions,
    hasPermission,
    hasAllPermissions,
    hasAnyPermission,
    canInProject,
    loadProjectPermissions,
    login,
    logout,
    bootstrap,
    applySession,
    clearSession,
    mergePermissions,
  }
})
