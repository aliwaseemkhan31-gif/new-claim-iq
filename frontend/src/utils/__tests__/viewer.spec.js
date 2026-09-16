import { describe, expect, it } from 'vitest'

import {
  LAYER_PROJECT,
  LAYER_STANDARD_FORM,
  citationRoute,
  clampPage,
  highlightSegments,
  layerLabel,
  rowsOf,
  sourceLayer,
} from '../viewer'

describe('source layers', () => {
  it('prefers an explicit layer and falls back to the knowledge-base flag', () => {
    expect(sourceLayer({ layer: LAYER_STANDARD_FORM })).toBe(LAYER_STANDARD_FORM)
    expect(sourceLayer({ is_knowledge_base: true })).toBe(LAYER_STANDARD_FORM)
    expect(sourceLayer({ is_knowledge_base: false })).toBe(LAYER_PROJECT)
    expect(sourceLayer(null)).toBeNull()
  })

  it('labels standard-form sources with their edition, never generically', () => {
    expect(layerLabel({ layer: LAYER_STANDARD_FORM, edition_label: 'FIDIC Red Book 1987' })).toBe(
      'Standard form · FIDIC Red Book 1987'
    )
    expect(layerLabel({ is_knowledge_base: true, edition: 'red-book-2017' })).toBe(
      'Standard form · red-book-2017'
    )
    expect(layerLabel({ layer: LAYER_PROJECT })).toBe('Project document')
  })
})

describe('citationRoute', () => {
  it('opens a project document at the cited page with the quotation', () => {
    expect(
      citationRoute({ document_id: 'd1', page_number: 12, quotation: 'within 28 days' })
    ).toEqual({
      name: 'document-viewer',
      params: { documentId: 'd1' },
      query: { page: '12', q: 'within 28 days' },
    })
  })

  it('opens standard-form text in the knowledge-base viewer', () => {
    expect(citationRoute({ layer: LAYER_STANDARD_FORM, knowledge_base_id: 'kb1', page_number: 3 })).toEqual({
      name: 'knowledge-base-viewer',
      params: { knowledgeBaseId: 'kb1' },
      query: { page: '3' },
    })
  })

  it('accepts the analysis payload, where a knowledge-base source id is in document_id', () => {
    expect(citationRoute({ is_knowledge_base: true, document_id: 'kb2', page_number: 5 }).params).toEqual({
      knowledgeBaseId: 'kb2',
    })
  })

  it('returns null rather than a dead link when the source is unknown', () => {
    expect(citationRoute({ ref: 'S9', rendered: null })).toBeNull()
    expect(citationRoute(null)).toBeNull()
  })

  it('defaults an absent or invalid page to 1', () => {
    expect(citationRoute({ document_id: 'd1' }).query.page).toBe('1')
    expect(citationRoute({ document_id: 'd1', page_number: 'x' }).query.page).toBe('1')
  })
})

describe('highlightSegments', () => {
  it('matches across line breaks and case differences', () => {
    const segments = highlightSegments('The Contractor shall give\nNotice to the Engineer.', 'give notice')
    expect(segments.map((s) => s.match)).toEqual([false, true, false])
    expect(segments[1].text).toBe('give\nNotice')
  })

  it('returns the whole text unhighlighted when the quotation is absent', () => {
    expect(highlightSegments('abc def', 'xyz')).toEqual([{ text: 'abc def', match: false }])
    expect(highlightSegments('abc def', '')).toEqual([{ text: 'abc def', match: false }])
  })

  it('treats regular-expression characters in the quotation literally', () => {
    const segments = highlightSegments('Sub-Clause 20.2.1 (a) applies', '20.2.1 (a)')
    expect(segments.find((s) => s.match).text).toBe('20.2.1 (a)')
  })
})

describe('helpers', () => {
  it('clamps page numbers into range', () => {
    expect(clampPage('0', 10)).toBe(1)
    expect(clampPage(99, 10)).toBe(10)
    expect(clampPage('4', null)).toBe(4)
  })

  it('reads rows from paginated or bare responses', () => {
    expect(rowsOf({ results: [1] })).toEqual([1])
    expect(rowsOf([2])).toEqual([2])
    expect(rowsOf(null)).toEqual([])
  })
})
