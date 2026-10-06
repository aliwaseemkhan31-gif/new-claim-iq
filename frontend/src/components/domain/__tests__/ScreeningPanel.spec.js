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

/**
 * The counts the server sends, computed the way the server computes them.
 *
 * Note `barred` and `lapsed` are counted separately and are NOT outstanding:
 * server-side `is_outstanding` is incomplete-or-indeterminate only. The panel
 * has its own, wider notion for what to show and in what order.
 */
function counts(checks) {
  const outstanding = (c) => ['incomplete', 'indeterminate'].includes(c.status)
  return {
    outstanding: checks.filter(outstanding).length,
    blocking_outstanding: checks.filter((c) => c.weight === 'blocking' && outstanding(c)).length,
    barred: checks.filter((c) => c.status === 'barred').length,
    lapsed: checks.filter((c) => c.status === 'lapsed').length,
  }
}

function area(overrides = {}) {
  const checks = overrides.checks ?? [check()]
  return {
    code: 'notice',
    label: 'Notice and procedural compliance',
    ...counts(checks),
    ...overrides,
    checks,
  }
}

/** A stage, with its counts rolled up from the areas it holds. */
function stage(overrides = {}) {
  const areas = overrides.areas ?? [area()]
  const all = areas.flatMap((a) => a.checks)
  return {
    code: 'notice',
    label: 'Notice',
    description: 'Whether the event was notified, in time and to the right party.',
    ...counts(all),
    ...overrides,
    areas,
  }
}

function screening(overrides = {}) {
  const stages = overrides.stages ?? [stage()]
  return {
    claim_type: 'eot',
    outcome: 'ready_with_queries',
    outcome_label: 'Assessable, with queries',
    summary: '0 blocking and 1 advisory item(s) outstanding.',
    caveat:
      'Screening reads what has been recorded about this claim. It is not a view on whether the claim succeeds.',
    blocking_outstanding: 0,
    advisory_outstanding: 1,
    areas: stages.flatMap((s) => s.areas),
    ...overrides,
    stages,
  }
}

function render(data = screening(), props = {}) {
  return mount(ScreeningPanel, { props: { screening: data, ...props } })
}

/** The stage tile that expands a group of checks. */
function stageTile(wrapper, index = 0) {
  return wrapper.findAll('.screen__stage')[index]
}

const eventStage = (checks) =>
  stage({
    code: 'event',
    label: 'Event',
    description: 'What happened, and when.',
    areas: [area({ code: 'event', label: 'Event occurrence and causation', checks })],
  })

