import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FindingCard from '../FindingCard.vue'

const reviewFinding = vi.fn()
vi.mock('@/api/ai', () => ({ reviewFinding: (...args) => reviewFinding(...args) }))

const RouterLinkStub = { props: ['to'], template: '<a><slot /></a>' }

function finding(overrides = {}) {
  return {
    id: 'f1',
    statement: 'Notice was given on day 19 of the 28-day period.',
    effective_statement: 'Notice was given on day 19 of the 28-day period.',
    epistemic_status: 'fact',
    citations: [{ document_id: 'd1', page_number: 3, rendered: '[Contract — p.3]' }],
    review: { state: 'unreviewed', history_length: 0, latest: null },
    ...overrides,
  }
}

function render(props = {}) {
  return mount(FindingCard, {
    props: { finding: finding(), canReview: true, ...props },
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

describe('FindingCard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    reviewFinding.mockReset()
  })

  it('marks an unreviewed finding as unreviewed', () => {
    expect(render().text()).toContain('Unreviewed AI finding')
  })

  it('hides the review actions from a user who cannot review', () => {
    const wrapper = render({ canReview: false })
    expect(wrapper.text()).not.toContain('Accept')
  })

  it('requires a reason of at least ten characters to reject', async () => {
    const wrapper = render()
    await wrapper.findAll('button').find((button) => button.text().includes('Reject')).trigger('click')

    const submit = () => wrapper.findAll('button').find((b) => b.text().includes('Reject finding'))
    expect(submit().attributes('disabled')).toBeDefined()

    await wrapper.find('textarea').setValue('too short')
    expect(submit().attributes('disabled')).toBeDefined()

    await wrapper.find('textarea').setValue('The notice relates to a different event.')
    expect(submit().attributes('disabled')).toBeUndefined()
  })

  it('sends the review and emits the updated finding', async () => {
    const updated = { ...finding(), review: { state: 'accepted', latest: null } }
    reviewFinding.mockResolvedValue(updated)

    const wrapper = render()
    await wrapper.findAll('button').find((button) => button.text().includes('Accept')).trigger('click')
    await wrapper.findAll('button').find((b) => b.text().includes('Accept finding')).trigger('click')
    await new Promise((resolve) => setTimeout(resolve))

    expect(reviewFinding).toHaveBeenCalledWith('f1', {
      action: 'accept',
      reason: '',
      amendedStatement: '',
    })
    expect(wrapper.emitted('reviewed')[0]).toEqual([updated])
  })

  it('keeps the AI statement visible beside a reviewer amendment', () => {
    const wrapper = render({
      finding: finding({
        review: { state: 'amended', latest: { reason: 'Receipt governs.', reviewer_email: 'a@b.c' } },
        effective_statement: 'Notice was received on day 21.',
      }),
    })
    expect(wrapper.text()).toContain('Notice was given on day 19 of the 28-day period.')
    expect(wrapper.text()).toContain('Notice was received on day 21.')
    expect(wrapper.text()).toContain('Receipt governs.')
  })

  it('says plainly when a statement carries no citation', () => {
    const wrapper = render({ finding: finding({ citations: [] }) })
    expect(wrapper.text()).toContain('No citation')
  })
})
