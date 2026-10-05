import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as claimsApi from '@/api/claims'
import * as projectsApi from '@/api/projects'
import { useAuthStore } from '@/stores/auth'
import { useProjectsStore } from '@/stores/projects'

import ClaimsPanel from '../ClaimsPanel.vue'

vi.mock('@/api/claims')
vi.mock('@/api/projects')

const push = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  useRoute: () => ({ query: {}, params: {} }),
}))

const PROJECT_A = '11111111-1111-4111-8111-111111111111'
const PROJECT_B = '22222222-2222-4222-8222-222222222222'

function claim(overrides = {}) {
  return {
    id: 'c1',
    project: PROJECT_A,
    project_name: 'Jaglot–Skardu Road',
    title: 'Delay to the river crossing',
    reference: 'CL-004',
    claim_type: 'eot',
    claim_type_label: 'Extension of Time',
    status: 'notified',
    human_outcome: 'unassessed',
    amount_claimed: null,
    currency: '',
    time_claimed_days: 42,
    has_awareness_date: true,
    contractual_basis: ['20.1'],
    updated_at: '2026-09-01T00:00:00Z',
    ...overrides,
  }
}

/** The register is a global one unless a project is given. */
function mountPanel(props = {}) {
  return mount(ClaimsPanel, {
    props: { showProject: true, ...props },
    global: { stubs: { Teleport: true } },
  })
}

async function settle(wrapper) {
  await vi.waitFor(() => expect(wrapper.find('table').exists()).toBe(true))
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()

  const auth = useAuthStore()
  auth.user = { id: 'u1', email: 'engineer@example.com' }
  auth.permissions = ['claim.create', 'claim.edit']

  const projects = useProjectsStore()
  projects.items = [
    { id: PROJECT_A, name: 'Jaglot–Skardu Road' },
    { id: PROJECT_B, name: 'Indus Highway N-55' },
  ]
  projects.loaded = true

  claimsApi.listClaims.mockResolvedValue({ count: 1, results: [claim()] })
  claimsApi.fetchScreeningSummary.mockResolvedValue({ results: {} })
  claimsApi.fetchClaim.mockResolvedValue(claim({ description: 'Flood on 3 March.' }))
  claimsApi.createClaim.mockResolvedValue(claim({ id: 'new-claim' }))
  claimsApi.updateClaim.mockResolvedValue(claim())
  projectsApi.listParties.mockResolvedValue([])
})

describe('ClaimsPanel across projects', () => {
  it('offers to create a claim even though no project is in scope', async () => {
    const wrapper = mountPanel()
    await settle(wrapper)

    const button = wrapper.findAll('button').find((b) => b.text().includes('New claim'))
    expect(button).toBeTruthy()
  })

  it('asks which project a new claim belongs to, and refuses to create one without it', async () => {
    const wrapper = mountPanel()
    await settle(wrapper)

    await wrapper.vm.openCreate()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('#claim-project').exists()).toBe(true)

    wrapper.vm.form.title = 'Standing time on the crusher'
    await wrapper.vm.submitForm()

    expect(claimsApi.createClaim).not.toHaveBeenCalled()
    expect(wrapper.vm.fieldErrors.project).toBeTruthy()
  })

  it('creates the claim against the project chosen in the form', async () => {
    const wrapper = mountPanel()
    await settle(wrapper)

    await wrapper.vm.openCreate()
    wrapper.vm.form.title = 'Standing time on the crusher'
    wrapper.vm.form.project = PROJECT_B
    await wrapper.vm.$nextTick()
    await wrapper.vm.submitForm()

    expect(claimsApi.createClaim).toHaveBeenCalledWith(
      expect.objectContaining({ project: PROJECT_B, title: 'Standing time on the crusher' })
    )
  })

  it('does not ask for a project when the register is already inside one', async () => {
    const wrapper = mountPanel({ projectId: PROJECT_A, showProject: false })
    await settle(wrapper)

    await wrapper.vm.openCreate()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('#claim-project').exists()).toBe(false)
    expect(wrapper.vm.form.project).toBe(PROJECT_A)
  })
})

describe('ClaimsPanel editing', () => {
  it('re-reads the claim rather than editing the register row', async () => {
    const wrapper = mountPanel()
    await settle(wrapper)

    await wrapper.vm.openEdit(claim())
    expect(claimsApi.fetchClaim).toHaveBeenCalledWith('c1')
    // The description is absent from the register row and present on the form,
    // which is the point of re-reading: saving must not blank it.
    expect(wrapper.vm.form.description).toBe('Flood on 3 March.')
    expect(wrapper.vm.form.contractual_basis).toBe('20.1')
  })

  it('saves an edit as a patch on the existing claim', async () => {
    const wrapper = mountPanel()
    await settle(wrapper)

    await wrapper.vm.openEdit(claim())
    wrapper.vm.form.title = 'Delay to the river crossing (revised)'
    await wrapper.vm.submitForm()

    expect(claimsApi.createClaim).not.toHaveBeenCalled()
    expect(claimsApi.updateClaim).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({ title: 'Delay to the river crossing (revised)' })
    )
  })

  it('sends a cleared date as null, so emptying a field clears it', async () => {
    claimsApi.fetchClaim.mockResolvedValue(claim({ awareness_date: '2026-03-03' }))
    const wrapper = mountPanel()
    await settle(wrapper)

    await wrapper.vm.openEdit(claim())
    expect(wrapper.vm.form.awareness_date).toBe('2026-03-03')
    wrapper.vm.form.awareness_date = ''
    await wrapper.vm.submitForm()

    expect(claimsApi.updateClaim).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({ awareness_date: null })
    )
  })

  it('moves the claim when the project is changed, and warns before it does', async () => {
    const wrapper = mountPanel()
    await settle(wrapper)

    await wrapper.vm.openEdit(claim())
    expect(wrapper.vm.movingProject).toBe(false)

    wrapper.vm.form.project = PROJECT_B
    await wrapper.vm.$nextTick()
    expect(wrapper.vm.movingProject).toBe(true)
    expect(wrapper.text()).toContain('Indus Highway N-55')

    await wrapper.vm.submitForm()
    expect(claimsApi.updateClaim).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({ project: PROJECT_B })
    )
  })

  it('clears a party that does not exist on the project being moved to', async () => {
    projectsApi.listParties.mockResolvedValueOnce([{ id: 'p1', name: 'Contractor', role: 'contractor' }])
    claimsApi.fetchClaim.mockResolvedValue(claim({ claimant: 'p1' }))

    const wrapper = mountPanel()
    await settle(wrapper)

    await wrapper.vm.openEdit(claim())
    expect(wrapper.vm.form.claimant).toBe('p1')

    // The destination project has parties of its own; p1 is not among them.
    projectsApi.listParties.mockResolvedValue([{ id: 'p9', name: 'Employer', role: 'employer' }])
    wrapper.vm.form.project = PROJECT_B
    await vi.waitFor(() => expect(wrapper.vm.form.claimant).toBe(''))
  })
})
