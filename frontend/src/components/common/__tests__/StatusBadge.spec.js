import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatusBadge from '../StatusBadge.vue'

const toneOf = (wrapper) => wrapper.attributes('data-tone')

describe('StatusBadge — tone mapping', () => {
  it.each([
    ['approved', 'success'],
    ['active', 'success'],
    ['completed', 'success'],
    ['failed', 'danger'],
    ['rejected', 'danger'],
    ['pending', 'warning'],
    ['on_hold', 'warning'],
    ['processing', 'info'],
    ['in_review', 'info'],
    ['draft', 'neutral'],
    ['archived', 'neutral'],
  ])('maps %s to the %s tone', (status, tone) => {
    expect(toneOf(mount(StatusBadge, { props: { status } }))).toBe(tone)
  })

  it('falls back to neutral for an unrecognised status rather than guessing', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'awaiting_adjudication' } })
    expect(toneOf(wrapper)).toBe('neutral')
    expect(wrapper.classes()).toContain('badge--neutral')
  })

  it('normalises case, hyphens and spaces before looking the status up', () => {
    expect(toneOf(mount(StatusBadge, { props: { status: 'IN_REVIEW' } }))).toBe('info')
    expect(toneOf(mount(StatusBadge, { props: { status: 'in-review' } }))).toBe('info')
    expect(toneOf(mount(StatusBadge, { props: { status: 'In Review' } }))).toBe('info')
    expect(toneOf(mount(StatusBadge, { props: { status: '  active  ' } }))).toBe('success')
  })

  it('lets an explicit tone override the vocabulary', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'approved', tone: 'danger' } })
    expect(toneOf(wrapper)).toBe('danger')
  })

  it('is neutral when no status is supplied at all', () => {
    expect(toneOf(mount(StatusBadge))).toBe('neutral')
  })
})

describe('StatusBadge — label', () => {
  it('humanises the status into sentence case', () => {
    expect(mount(StatusBadge, { props: { status: 'in_review' } }).text()).toBe('In review')
    expect(mount(StatusBadge, { props: { status: 'partially_substantiated' } }).text()).toBe(
      'Partially substantiated'
    )
  })

  it('shows Unknown rather than an empty pill for a missing status', () => {
    expect(mount(StatusBadge, { props: { status: null } }).text()).toBe('Unknown')
    expect(mount(StatusBadge, { props: { status: '' } }).text()).toBe('Unknown')
  })

  it('prefers an explicit label over the humanised status', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'in_review', label: 'Awaiting DAB' } })
    expect(wrapper.text()).toBe('Awaiting DAB')
  })
})

describe('StatusBadge — presentation', () => {
  it('renders the dot by default and hides it on request', () => {
    expect(mount(StatusBadge, { props: { status: 'active' } }).find('.badge__dot').exists()).toBe(
      true
    )
    expect(
      mount(StatusBadge, { props: { status: 'active', dot: false } }).find('.badge__dot').exists()
    ).toBe(false)
  })

  it('applies the size modifier', () => {
    expect(mount(StatusBadge, { props: { status: 'active' } }).classes()).toContain('badge--md')
    expect(mount(StatusBadge, { props: { status: 'active', size: 'sm' } }).classes()).toContain(
      'badge--sm'
    )
  })
})
