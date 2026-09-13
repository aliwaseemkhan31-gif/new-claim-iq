import { beforeEach, describe, expect, it, vi } from 'vitest'

import { resolveNavigation, routePermission, routeRequiresAuth } from '../index'

/**
 * A stand-in for the auth store. Using a fake rather than a real Pinia store
 * keeps these tests about the guard's decisions, not about session plumbing.
 */
function fakeAuth({ authenticated = false, permissions = [], bootstrapped = true } = {}) {
  const store = {
    isAuthenticated: authenticated,
    bootstrapped,
    permissions,
    hasPermission: (code) => permissions.includes(code),
    bootstrap: vi.fn(async () => {
      store.bootstrapped = true
    }),
  }
  return store
}

/** A minimal route object of the shape vue-router hands the guard. */
function route({ name = 'dashboard', fullPath = '/dashboard', matched = [] } = {}) {
  return { name, fullPath, matched, params: {}, query: {} }
}

const AUTHED_PARENT = { meta: { requiresAuth: true } }

describe('routeRequiresAuth', () => {
  it('inherits requiresAuth from a parent record', () => {
    expect(routeRequiresAuth(route({ matched: [AUTHED_PARENT, { meta: {} }] }))).toBe(true)
  })

  it('lets the nearest child override the parent', () => {
    const to = route({ matched: [AUTHED_PARENT, { meta: { requiresAuth: false } }] })
    expect(routeRequiresAuth(to)).toBe(false)
  })

  it('defaults to false when nothing declares it', () => {
    expect(routeRequiresAuth(route({ matched: [{ meta: {} }] }))).toBe(false)
  })
})

describe('routePermission', () => {
  it('returns the nearest declared permission', () => {
    const to = route({
      matched: [AUTHED_PARENT, { meta: { permission: 'claim.view' } }],
    })
    expect(routePermission(to)).toBe('claim.view')
  })

  it('returns null when no record declares one', () => {
    expect(routePermission(route({ matched: [AUTHED_PARENT] }))).toBeNull()
  })
})

describe('navigation guard — authentication', () => {
  let auth

  beforeEach(() => {
    auth = fakeAuth()
  })

  it('redirects an anonymous user to login and preserves the target', async () => {
    const to = route({ name: 'claims', fullPath: '/claims', matched: [AUTHED_PARENT] })

    const result = await resolveNavigation(to, auth)

    expect(result).toEqual({ name: 'login', query: { redirect: '/claims' } })
  })

  it('does not add a redirect query for the root path', async () => {
    const to = route({ name: 'dashboard', fullPath: '/', matched: [AUTHED_PARENT] })

    const result = await resolveNavigation(to, auth)

    expect(result).toEqual({ name: 'login', query: {} })
  })

  it('resolves the session before deciding, so a deep link survives a refresh', async () => {
    const store = fakeAuth({ bootstrapped: false })
    store.bootstrap = vi.fn(async () => {
      store.bootstrapped = true
      store.isAuthenticated = true
    })

    const to = route({ name: 'claims', fullPath: '/claims', matched: [AUTHED_PARENT] })
    const result = await resolveNavigation(to, store)

    expect(store.bootstrap).toHaveBeenCalledOnce()
    expect(result).toBe(true)
  })

  it('does not re-bootstrap a session that is already resolved', async () => {
    const store = fakeAuth({ authenticated: true, bootstrapped: true })
    await resolveNavigation(route({ matched: [AUTHED_PARENT] }), store)
    expect(store.bootstrap).not.toHaveBeenCalled()
  })

  it('allows an authenticated user onto a guarded route', async () => {
    const store = fakeAuth({ authenticated: true })
    const to = route({ name: 'dashboard', matched: [AUTHED_PARENT] })
    expect(await resolveNavigation(to, store)).toBe(true)
  })

  it('allows anyone onto a public route without bootstrapping', async () => {
    const to = route({ name: 'not-found', matched: [{ meta: { requiresAuth: false } }] })
    expect(await resolveNavigation(to, auth)).toBe(true)
    expect(auth.bootstrap).not.toHaveBeenCalled()
  })

  it('bounces an authenticated user away from the login page', async () => {
    const store = fakeAuth({ authenticated: true })
    const to = route({
      name: 'login',
      fullPath: '/login',
      matched: [{ meta: { requiresAuth: false } }],
    })

    expect(await resolveNavigation(to, store)).toEqual({ name: 'dashboard' })
  })

  it('leaves an anonymous user on the login page', async () => {
    const to = route({
      name: 'login',
      fullPath: '/login',
      matched: [{ meta: { requiresAuth: false } }],
    })
    expect(await resolveNavigation(to, auth)).toBe(true)
  })
})

describe('navigation guard — permissions', () => {
  it('redirects to forbidden when the permission is missing', async () => {
    const store = fakeAuth({ authenticated: true, permissions: ['document.view'] })
    const to = route({
      name: 'claims',
      fullPath: '/claims',
      matched: [AUTHED_PARENT, { meta: { permission: 'claim.view' } }],
    })

    expect(await resolveNavigation(to, store)).toEqual({
      name: 'forbidden',
      query: { from: '/claims' },
    })
  })

  it('allows the route when the permission is held', async () => {
    const store = fakeAuth({ authenticated: true, permissions: ['claim.view'] })
    const to = route({
      name: 'claims',
      matched: [AUTHED_PARENT, { meta: { permission: 'claim.view' } }],
    })

    expect(await resolveNavigation(to, store)).toBe(true)
  })

  it('checks authentication before permissions', async () => {
    const store = fakeAuth({ authenticated: false })
    const to = route({
      name: 'claims',
      fullPath: '/claims',
      matched: [AUTHED_PARENT, { meta: { permission: 'claim.view' } }],
    })

    const result = await resolveNavigation(to, store)

    expect(result.name).toBe('login')
  })

  it('enforces a permission declared on a nested project tab', async () => {
    const store = fakeAuth({ authenticated: true, permissions: ['project.view'] })
    const to = route({
      name: 'project-ai',
      fullPath: '/projects/7/ai',
      matched: [AUTHED_PARENT, { meta: { title: 'Project' } }, { meta: { permission: 'ai.query' } }],
    })

    expect(await resolveNavigation(to, store)).toEqual({
      name: 'forbidden',
      query: { from: '/projects/7/ai' },
    })
  })
})
