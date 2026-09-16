/**
 * Source layers and citation navigation.
 *
 * Two corpora are never presented as one: a project's own documents, and the
 * standard form for its declared edition. These helpers are the single place
 * the UI decides which a source is, how it is labelled, and where a citation
 * opens.
 */
export const LAYER_PROJECT = 'project_document'
export const LAYER_STANDARD_FORM = 'standard_form'

export function sourceLayer(source) {
  if (!source) return null
  if (source.layer === LAYER_PROJECT || source.layer === LAYER_STANDARD_FORM) return source.layer
  return source.is_knowledge_base ? LAYER_STANDARD_FORM : LAYER_PROJECT
}

export function layerLabel(source) {
  const layer = sourceLayer(source)
  if (layer === LAYER_STANDARD_FORM) {
    const edition = source.edition_label || source.edition || source.edition_code
    return edition ? `Standard form · ${edition}` : 'Standard form'
  }
  if (layer === LAYER_PROJECT) return 'Project document'
  return 'Unknown source'
}

/**
 * Where a citation opens. Returns null when the citation cannot be located —
 * the UI then shows the citation text without a link rather than a dead one.
 */
export function citationRoute(citation) {
  if (!citation) return null
  const page = Math.max(1, Number.parseInt(citation.page_number, 10) || 1)
  const query = { page: String(page) }
  if (citation.quotation) query.q = String(citation.quotation).slice(0, 300)

  if (sourceLayer(citation) === LAYER_STANDARD_FORM) {
    const id = citation.knowledge_base_id || citation.document_id
    return id ? { name: 'knowledge-base-viewer', params: { knowledgeBaseId: id }, query } : null
  }
  return citation.document_id
    ? { name: 'document-viewer', params: { documentId: citation.document_id }, query }
    : null
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Split `text` around the first occurrence of `needle` for highlighting.
 *
 * Whitespace-insensitive and case-insensitive, because extracted text breaks
 * lines where the quotation does not. Returns a single unmatched segment when
 * the needle is absent — the page is still shown, just without a highlight.
 */
export function highlightSegments(text, needle) {
  const source = text ?? ''
  const wanted = (needle ?? '').trim()
  if (!source || wanted.length < 3) return [{ text: source, match: false }]

  const pattern = wanted.split(/\s+/).map(escapeRegExp).join('\\s+')
  const match = new RegExp(pattern, 'i').exec(source)
  if (!match) return [{ text: source, match: false }]

  const segments = []
  if (match.index > 0) segments.push({ text: source.slice(0, match.index), match: false })
  segments.push({ text: match[0], match: true })
  const end = match.index + match[0].length
  if (end < source.length) segments.push({ text: source.slice(end), match: false })
  return segments
}

export function clampPage(page, pageCount) {
  const value = Number.parseInt(page, 10)
  const count = Number.parseInt(pageCount, 10)
  if (!Number.isFinite(value) || value < 1) return 1
  if (Number.isFinite(count) && count > 0 && value > count) return count
  return value
}

/** Rows from a paginated envelope or a bare array. */
export function rowsOf(data) {
  if (!data) return []
  return Array.isArray(data) ? data : (data.results ?? [])
}
