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

function area(overrides = {}) {
  const checks = overrides.checks ?? [check()]
  return {
    code: 'notice',
    label: 'Notice and procedural compliance',
    outstanding: checks.filter((c) => ['incomplete', 'indeterminate', 'barred'].includes(c.status))
      .length,
    blocking_outstanding: checks.filter(
      (c) => c.weight === 'blocking' && ['incomplete', 'indeterminate', 'barred'].includes(c.status),
    ).length,
    barred: checks.filter((c) => c.status === 'barred').length,
    ...overrides,
    checks,
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
    areas: [area()],
    ...overrides,
  }
}

function render(data = screening(), props = {}) {
  return mount(ScreeningPanel, { props: { screening: data, ...props } })
}

/** The area tile that expands a group of checks. */
function areaTile(wrapper, index = 0) {
  return wrapper.findAll('.screen__area')[index]
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

  it('shows the five areas with their own state before any checks', () => {
    const wrapper = render(
      screening({
        areas: [
          area(),
          area({ code: 'relief', label: 'Quantum and relief claimed', checks: [check({ code: 'QR1', status: 'satisfied', items: [] })] }),
        ],
      }),
    )
    const tiles = wrapper.findAll('.screen__area')
    expect(tiles).toHaveLength(2)
    expect(tiles[0].text()).toContain('1 to check')
    expect(tiles[1].text()).toContain('All recorded')
  })

  it('opens an area that holds something blocking, and leaves the rest closed', () => {
    const blocking = area({
      code: 'event',
      label: 'Event occurrence and causation',
      checks: [check({ code: 'EV1', weight: 'blocking', detail: 'No event date is recorded.' })],
    })
    const wrapper = render(screening({ areas: [blocking, area()] }))

    // The blocking area is open on arrival; the advisory one is not.
    expect(wrapper.text()).toContain('No event date is recorded.')
    expect(wrapper.text()).not.toContain('A notice went to a party other than')
  })

  it('expands an area when its tile is clicked', async () => {
    const wrapper = render()
    expect(wrapper.text()).not.toContain('A notice went to a party other than')

    await areaTile(wrapper).trigger('click')
    expect(wrapper.text()).toContain('A notice went to a party other than')
    expect(wrapper.text()).toContain('Clause 53.1 names the Engineer')
    expect(wrapper.text()).toContain('Record the correct recipient')
  })

  it('hides satisfied checks until asked, then shows them', async () => {
    const wrapper = render(
      screening({
        areas: [
          area({
            checks: [
              check(),
              check({ code: 'NC1', status: 'satisfied', detail: 'Periods run from 2026-08-12.', items: [] }),
            ],
          }),
        ],
      }),
    )
    await areaTile(wrapper).trigger('click')
    expect(wrapper.text()).not.toContain('Periods run from 2026-08-12')

    await wrapper.find('.screen button.btn, .screen button').trigger('click')
    expect(wrapper.text()).toContain('Periods run from 2026-08-12')
  })

  it('marks a blocking check as blocking assessment', () => {
    const wrapper = render(
      screening({
        outcome: 'not_ready',
        outcome_label: 'Not yet assessable',
        areas: [
          area({
            code: 'event',
            label: 'Event occurrence and causation',
            checks: [check({ code: 'EV1', weight: 'blocking', detail: 'No event date is recorded.' })],
          }),
        ],
      }),
    )
    expect(wrapper.text()).toContain('Blocks assessment')
  })

  it('does not mark a satisfied blocking check as blocking', async () => {
    const wrapper = render(
      screening({
        areas: [
          area({
            checks: [check({ code: 'EV1', weight: 'blocking', status: 'satisfied', items: [] })],
          }),
        ],
      }),
    )
    await areaTile(wrapper).trigger('click')
    expect(wrapper.text()).not.toContain('Blocks assessment')
  })

  it('lifts a possible time bar out of its area, where it cannot be missed', () => {
    const wrapper = render(
      screening({
        outcome: 'barred',
        outcome_label: 'Possible time bar — read the provision',
        areas: [
          area({
            checks: [
              check({
                code: 'NC4',
                weight: 'blocking',
                status: 'barred',
                detail: 'Notice under Clause 20.2.1 was given late.',
                items: ['20.2.1'],
              }),
            ],
          }),
        ],
      }),
    )
    const alert = wrapper.find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('Possible time bar')
    expect(alert.text()).toContain('Notice under Clause 20.2.1 was given late.')
    expect(alert.text()).toContain('not a determination')
  })

  it('offers an action only where the remedy can be acted on', async () => {
    const wrapper = render(screening(), {
      actions: { NC6: { kind: 'tab', tab: 'notices', label: 'Go to Notice', icon: 'pi pi-arrow-right' } },
    })
    await areaTile(wrapper).trigger('click')

    const action = wrapper.findAll('button').find((b) => b.text().includes('Go to Notice'))
    expect(action).toBeTruthy()
    await action.trigger('click')
    expect(wrapper.emitted('act')[0][0]).toMatchObject({ kind: 'tab', tab: 'notices' })
  })

  it('shows no action when the caller offers none', async () => {
    const wrapper = render()
    await areaTile(wrapper).trigger('click')
    expect(wrapper.findAll('button').some((b) => b.text().includes('Go to Notice'))).toBe(false)
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
    expect(wrapper.findAll('.screen__area')).toHaveLength(5)
    expect(wrapper.text()).not.toContain('undefined')
    expect(wrapper.text()).not.toContain('NaN')
  })

  it('summarises each area of the live payload without opening it', async () => {
    const payload = (await import('./__fixtures__/screening.n55.json')).default
    const wrapper = mount(ScreeningPanel, { props: { screening: payload } })

    const tiles = wrapper.findAll('.screen__area').map((t) => t.text())
    expect(tiles.find((t) => t.includes('Notice'))).toContain('3 to check')
    expect(tiles.find((t) => t.includes('Contractual entitlement'))).toContain('All recorded')
    // Nothing blocks, so nothing opens on arrival.
    expect(wrapper.find('.screen__checks').exists()).toBe(false)
  })
})
