/** Cross-corpus search: project documents plus the FIDIC knowledge bases. */
import { get, post } from './client'

/**
 * Hybrid (keyword + vector) search.
 *
 * `editions` matters: knowledge-base retrieval is edition-scoped by design —
 * a 1987 Red Book clause is not an answer to a 2017 contract question.
 */
export function search({ query, projectId, scopes, editions, page, pageSize } = {}, config = {}) {
  return post(
    '/search/',
    {
      query,
      project: projectId,
      scopes,
      editions,
      page,
      page_size: pageSize,
    },
    config
  )
}

export function suggest(query, params = {}) {
  return get('/search/suggest/', { params: { q: query, ...params } })
}

export function listSavedSearches(params = {}) {
  return get('/search/saved/', { params })
}

export function saveSearch(payload) {
  return post('/search/saved/', payload)
}

/** Knowledge base — the ingested FIDIC editions and their clauses. */
export function listKnowledgeBases(params = {}) {
  return get('/knowledge/bases/', { params })
}

export function listClauses(params = {}) {
  return get('/clauses/', { params })
}

export function fetchClause(clauseId) {
  return get(`/clauses/${clauseId}/`)
}
