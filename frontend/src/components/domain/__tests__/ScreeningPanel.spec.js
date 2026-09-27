import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ScreeningPanel from '../ScreeningPanel.vue'

function check(overrides = {}) {
  return {
    code: 'NC6',
    question: 'Did each notice go to the party the provision names?',
    why: 'A notice to the Employer under a clause naming the Engineer is a live argument.',
    weight: 'advisory',
    status: 'incomplete',
    detail: 'A notice went to a party other than the one the provision names.',
    items: ['Clause 53.1 names the Engineer; the notice went to the Employer'],
    remedy: 'Record the correct recipient, or note why the addressee stands.',
    ...overrides,
  }
}

function screening(overrides = {}) {
  return {
    claim_type: 'eot',
    outcome: 'ready_with_queries',
    outcome_label: 'Assessable, with queries',
    summary: '0 blocking and 1 advisory item(s) outstanding.',
    caveat:
      'Screening reads what has been recorded about this claim. It is not a view on whether the claim succeeds.',
    blocking_outstanding: 0,
    advisory_outstanding: 1,
    areas: [
      {
        code: 'notice',
        label: 'Notice and procedural compliance',
        checks: [check(), check({ code: 'NC1', status: 'satisfied', detail: 'Periods run from 2026-08-12.', items: [] })],
      },
    ],
    ...overrides,
  }
}

function render(data = screening()) {
  return mount(ScreeningPanel, { props: { screening: data } })
}

describe('ScreeningPanel', () => {
  it('leads with the outcome and what is outstanding', () => {
    const wrapper = render()
    expect(wrapper.text()).toContain('Assessable, with queries')
    expect(wrapper.text()).toContain('0 blocking and 1 advisory item(s) outstanding.')
  })

  it('always shows the caveat that screening is not a view on the merits', () => {
    expect(render().text()).toContain('not a view on whether the claim succeeds')
  })

  it('shows outstanding checks and hides satisfied ones until asked', async () => {
    const wrapper = render()
    expect(wrapper.text()).toContain('Did each notice go to the party')
    expect(wrapper.text()).not.toContain('Periods run from 2026-08-12')

    await wrapper.find('button').trigger('click')
    expect(wrapper.text()).toContain('Periods run from 2026-08-12')
  })

  it('names the specific items a check objected to', () => {
    expect(render().text()).toContain('Clause 53.1 names the Engineer')
  })

  it('offers the remedy for an outstanding check', () => {
    expect(render().text()).toContain('Record the correct recipient')
  })

  it('marks a blocking check as blocking assessment', () => {
    const wrapper = render(
      screening({
        outcome: 'not_ready',
        outcome_label: 'Not yet assessable',
        areas: [
          {
            code: 'event',
            label: 'Event occurrence and causation',
            checks: [check({ code: 'EV1', weight: 'blocking', detail: 'No event date is recorded.' })],
          },
        ],
      }),
    )
    expect(wrapper.text()).toContain('Blocks assessment')
  })

  it('does not mark a satisfied blocking check as blocking', async () => {
    const wrapper = render(
      screening({
        areas: [
          {
            code: 'event',
            label: 'Event occurrence and causation',
            checks: [check({ code: 'EV1', weight: 'blocking', status: 'satisfied', items: [] })],
          },
        ],
      }),
    )
    await wrapper.find('button').trigger('click')
    expect(wrapper.text()).not.toContain('Blocks assessment')
  })

  it('says so plainly when nothing is outstanding', () => {
    const wrapper = render(
      screening({
        outcome: 'ready',
        outcome_label: 'Assessable',
        summary: '0 blocking and 0 advisory item(s) outstanding.',
        advisory_outstanding: 0,
        areas: [
          {
            code: 'notice',
            label: 'Notice and procedural compliance',
            checks: [check({ status: 'satisfied', items: [] })],
          },
        ],
      }),
    )
    expect(wrapper.text()).toContain('Every applicable check is satisfied')
  })

  it('shows a possible time bar as the most serious outcome', () => {
    const wrapper = render(
      screening({
        outcome: 'barred',
        outcome_label: 'Possible time bar — read the provision',
        areas: [
          {
            code: 'notice',
            label: 'Notice and procedural compliance',
            checks: [
              check({
                code: 'NC4',
                weight: 'blocking',
                status: 'barred',
                detail: 'Notice under Clause 20.2.1 was given late.',
                items: ['20.2.1'],
              }),
            ],
          },
        ],
      }),
    )
    expect(wrapper.text()).toContain('Possible time bar')
    expect(wrapper.text()).toContain('Possible bar')
  })
})

// The payload the server actually returns, captured from the Indus Highway
// N-55 claim. Guards the contract between the API and this component: a
// renamed field here is a blank panel in front of a user, and no unit test
// built from a hand-written fixture would catch it.
describe('ScreeningPanel against the live API payload', () => {
  it('renders what the server returns for the N-55 claim', async () => {
    const payload = (await import('./__fixtures__/screening.n55.json')).default
    const wrapper = mount(ScreeningPanel, { props: { screening: payload } })

    expect(wrapper.text()).toContain('Assessable, with queries')
    expect(wrapper.text()).toContain('0 blocking and 5 advisory item(s) outstanding.')
    expect(wrapper.text()).toContain('Clause 53.1 names the Engineer')
    expect(wrapper.text()).toContain('money is claimed on a claim type that does not normally seek it')
    expect(wrapper.text()).not.toContain('undefined')
  })
})