describe('ScreeningPanel', () => {
  it('leads with the outcome and what is outstanding', () => {
    const wrapper = render()
    expect(wrapper.text()).toContain('Assessable, with queries')
    expect(wrapper.text()).toContain('0 blocking and 1 advisory item(s) outstanding.')
  })

  it('always shows the caveat that screening is not a view on the merits', () => {
    expect(render().text()).toContain('not a view on whether the claim succeeds')
  })

  it('shows each stage with its own state before any checks', () => {
    const wrapper = render(
      screening({
        stages: [
          stage(),
          stage({
            code: 'claim',
            label: 'Claim',
            areas: [
              area({
                code: 'relief',
                label: 'Quantum and relief claimed',
                checks: [check({ code: 'QR1', status: 'satisfied', items: [] })],
              }),
            ],
          }),
        ],
      }),
    )
    const tiles = wrapper.findAll('.screen__stage')
    expect(tiles).toHaveLength(2)
    expect(tiles[0].text()).toContain('1 to check')
    expect(tiles[1].text()).toContain('All recorded')
  })

  it('opens a stage that holds something blocking, and leaves the rest closed', () => {
    const blocking = eventStage([
      check({ code: 'EV1', weight: 'blocking', detail: 'No event date is recorded.' }),
    ])
    const wrapper = render(screening({ stages: [blocking, stage()] }))

    // The blocking stage is open on arrival; the advisory one is not.
    expect(wrapper.text()).toContain('No event date is recorded.')
    expect(wrapper.text()).not.toContain('A notice went to a party other than')
  })

  it('expands a stage when its tile is clicked', async () => {
    const wrapper = render()
    expect(wrapper.text()).not.toContain('A notice went to a party other than')

    await stageTile(wrapper).trigger('click')
    expect(wrapper.text()).toContain('A notice went to a party other than')
    expect(wrapper.text()).toContain('Clause 53.1 names the Engineer')
    expect(wrapper.text()).toContain('Record the correct recipient')
  })

  it('hides satisfied checks until asked, then shows them', async () => {
    const wrapper = render(
      screening({
        stages: [
          stage({
            areas: [
              area({
                checks: [
                  check(),
                  check({
                    code: 'NC1',
                    status: 'satisfied',
                    detail: 'Periods run from 2026-08-12.',
                    items: [],
                  }),
                ],
              }),
            ],
          }),
        ],
      }),
    )
    await stageTile(wrapper).trigger('click')
    expect(wrapper.text()).not.toContain('Periods run from 2026-08-12')

    await wrapper.find('.screen button.btn, .screen button').trigger('click')
    expect(wrapper.text()).toContain('Periods run from 2026-08-12')
  })

  it('marks a blocking check as blocking assessment', () => {
    const wrapper = render(
      screening({
        outcome: 'not_ready',
        outcome_label: 'Not yet assessable',
        stages: [
          eventStage([
            check({ code: 'EV1', weight: 'blocking', detail: 'No event date is recorded.' }),
          ]),
        ],
      }),
    )
    expect(wrapper.text()).toContain('Blocks assessment')
  })

  it('does not mark a satisfied blocking check as blocking', async () => {
    const wrapper = render(
      screening({
        stages: [
          stage({
            areas: [
              area({
                checks: [check({ code: 'EV1', weight: 'blocking', status: 'satisfied', items: [] })],
              }),
            ],
          }),
        ],
      }),
    )
    await stageTile(wrapper).trigger('click')
    expect(wrapper.text()).not.toContain('Blocks assessment')
  })

  it('lifts a possible time bar out of its stage, where it cannot be missed', () => {
    const wrapper = render(
      screening({
        outcome: 'barred',
        outcome_label: 'Possible time bar — read the provision',
        stages: [
          stage({
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
      actions: {
        NC6: { kind: 'tab', tab: 'notices', label: 'Go to Notice', icon: 'pi pi-arrow-right' },
      },
    })
    await stageTile(wrapper).trigger('click')

    const action = wrapper.findAll('button').find((b) => b.text().includes('Go to Notice'))
    expect(action).toBeTruthy()
    await action.trigger('click')
    expect(wrapper.emitted('act')[0][0]).toMatchObject({ kind: 'tab', tab: 'notices' })
  })

  it('shows no action when the caller offers none', async () => {
    const wrapper = render()
    await stageTile(wrapper).trigger('click')
    expect(wrapper.findAll('button').some((b) => b.text().includes('Go to Notice'))).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// The Event > Notice > Claim hierarchy
//
// The client requirement. The chain is the contract's own: FIDIC 2017
// Sub-Clause 20.2.4 makes the contractual basis, the records and the relief
// parts of the fully detailed Claim rather than steps beside it.
// ---------------------------------------------------------------------------

const claimStage = () =>
  stage({
    code: 'claim',
    label: 'Claim',
    description: 'What is being asked for, on what basis, and on what evidence.',
    areas: [
      area({
        code: 'entitlement',
        label: 'Contractual basis of the Claim',
        checks: [check({ code: 'CB2', detail: 'No provision is named.', items: [] })],
      }),
      area({
        code: 'records',
        label: 'Supporting evidence and contemporary records',
        checks: [check({ code: 'RC1', status: 'satisfied', detail: 'Four records.', items: [] })],
      }),
      area({
        code: 'relief',
        label: 'Quantum and relief claimed',
        checks: [check({ code: 'QR1', status: 'satisfied', detail: 'EOT of 30 days.', items: [] })],
      }),
    ],
  })

const fullChain = () =>
  screening({
    stages: [
      eventStage([check({ code: 'EV1', status: 'satisfied', detail: 'Occurred.', items: [] })]),
      stage(),
      claimStage(),
    ],
  })

describe('ScreeningPanel hierarchy', () => {
  it('shows three stages in the order event, notice, claim', () => {
    const tiles = render(fullChain())
      .findAll('.screen__stage')
      .map((t) => t.text())
    expect(tiles).toHaveLength(3)
    expect(tiles[0]).toContain('Event')
    expect(tiles[1]).toContain('Notice')
    expect(tiles[2]).toContain('Claim')
  })

  it('numbers the stages, because the order is the point', () => {
    const steps = render(fullChain())
      .findAll('.screen__step')
      .map((s) => s.text())
    expect(steps).toEqual(['1', '2', '3'])
  })

  it('draws an arrow between the stages but not after the last', () => {
    expect(render(fullChain()).findAll('.screen__arrow')).toHaveLength(2)
  })

  it('names the parts of the Claim on its tile, before anything is opened', () => {
    const claim = render(fullChain()).findAll('.screen__stage')[2]
    expect(claim.text()).toContain('Contractual basis')
    expect(claim.text()).toContain('Records')
    expect(claim.text()).toContain('Relief')
  })

  it('does not list parts on a stage that holds a single area', () => {
    const tiles = render(fullChain()).findAll('.screen__stage')
    expect(tiles[0].find('.screen__parts').exists()).toBe(false)
    expect(tiles[2].find('.screen__parts').exists()).toBe(true)
  })

  it('shows entitlement inside the Claim, not as a step of its own', async () => {
    const wrapper = render(fullChain())
    expect(wrapper.findAll('.screen__stage').map((t) => t.text()).join(' ')).not.toContain(
      'Entitlement',
    )

    await stageTile(wrapper, 2).trigger('click')
    const nested = wrapper.findAll('.screen__area-block.is-nested').map((b) => b.text())
    expect(nested).toHaveLength(3)
    expect(nested[0]).toContain('Contractual basis of the Claim')
  })

  it('opens every part of the Claim together, in basis then records then relief order', async () => {
    const wrapper = render(fullChain())
    await stageTile(wrapper, 2).trigger('click')
    const headings = wrapper.findAll('.screen__area-block .text-overline').map((h) => h.text())
    expect(headings).toEqual([
      'Contractual basis of the Claim',
      'Supporting evidence and contemporary records',
      'Quantum and relief claimed',
    ])
  })

  it('rolls a stage state up from the areas it holds', () => {
    const wrapper = render(
      screening({
        stages: [
          stage({
            code: 'claim',
            label: 'Claim',
            areas: [
              area({ code: 'entitlement', checks: [check({ status: 'satisfied', items: [] })] }),
              area({
                code: 'relief',
                checks: [check({ code: 'QR1', weight: 'blocking' })],
              }),
            ],
          }),
        ],
      }),
    )
    // One blocking check in one of two areas still makes the whole stage blocking.
    expect(stageTile(wrapper).text()).toContain('1 blocking')
  })

  it('does not indent the single area of the Event or Notice stage', async () => {
    const wrapper = render(fullChain())
    await stageTile(wrapper, 1).trigger('click')
    expect(wrapper.find('.screen__checks .screen__area-block.is-nested').exists()).toBe(false)
  })
})

describe('ScreeningPanel dependency links', () => {
  const gated = () =>
    screening({
      stages: [
        eventStage([
          check({
            code: 'EV3',
            status: 'indeterminate',
            detail: 'Cannot be determined until NC1 is resolved: No awareness date is recorded.',
            items: [],
          }),
        ]),
        stage({
          areas: [
            area({
              checks: [
                check({
                  code: 'NC1',
                  weight: 'blocking',
                  status: 'indeterminate',
                  detail: 'No awareness date is recorded.',
                  items: [],
                }),
              ],
            }),
          ],
        }),
      ],
    })

  it('turns the gating code into a link, because it lives in another stage', async () => {
    const wrapper = render(gated())
    await stageTile(wrapper, 0).trigger('click')
    const link = wrapper.findAll('.screen__dep')
    expect(link).toHaveLength(1)
    expect(link[0].text()).toBe('NC1')
  })

  it('keeps the rest of the detail readable around the link', async () => {
    const wrapper = render(gated())
    await stageTile(wrapper, 0).trigger('click')
    const text = wrapper.find('.screen__detail').text()
    expect(text).toContain('Cannot be determined until')
    expect(text).toContain('NC1')
    expect(text).toContain('No awareness date is recorded.')
  })

  it('opens the stage holding the dependency and marks it', async () => {
    const wrapper = render(gated())
    await stageTile(wrapper, 0).trigger('click')
    // The Notice stage is already open here (NC1 blocks), so assert the mark.
    await wrapper.find('.screen__dep').trigger('click')
    expect(wrapper.find('#screen-check-NC1').classes()).toContain('is-focused')
  })

  it('opens a closed stage to reveal the dependency', async () => {
    const data = gated()
    // Make NC1 satisfied so the Notice stage does not open by itself.
    data.stages[1].areas[0].checks[0].status = 'satisfied'
    data.stages[1].areas[0].blocking_outstanding = 0
    data.stages[1].blocking_outstanding = 0

    const wrapper = render(data)
    await stageTile(wrapper, 0).trigger('click')
    expect(wrapper.find('#screen-check-NC1').exists()).toBe(false)

    await wrapper.find('.screen__dep').trigger('click')
    expect(wrapper.find('#screen-check-NC1').exists()).toBe(true)
  })

  it('leaves an ordinary detail alone', async () => {
    const wrapper = render(fullChain())
    await stageTile(wrapper, 0).trigger('click')
    expect(wrapper.find('.screen__dep').exists()).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// The Sub-Clause 20.2.4 lapse
//
// A second mechanism that is adverse on the contract rather than on the state
// of the file. Not a time bar: the Notice of Claim is deemed to have lapsed
// for want of a timely statement of contractual basis, and the Engineer's
// failure to give Notice of that within 14 days reverses it.
// ---------------------------------------------------------------------------

const lapsedCheck = () =>
  check({
    code: 'DC3',
    weight: 'blocking',
    status: 'lapsed',
    question: 'Has the period for stating the contractual basis expired?',
    detail:
      'The period under Clause 20.2.4 expired on 2026-09-01 with no fully detailed Claim on record. The Engineer must give Notice of that within 14 days.',
    items: ['20.2.4'],
  })

const lapsedScreening = () =>
  screening({
    outcome: 'lapsed',
    outcome_label: 'Notice of Claim may have lapsed — read the provision',
    stages: [
      stage({
        code: 'claim',
        label: 'Claim',
        areas: [area({ code: 'submission', label: 'The Claim as submitted', checks: [lapsedCheck()] })],
      }),
    ],
  })

describe('ScreeningPanel lapse', () => {
  it('lifts a lapse out of its stage, as it does a time bar', () => {
    const alert = render(lapsedScreening()).find('[role="alert"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('Notice of Claim may have lapsed')
    expect(alert.text()).toContain('expired on 2026-09-01')
  })

  it('says the consequence depends on the provision, not on the screen', () => {
    expect(render(lapsedScreening()).find('[role="alert"]').text()).toContain(
      'not a determination',
    )
  })

  it('does not call a lapse a time bar', () => {
    const text = render(lapsedScreening()).text()
    expect(text).not.toContain('Possible time bar')
    expect(text).not.toContain('Possible bar')
  })

  it('labels the check itself as a possible lapse', () => {
    const wrapper = render(lapsedScreening())
    expect(wrapper.find('#screen-check-DC3').text()).toContain('Notice may have lapsed')
  })

  it('opens the stage holding a lapse on arrival', () => {
    expect(render(lapsedScreening()).find('.screen__checks').exists()).toBe(true)
  })

  it('marks the stage tile as adverse', () => {
    const tile = stageTile(render(lapsedScreening()))
    expect(tile.text()).toContain('Notice may have lapsed')
    expect(tile.classes()).toContain('is-danger')
  })

  it('treats a lapsed check as outstanding, so it survives the filter', () => {
    // Outstanding-only is the default view; a lapse must not be filtered out.
    expect(render(lapsedScreening()).find('#screen-check-DC3').exists()).toBe(true)
  })

  it('shows the submission area as a part of the Claim', () => {
    const wrapper = render(
      screening({
        stages: [
          stage({
            code: 'claim',
            label: 'Claim',
            areas: [
              area({ code: 'submission', label: 'The Claim as submitted', checks: [lapsedCheck()] }),
              area({ code: 'relief', label: 'Quantum and relief claimed', checks: [check({ code: 'QR1' })] }),
            ],
          }),
        ],
      }),
    )
    expect(stageTile(wrapper).text()).toContain('Submission')
  })
})

// A server not yet redeployed, or a cached bundle from before the hierarchy
// landed, sends `areas` without `stages`. The panel must still render the
// chain rather than going blank.
describe('ScreeningPanel without a stages key', () => {
  function legacy() {
    const data = fullChain()
    const areas = data.stages.flatMap((s) => s.areas)
    return { ...data, areas, stages: undefined }
  }

  it('derives the chain from the flat area list', () => {
    const tiles = render(legacy())
      .findAll('.screen__stage')
      .map((t) => t.text())
    expect(tiles).toHaveLength(3)
    expect(tiles[0]).toContain('Event')
    expect(tiles[2]).toContain('Claim')
  })

  it('rolls the derived stage counts up from the areas', () => {
    expect(stageTile(render(legacy()), 1).text()).toContain('1 to check')
  })

  it('drops a stage whose areas are all absent', () => {
    const data = legacy()
    data.areas = data.areas.filter((a) => a.code !== 'event')
    const tiles = render(data)
      .findAll('.screen__stage')
      .map((t) => t.text())
    expect(tiles).toHaveLength(2)
    expect(tiles[0]).toContain('Notice')
  })

  it('renders no undefined or NaN from the missing counts', () => {
    const text = render(legacy()).text()
    expect(text).not.toContain('undefined')
    expect(text).not.toContain('NaN')
  })
})

// The payload the server actually returns, captured from the Indus Highway
// N-55 claim. Guards the contract between the API and this component: a
// renamed field here is a blank panel in front of a user, and no unit test
// built from a hand-written fixture would catch it.
describe('ScreeningPanel against the live API payload', () => {
  const load = async () => (await import('./__fixtures__/screening.n55.json')).default

  it('renders what the server returns for the N-55 claim', async () => {
    const wrapper = mount(ScreeningPanel, { props: { screening: await load() } })

    expect(wrapper.text()).toContain('Assessable, with queries')
    expect(wrapper.text()).toContain('0 blocking and 5 advisory item(s) outstanding.')
    expect(wrapper.findAll('.screen__stage')).toHaveLength(3)
    expect(wrapper.text()).not.toContain('undefined')
    expect(wrapper.text()).not.toContain('NaN')
  })

  it('summarises each stage of the live payload without opening it', async () => {
    const wrapper = mount(ScreeningPanel, { props: { screening: await load() } })

    const tiles = wrapper.findAll('.screen__stage').map((t) => t.text())
    expect(tiles.find((t) => t.includes('Notice'))).toContain('3 to check')
    expect(tiles.find((t) => t.includes('Claim'))).toContain('Contractual basis')
    // Nothing blocks, so nothing opens on arrival.
    expect(wrapper.find('.screen__checks').exists()).toBe(false)
  })

  it('puts the live payload in event, notice, claim order', async () => {
    const tiles = mount(ScreeningPanel, { props: { screening: await load() } })
      .findAll('.screen__stage')
      .map((t) => t.text())
    expect(tiles[0]).toContain('Event')
    expect(tiles[1]).toContain('Notice')
    expect(tiles[2]).toContain('Claim')
  })
})
