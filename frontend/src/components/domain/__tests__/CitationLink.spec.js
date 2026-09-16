import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CitationLink from '../CitationLink.vue'
import SourceLayerBadge from '../SourceLayerBadge.vue'

const RouterLinkStub = {
  props: ['to'],
  template: '<a :data-to="JSON.stringify(to)"><slot /></a>',
}

function render(citation, props = {}) {
  return mount(CitationLink, {
    props: { citation, ...props },
    global: { stubs: { RouterLink: RouterLinkStub } },
  })
}

describe('CitationLink', () => {
  it('links a project-document citation to the viewer at its page', () => {
    const wrapper = render({
      document_id: 'doc-1',
      page_number: 12,
      rendered: '[Contract — Clause 8.5 — p.12]',
      quotation: 'within 28 days',
    })
    const to = JSON.parse(wrapper.find('a').attributes('data-to'))
    expect(to).toEqual({
      name: 'document-viewer',
      params: { documentId: 'doc-1' },
      query: { page: '12', q: 'within 28 days' },
    })
    expect(wrapper.text()).toContain('[Contract — Clause 8.5 — p.12]')
  })

  it('links standard-form text to the knowledge-base viewer', () => {
    const wrapper = render({ is_knowledge_base: true, document_id: 'kb-1', page_number: 31 })
    const to = JSON.parse(wrapper.find('a').attributes('data-to'))
    expect(to.name).toBe('knowledge-base-viewer')
    expect(to.params).toEqual({ knowledgeBaseId: 'kb-1' })
  })

  it('shows the source layer so the two corpora are never confused', () => {
    const standard = render({ is_knowledge_base: true, document_id: 'kb-1', edition_label: 'FIDIC Red Book 1987' })
    expect(standard.findComponent(SourceLayerBadge).text()).toContain('Standard form · FIDIC Red Book 1987')

    const project = render({ document_id: 'doc-1' })
    expect(project.findComponent(SourceLayerBadge).text()).toContain('Project document')
  })

  it('renders unlinkable citations as text, never as a dead link', () => {
    const wrapper = render({ ref: 'S4', rendered: '[Unknown source]' })
    expect(wrapper.find('a').exists()).toBe(false)
    expect(wrapper.text()).toContain('source not available')
  })

  it('quotes the cited passage, and can be asked not to', () => {
    expect(render({ document_id: 'd', quotation: 'exactly this' }).text()).toContain('exactly this')
    expect(
      render({ document_id: 'd', quotation: 'exactly this' }, { showQuotation: false }).text()
    ).not.toContain('exactly this')
  })

  it('falls back to a readable label when the server sent none', () => {
    const wrapper = render({ document_id: 'd', document_title: 'Particular Conditions', clause_number: '20.1', page_number: 7 })
    expect(wrapper.text()).toContain('Particular Conditions — Clause 20.1 — p.7')
  })
})
