import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'

import * as knowledgeApi from '@/api/knowledge'
import { useAuthStore } from '@/stores/auth'

import AiWorkspaceView from '../AiWorkspaceView.vue'

vi.mock('@/api/knowledge')
vi.mock('@/api/ai', () => ({
  ask: vi.fn(),
  listQuestions: vi.fn().mockResolvedValue({ results: [] }),
  fetchQuestion: vi.fn(),
}))
// The project picker fetches on mount; left real it reaches the network and
// every test inherits an unhandled rejection that has nothing to do with it.
vi.mock('@/api/projects', () => ({
  listProjects: vi.fn().mockResolvedValue({ count: 0, results: [] }),
  fetchProject: vi.fn(),
  listParties: vi.fn().mockResolvedValue([]),
}))

// A route whose query the test can drive, and a `replace` that writes to it —
// the component holds its scope in the URL, so the URL has to behave like one.
const route = reactive({ query: {}, params: {} })
const replace = vi.fn(({ query }) => {
  for (const key of Object.keys(route.query)) delete route.query[key]
  Object.assign(route.query, query)
})

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useRoute: () => route,
}))

function edition(code, label, retrievable = true) {
  return {
    code,
    label,
    form_code: 'red-book',
    year: 1987,
    knowledge_base: retrievable ? { id: 'kb1', status: 'published', is_retrievable: true } : null,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  for (const key of Object.keys(route.query)) delete route.query[key]

  const auth = useAuthStore()
  auth.user = { id: 'u1', email: 'engineer@example.com' }
  auth.permissions = ['ai.query']

  knowledgeApi.listEditions.mockResolvedValue({
    editions: [
      edition('red-book-1987', 'FIDIC Red Book 1987 (4th Edition)'),
      edition('yellow-book-2017', 'FIDIC Yellow Book 2017 (2nd Edition)'),
      edition('green-book-1999', 'FIDIC Green Book 1999', false),
    ],
  })
})

async function mountView() {
  const wrapper = mount(AiWorkspaceView, { global: { stubs: { Teleport: true } } })
  await vi.waitFor(() => expect(knowledgeApi.listEditions).toHaveBeenCalled())
  await wrapper.vm.$nextTick()
  return wrapper
}

describe('AiWorkspaceView scope', () => {
  it('starts in the project scope', async () => {
    const wrapper = await mountView()
    expect(wrapper.vm.scope).toBe('project')
    expect(wrapper.text()).toContain('Choose a project first')
  })

  it('stays in the standard-form scope before an edition is chosen', async () => {
    // The regression: switching scope writes `?edition=` with no value yet, and
    // reading that empty value as "no edition, therefore the project scope"
    // bounced the screen straight back to where it came from — the URL changed
    // and nothing else did.
    const wrapper = await mountView()
    wrapper.vm.chooseStandardFormScope()
    await wrapper.vm.$nextTick()

    expect('edition' in route.query).toBe(true)
    expect(wrapper.vm.scope).toBe('standard-form')
    expect(wrapper.text()).toContain('Choose an edition first')
  })

  it('offers only editions with a published knowledge base', async () => {
    const wrapper = await mountView()
    wrapper.vm.chooseStandardFormScope()
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.retrievable.map((e) => e.code)).toEqual([
      'red-book-1987',
      'yellow-book-2017',
    ])
  })

  it('does not choose an edition on the user behalf when several are published', async () => {
    const wrapper = await mountView()
    wrapper.vm.chooseStandardFormScope()
    await wrapper.vm.$nextTick()
    // ADR 0004: editions differ in ways that change the answer.
    expect(wrapper.vm.edition).toBeNull()
  })

  it('asks against the chosen edition once one is picked', async () => {
    const wrapper = await mountView()
    wrapper.vm.chooseStandardFormScope()
    await wrapper.vm.$nextTick()
    wrapper.vm.chooseEdition('red-book-1987')
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.scope).toBe('standard-form')
    expect(wrapper.vm.edition).toBe('red-book-1987')
    expect(wrapper.vm.editionLabel).toBe('FIDIC Red Book 1987 (4th Edition)')
  })

  it('leaves no edition behind when switching back to a project', async () => {
    const wrapper = await mountView()
    wrapper.vm.chooseStandardFormScope()
    wrapper.vm.chooseEdition('red-book-1987')
    await wrapper.vm.$nextTick()

    wrapper.vm.chooseProjectScope()
    await wrapper.vm.$nextTick()

    expect('edition' in route.query).toBe(false)
    expect(wrapper.vm.scope).toBe('project')
  })

  it('reads the scope from a pasted link', async () => {
    route.query.edition = 'red-book-1987'
    const wrapper = await mountView()
    expect(wrapper.vm.scope).toBe('standard-form')
    expect(wrapper.vm.edition).toBe('red-book-1987')
  })

  it('says so when nothing is published, rather than offering an empty picker', async () => {
    knowledgeApi.listEditions.mockResolvedValue({
      editions: [edition('red-book-1987', 'FIDIC Red Book 1987', false)],
    })
    const wrapper = await mountView()
    wrapper.vm.chooseStandardFormScope()
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('No standard form is published yet')
  })
})
