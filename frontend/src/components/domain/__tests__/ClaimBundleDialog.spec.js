import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as claimsApi from '@/api/claims'

import ClaimBundleDialog from '../ClaimBundleDialog.vue'

vi.mock('@/api/claims', () => ({
  listClaims: vi.fn(),
  readClaimBundle: vi.fn(),
  applyClaimBundle: vi.fn(),
}))

const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const KINDS = [
  { code: 'claim_letter', label: 'Claim letter', element: null },
  { code: 'notice', label: 'Notice', element: 'notice' },
  { code: 'programme', label: 'Programme', element: 'time_impact' },
  { code: 'invoice_cost', label: 'Invoice', element: 'cost_impact' },
]

function segment(first, last, kind, extra = {}) {
  const element = KINDS.find((k) => k.code === kind)?.element ?? null
  return { first_page: first, last_page: last, pages: last - first + 1, kind, title: '', date: null, element, ...extra }
}

const READING = {
  document: { id: 'doc-1', title: 'bundle.pdf' },
  pages_read: 6,
  model: 'qwen2.5:7b-instruct',
  notes: [],
  kinds: KINDS,
  segments: [
    segment(1, 2, 'claim_letter', { title: 'Claim for extension of time', date: '1999-03-15' }),
    segment(3, 3, 'notice', { date: '1997-06-10' }),
    segment(4, 6, 'programme'),
  ],
  draft: {
    fields: [
      { name: 'title', value: 'Claim for extension of time', found: true, quote_verified: true },
      { name: 'claim_type', value: 'eot', found: true, quote_verified: true },
      { name: 'submission_date', value: '1999-03-15', found: true, quote_verified: true },
      { name: 'time_claimed_days', value: 420, found: true, quote_verified: true },
      { name: 'contractual_basis', value: ['44.2'], found: true, quote_verified: true },
    ],
  },
}

async function openAndRead() {
  const wrapper = mount(ClaimBundleDialog, {
    props: { modelValue: false, projectId: 'p1' },
    global: { stubs: { teleport: true } },
  })
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  const input = wrapper.find('#bundle-file')
  Object.defineProperty(input.element, 'files', { value: [new File(['%PDF'], 'bundle.pdf')] })
  await input.trigger('change')
  const upload = wrapper.findAll('button').find((b) => b.text().includes('Upload and split'))
  await upload.trigger('click')
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  claimsApi.listClaims.mockResolvedValue({ count: 0, results: [] })
  claimsApi.readClaimBundle.mockResolvedValue(structuredClone(READING))
  claimsApi.applyClaimBundle.mockResolvedValue({ claim: 'c9', letter: true, notices: 1, evidence: 2 })
})

describe('ClaimBundleDialog', () => {
  it('shows each part of the bundle for review, letter first', async () => {
    const wrapper = await openAndRead()
    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[0].classes()).toContain('is-letter')
    expect(wrapper.find('#b-date').element.value).toBe('1999-03-15')
    expect(wrapper.find('#bc-title').element.value).toBe('Claim for extension of time')
  })

  it('splits and joins parts without losing a page', async () => {
    const wrapper = await openAndRead()
    const splitButtons = wrapper.findAll('button[title="Split this part in two"]')
    await splitButtons[2].trigger('click') // programme, pages 4–6
    let rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(4)

    const joinButtons = wrapper.findAll('button[title="Join with the next part"]')
    await joinButtons[2].trigger('click')
    rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(wrapper.text()).not.toContain('Pages not in any part')
  })

  it('files the reviewed split on a new claim and opens its checklist', async () => {
    const wrapper = await openAndRead()
    await wrapper.find('#b-clause').setValue('44.2')
    const apply = wrapper.findAll('button').find((b) => b.text().includes('Create claim and file'))
    await apply.trigger('click')
    await flushPromises()

    const payload = claimsApi.applyClaimBundle.mock.calls[0][0]
    expect(payload.document).toBe('doc-1')
    expect(payload.letter_date).toBe('1999-03-15')
    expect(payload.clause_number).toBe('44.2')
    expect(payload.new_claim).toMatchObject({ title: 'Claim for extension of time', claim_type: 'eot', contractual_basis: ['44.2'] })
    expect(payload.segments.map((s) => [s.first_page, s.last_page, s.kind, s.element])).toEqual([
      [1, 2, 'claim_letter', null],
      [3, 3, 'notice', 'notice'],
      [4, 6, 'programme', 'time_impact'],
    ])
    expect(push).toHaveBeenCalledWith({ name: 'claim-detail', params: { claimId: 'c9' }, query: { tab: 'checklist' } })
  })
})
