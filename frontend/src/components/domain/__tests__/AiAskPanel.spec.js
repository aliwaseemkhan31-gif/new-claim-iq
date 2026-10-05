import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as aiApi from '@/api/ai'
import { useAuthStore } from '@/stores/auth'

import AiAskPanel from '../AiAskPanel.vue'

vi.mock('@/api/ai')

const replace = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useRoute: () => ({ query: {}, params: {} }),
}))

const PROJECT = '11111111-1111-4111-8111-111111111111'
const EDITION = 'red-book-2017'

function answer(overrides = {}) {
  return {
    id: 'q1',
    question: 'Within what period must notice of a claim be given?',
    project: null,
    edition_code: EDITION,
    answer: {
      summary: 'Twenty-eight days from awareness.',
      confidence: 'high',
      insufficient_evidence: false,
      findings: [],
      missing_information: [],
      caveats: [],
    },
    sources: [],
    cited_refs: [],
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()

  const auth = useAuthStore()
  auth.user = { id: 'u1', email: 'engineer@example.com' }
  auth.permissions = ['ai.query']

  aiApi.listQuestions.mockResolvedValue({ results: [] })
  aiApi.ask.mockResolvedValue(answer())
})

describe('AiAskPanel reading a standard form alone', () => {
  it('asks by edition, with no project', async () => {
    const wrapper = mount(AiAskPanel, {
      props: { edition: EDITION, editionLabel: 'FIDIC Red Book 2017' },
    })

    wrapper.vm.question = 'Within what period must notice of a claim be given?'
    await wrapper.vm.ask()

    expect(aiApi.ask).toHaveBeenCalledWith(
      expect.objectContaining({ edition: EDITION, projectId: null })
    )
  })

  it('reads its history by edition rather than by project', async () => {
    mount(AiAskPanel, { props: { edition: EDITION } })
    await vi.waitFor(() =>
      expect(aiApi.listQuestions).toHaveBeenCalledWith(
        { projectId: null, edition: EDITION },
        expect.anything()
      )
    )
  })

  it('does not offer the standard-form toggle, having nothing else to read', async () => {
    const wrapper = mount(AiAskPanel, {
      props: { edition: EDITION, editionLabel: 'FIDIC Red Book 2017' },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('FIDIC Red Book 2017')
  })

  it('will not ask without an edition to ask against', async () => {
    const wrapper = mount(AiAskPanel, { props: {} })
    wrapper.vm.question = 'Within what period must notice be given?'
    await wrapper.vm.$nextTick()

    expect(wrapper.vm.canAsk).toBe(false)
  })
})

describe('AiAskPanel reading a project', () => {
  it('asks about the project, carrying the standard-form toggle', async () => {
    aiApi.ask.mockResolvedValue(answer({ project: PROJECT, edition_code: null }))
    const wrapper = mount(AiAskPanel, { props: { projectId: PROJECT } })

    wrapper.vm.question = 'Was notice given in time on this job?'
    wrapper.vm.includeKnowledgeBase = false
    await wrapper.vm.ask()

    expect(aiApi.ask).toHaveBeenCalledWith(
      expect.objectContaining({ projectId: PROJECT, includeKnowledgeBase: false })
    )
  })

  it('still offers the standard-form toggle', async () => {
    const wrapper = mount(AiAskPanel, { props: { projectId: PROJECT } })
    await wrapper.vm.$nextTick()
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true)
  })
})
