import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import { useListQuery } from '../useListQuery'

const Host = {
  setup() {
    return useListQuery({ q: '', status: '', page: 1 })
  },
  template: '<div />',
}

async function mountAt(fullPath) {
  // jsdom's history is shared between tests; a real web history is used so
  // the `replace`-not-`push` guarantee can actually be asserted.
  window.history.replaceState(null, '', '/start')
  const router = createRouter({
    history: createWebHistory(),
    routes: [{ path: '/claims', name: 'claims', component: { template: '<div />' } }],
  })
  await router.push(fullPath)
  const wrapper = mount(Host, { global: { plugins: [router] } })
  return { wrapper, router }
}

describe('useListQuery', () => {
  it('reads defaults when the URL says nothing', async () => {
    const { wrapper } = await mountAt('/claims')
    expect(wrapper.vm.q).toBe('')
    expect(wrapper.vm.status).toBe('')
    expect(wrapper.vm.page).toBe(1)
    expect(wrapper.vm.isFiltered).toBe(false)
  })

  it('reads filters and a page from the URL', async () => {
    const { wrapper } = await mountAt('/claims?q=notice&status=draft&page=3')
    expect(wrapper.vm.q).toBe('notice')
    expect(wrapper.vm.status).toBe('draft')
    expect(wrapper.vm.page).toBe(3)
    expect(wrapper.vm.isFiltered).toBe(true)
  })

  it('writes a filter into the URL', async () => {
    const { wrapper, router } = await mountAt('/claims')
    wrapper.vm.q = 'notice'
    await flushPromises()
    expect(router.currentRoute.value.query.q).toBe('notice')
  })

  it('leaves defaults out of the URL, so an untouched register has a clean address', async () => {
    const { wrapper, router } = await mountAt('/claims?q=notice')
    wrapper.vm.q = ''
    await flushPromises()
    expect(router.currentRoute.value.query.q).toBeUndefined()
  })

  it('resets the page when a filter changes', async () => {
    const { wrapper, router } = await mountAt('/claims?page=4')
    expect(wrapper.vm.page).toBe(4)
    wrapper.vm.status = 'draft'
    await flushPromises()
    expect(router.currentRoute.value.query.page).toBeUndefined()
    expect(wrapper.vm.page).toBe(1)
  })

  it('keeps the page when only the page changes', async () => {
    const { wrapper, router } = await mountAt('/claims?q=notice')
    wrapper.vm.page = 2
    await flushPromises()
    expect(router.currentRoute.value.query.q).toBe('notice')
    expect(router.currentRoute.value.query.page).toBe('2')
  })

  it('replaces rather than pushes, so Back leaves the register', async () => {
    const { wrapper, router } = await mountAt('/claims')
    const before = router.options.history.state.position
    expect(typeof before).toBe('number')
    wrapper.vm.q = 'notice'
    await flushPromises()
    expect(router.options.history.state.position).toBe(before)
    expect(router.options.history.state.replaced).toBe(true)
  })

  it('falls back to the default for a nonsense page number', async () => {
    const { wrapper } = await mountAt('/claims?page=banana')
    expect(wrapper.vm.page).toBe(1)
  })

  it('clear() returns every key to its default', async () => {
    const { wrapper, router } = await mountAt('/claims?q=notice&status=draft&page=5')
    wrapper.vm.clear()
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({})
    expect(wrapper.vm.isFiltered).toBe(false)
  })

  it('ignores the page when deciding whether anything is filtered', async () => {
    const { wrapper } = await mountAt('/claims?page=3')
    expect(wrapper.vm.isFiltered).toBe(false)
  })
})
