import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ClaimChecklist from '../ClaimChecklist.vue'

function row(overrides = {}) {
  return { key: 'event_date', label: 'Date of the event', status: 'ok', detail: '', ...overrides }
}

function checklist(sections) {
  return { counts: {}, outstanding: 0, complete: false, sections }
}

const SAMPLE = checklist([
  {
    code: 'event',
    label: 'Event',
    outstanding: 1,
    rows: [row(), row({ key: 'awareness_date', label: 'Date aware', status: 'missing' })],
  },
  {
    code: 'notice',
    label: 'Notice',
    outstanding: 1,
    rows: [
      row({
        key: 'deadline:44.2:notice_of_claim',
        label: 'Notice of the delay event (Sub-Clause 44.2(a))',
        status: 'late',
        detail: 'Given 2026-03-02, 4 day(s) after the deadline.',
        rule: 'Clause 44.2: 28 calendar days (as amended: Particular Conditions, p. 12)',
        is_amended: true,
      }),
    ],
  },
  {
    code: 'claim',
    label: 'Claim submission',
    outstanding: 1,
    rows: [row({ key: 'claim_document', label: 'Claim document uploaded', status: 'missing' })],
  },
  {
    code: 'evidence',
    label: 'Supporting evidence',
    outstanding: 0,
    rows: [row({ key: 'evidence:time_impact', label: 'Time impact', status: 'missing' })],
  },
])

describe('ClaimChecklist', () => {
  it('says which deadlines the contract itself amends', () => {
    const wrapper = mount(ClaimChecklist, { props: { checklist: SAMPLE } })
    expect(wrapper.text()).toContain('amended by contract')
    expect(wrapper.text()).toContain('Particular Conditions, p. 12')
  })

  it('offers nothing to fix without edit rights', () => {
    const wrapper = mount(ClaimChecklist, { props: { checklist: SAMPLE } })
    expect(wrapper.findAll('.checklist__action')).toHaveLength(0)
  })

  it('sends each outstanding row to the place that fixes it', async () => {
    const wrapper = mount(ClaimChecklist, { props: { checklist: SAMPLE, canEdit: true } })
    const buttons = wrapper.findAll('.checklist__action')
    // The recorded event date is done, so it has no action.
    expect(buttons.map((b) => b.text())).toEqual([
      'Record the date',
      'Add notice',
      'Add claim document',
      'Add document',
    ])

    for (const button of buttons) await button.trigger('click')
    expect(wrapper.emitted('act').map(([action]) => action)).toEqual([
      { kind: 'edit', label: 'Record the date' },
      { kind: 'file', role: 'notice', label: 'Add notice' },
      { kind: 'file', role: 'claim_submission', label: 'Add claim document' },
      { kind: 'file', role: 'supporting', element: 'time_impact', label: 'Add document' },
    ])
  })
})
