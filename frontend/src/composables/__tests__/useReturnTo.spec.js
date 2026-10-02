import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import { useReturnTo } from '../useReturnTo'

const blank = { template: '<div />' }

const ROUTES = [
  { path: '/documents', name: 'documents', component: blank, meta: { title: 'Documents' } },
  {
    path: '/documents/:documentId',
    name: 'document-viewer',
    component: blank,
    meta: { title: 'Document' },
  },
  { path: '/ai', name: 'ai-workspace', component: blank, meta: { title: 'AI Workspace' } },
  {
    path: '/projects/:projectId/documents',
    name: 'project-documents',
    component: blank,
    meta: { title: 'Documents' },
  },
  { path: '/login', name: 'login', component: blank, meta: { title: 'Sign in' } },
  { path: '/:rest(.*)*', name: 'not-found', component: blank, meta: { title: 'Page not found' } },
]

const Host = {
  setup() {
    const { target, usingHistory, go } = useReturnTo(() => ({
      to: { name: 'documents' },
      label: 'Documents',
    }))
    return { target, usingHistory, go }
  },
  template: '<div />',
}

/**
 * Walk the given paths in order, then mount on the last one.
 *
 * A real web history is used because the composable reads the back/forward
 * entries vue-router keeps in `history.state`, which the memory history does
 * not maintain — and which is the whole point of the composable.
 */
async function journey(paths) {
  // jsdom's history is shared between tests; start each journey from a known
  // entry so one test's stack is not another's previous page.
  window.history.replaceState(null, '', '/start')
  const router = createRouter({ history: createWebHistory(), routes: ROUTES })
  for (const path of paths) await router.push(path)
  const wrapper = mount(Host, { global: { plugins: [router] } })
  return { wrapper, router }
}

describe('useReturnTo', () => {
  it('offers the register when the detail screen was opened cold', async () => {
    const { wrapper } = await journey(['/documents/abc'])
    expect(wrapper.vm.usingHistory).toBe(false)
    expect(wrapper.vm.target).toEqual({ label: 'Documents', to: { name: 'documents' } })
  })

  it('names the AI workspace when the citation came from there', async () => {
    const { wrapper } = await journey(['/ai', '/documents/abc'])
    expect(wrapper.vm.usingHistory).toBe(true)
    expect(wrapper.vm.target.label).toBe('AI Workspace')
  })

  it('names the project tab when the document was opened from it', async () => {
    const { wrapper } = await journey(['/projects/p1/documents', '/documents/abc'])
    expect(wrapper.vm.target.label).toBe('Documents')
    expect(wrapper.vm.usingHistory).toBe(true)
  })

  it('steps back through history rather than pushing the fallback', async () => {
    const { wrapper, router } = await journey(['/ai', '/documents/abc'])
    const back = vi.spyOn(router, 'back')
    const push = vi.spyOn(router, 'push')
    wrapper.vm.go()
    expect(back).toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
  })

  it('pushes the fallback when there is nothing to go back to', async () => {
    const { wrapper, router } = await journey(['/documents/abc'])
    const push = vi.spyOn(router, 'push')
    wrapper.vm.go()
    await flushPromises()
    expect(push).toHaveBeenCalledWith({ name: 'documents' })
  })

  it('ignores the previous entry when it is the screen already shown', async () => {
    const { wrapper } = await journey(['/documents/abc', '/documents/def'])
    expect(wrapper.vm.usingHistory).toBe(false)
    expect(wrapper.vm.target.label).toBe('Documents')
  })

  it('never offers a return to the sign-in page', async () => {
    const { wrapper } = await journey(['/login', '/documents/abc'])
    expect(wrapper.vm.usingHistory).toBe(false)
  })

  it('never offers a return to a dead link', async () => {
    const { wrapper } = await journey(['/nope/nowhere', '/documents/abc'])
    expect(wrapper.vm.usingHistory).toBe(false)
  })

  it('is unaffected by the screen replacing its own URL', async () => {
    const { wrapper, router } = await journey(['/ai', '/documents/abc'])
    // What paging the viewer does: `replace`, not `push`.
    await router.replace('/documents/abc?page=7')
    await flushPromises()
    expect(wrapper.vm.target.label).toBe('AI Workspace')
  })
})
