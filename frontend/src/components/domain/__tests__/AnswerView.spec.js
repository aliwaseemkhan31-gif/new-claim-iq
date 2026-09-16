import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AnswerView from '../AnswerView.vue'

const RouterLinkStub = { props: ['to'], template: '<a><slot /></a>' }

const payload = {
  answer: {
    summary: 'Notice must be given within 28 days.',
    confidence: 'medium',
    insufficient_evidence: false,
    called_model: true,
    findings: [
      {
        statement: 'The Contractor must give notice within 28 days.',
        status: 'fact',
        citations: [
          { is_knowledge_base: true, document_id: 'kb1', page_number: 31, rendered: '[1987 — Clause 53.1 — p.31]' },
        ],
      },
      { statement: 'The programme impact is not established.', status: 'unknown', citations: [] },
    ],
    missing_information: ['An updated programme.'],
    caveats: [],
  },
  sources: [
    { ref: 'S1', is_knowledge_base: true, document_id: 'kb1', page_number: 31, edition_label: 'FIDIC Red Book 1987', excerpt: 'standard text' },
    { ref: 'S2', document_id: 'd1', page_number: 4, document_title: 'Particular Conditions', excerpt: 'project text' },
  ],
  cited_refs: ['S1'],
}

function render() {
  return mount(AnswerView, { props: { payload }, global: { stubs: { RouterLink: RouterLinkStub } } })
}

describe('AnswerView', () => {
  it('lists the two corpora separately, never merged', () => {
    const text = render().text()
    expect(text).toContain('This project’s documents')
    expect(text).toContain('Standard form')
    expect(text.indexOf('This project’s documents')).toBeLessThan(text.indexOf('Standard form · FIDIC'))
  })

  it('marks sources that were read but not cited', () => {
    expect(render().text()).toContain('Read, not cited')
  })

  it('shows each finding with its epistemic status', () => {
    const text = render().text()
    expect(text).toContain('Fact')
    expect(text).toContain('Unknown')
  })

  it('says when a finding has no citation rather than implying support', () => {
    expect(render().text()).toContain('No citation for this statement')
  })

  it('surfaces what the sources did not establish', () => {
    expect(render().text()).toContain('An updated programme.')
  })

  it('reports an insufficient-evidence answer as such', () => {
    const wrapper = mount(AnswerView, {
      props: { payload: { ...payload, answer: { ...payload.answer, insufficient_evidence: true } } },
      global: { stubs: { RouterLink: RouterLinkStub } },
    })
    expect(wrapper.text()).toContain('Insufficient evidence')
  })
})
