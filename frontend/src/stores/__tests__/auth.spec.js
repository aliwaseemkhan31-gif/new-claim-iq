import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/client'
import { useAuthStore } from '../auth'

vi.mock('@/api/auth', () => ({
  fetchCsrfToken: vi.fn().mockResolvedValue({}),
  login: vi.fn(),
  logout: vi.fn(),
  fetchCurrentUser: vi.fn(),
  changePassword: vi.fn(),
  fetchProjectPermissions: vi.fn(),
}))

import * as authApi from '@/api/auth'

const USER = { id: 1, email: 'a.khan@example.com', full_name: 'Aisha Khan' }

describe('auth store — hasPermission', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    store = useAuthStore()
  })

  it('denies everything while signed out, even a code in the array', () => {
    store.permissions = ['claim.view']
    expect(store.isAuthenticated).toBe(false)
    expect(store.hasPermission('claim.view')).toBe(false)
  })

  it('grants a code the user holds and denies one they do not', () => {
    store.applySession({ user: USER, permissions: ['claim.view', 'document.view'] })

    expect(store.hasPermission('claim.view')).toBe(true)
    expect(store.hasPermission('document.view')).toBe(true)
    expect(store.hasPermission('claim.delete')).toBe(false)
    expect(store.hasPermission('org.manage')).toBe(false)
  })

  it('does not treat a prefix as a grant', () => {
    store.applySession({ user: USER, permissions: ['claim.view'] })
    expect(store.hasPermission('claim')).toBe(false)
    expect(store.hasPermission('claim.view.extra')).toBe(false)
  })

  it('grants every code to a system administrator', () => {
    store.applySession({
      user: { ...USER, is_system_admin: true },
      permissions: [],
    })

    expect(store.hasPermission('org.manage')).toBe(true)
    expect(store.hasPermission('claim.delete')).toBe(true)
    expect(store.hasPermission('anything.at.all')).toBe(true)
  })

  it('treats an absent code as an ungated check for a signed-in user', () => {
    store.applySession({ user: USER, permissions: [] })
    expect(store.hasPermission(null)).toBe(true)
    expect(store.hasPermission(undefined)).toBe(true)
  })

  it('tolerates a missing permissions array', () => {
    store.applySession({ user: USER })
    expect(store.permissions).toEqual([])
    expect(store.hasPermission('claim.view')).toBe(false)
  })
})

describe('auth store — hasAllPermissions / hasAnyPermission', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    store = useAuthStore()
    store.applySession({ user: USER, permissions: ['claim.view', 'document.view'] })
  })

  it('requires every code for hasAllPermissions', () => {
    expect(store.hasAllPermissions(['claim.view', 'document.view'])).toBe(true)
    expect(store.hasAllPermissions(['claim.view', 'claim.delete'])).toBe(false)
  })

  it('requires one code for hasAnyPermission', () => {
    expect(store.hasAnyPermission(['claim.delete', 'document.view'])).toBe(true)
    expect(store.hasAnyPermission(['claim.delete', 'org.manage'])).toBe(false)
  })

  it('treats an empty list as no gate in both directions', () => {
    expect(store.hasAllPermissions([])).toBe(true)
    expect(store.hasAnyPermission([])).toBe(true)
  })
})

describe('auth store — identity derivation', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('prefers a split name, then full_name, then email', () => {
    const store = useAuthStore()

    store.applySession({ user: USER })
    expect(store.displayName).toBe('Aisha Khan')
    expect(store.initials).toBe('AK')

    store.applySession({ user: { full_name: 'Ops Team' } })
    expect(store.displayName).toBe('Ops Team')
    expect(store.initials).toBe('OT')

    store.applySession({ user: { email: 'x@y.z' } })
    expect(store.displayName).toBe('x@y.z')
  })

  it('yields an empty name and a placeholder initial when signed out', () => {
    const store = useAuthStore()
    expect(store.displayName).toBe('')
    expect(store.initials).toBe('?')
  })
})

describe('auth store — session lifecycle', () => {
  let store

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    store = useAuthStore()
  })

  it('applies the session returned by login', async () => {
    authApi.login.mockResolvedValue({
      user: USER,
      permissions: ['claim.view'],
      organization: { id: 7, name: 'Meridian' },
    })

    await store.login({ email: 'a.khan@example.com', password: 'secret' })

    expect(store.isAuthenticated).toBe(true)
    expect(store.organization.name).toBe('Meridian')
    expect(store.hasPermission('claim.view')).toBe(true)
    expect(store.bootstrapped).toBe(true)
  })

  it('clears the session and records the error when login fails', async () => {
    authApi.login.mockRejectedValue(
      new ApiError({ code: 'authentication_failed', message: 'Wrong credentials.' })
    )

    await expect(store.login({ email: 'a@b.c', password: 'b' })).rejects.toBeInstanceOf(ApiError)

    expect(store.isAuthenticated).toBe(false)
    expect(store.permissions).toEqual([])
    expect(store.error.code).toBe('authentication_failed')
    expect(store.loading).toBe(false)
  })

  it('treats a 401 from bootstrap as "signed out", not as an error to display', async () => {
    authApi.fetchCurrentUser.mockRejectedValue(
      new ApiError({ code: 'not_authenticated', status: 401 })
    )

    await store.bootstrap()

    expect(store.isAuthenticated).toBe(false)
    expect(store.bootstrapped).toBe(true)
    expect(store.error).toBeNull()
  })

  it('surfaces a non-auth bootstrap failure', async () => {
    authApi.fetchCurrentUser.mockRejectedValue(new ApiError({ code: 'network_error' }))

    await store.bootstrap()

    expect(store.isAuthenticated).toBe(false)
    expect(store.error.code).toBe('network_error')
  })

  it('only bootstraps once unless forced', async () => {
    authApi.fetchCurrentUser.mockResolvedValue({ user: USER, permissions: [] })

    await store.bootstrap()
    await store.bootstrap()
    expect(authApi.fetchCurrentUser).toHaveBeenCalledTimes(1)

    await store.bootstrap({ force: true })
    expect(authApi.fetchCurrentUser).toHaveBeenCalledTimes(2)
  })

  it('clears the client session even when the logout request fails', async () => {
    store.applySession({ user: USER, permissions: ['claim.view'] })
    authApi.logout.mockRejectedValue(new ApiError({ code: 'network_error' }))

    await store.logout()

    expect(store.isAuthenticated).toBe(false)
    expect(store.permissions).toEqual([])
  })

  it('merges project permissions without duplicating existing codes', () => {
    store.applySession({ user: USER, permissions: ['claim.view'] })
    store.mergePermissions(['claim.view', 'evidence.manage'])

    expect(store.permissions).toHaveLength(2)
    expect(store.hasPermission('evidence.manage')).toBe(true)
  })
})
