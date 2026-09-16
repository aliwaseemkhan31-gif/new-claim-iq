/**
 * Hybrid search over a project's documents and the standard form for its
 * declared edition. Returns passages, not generated answers; every result
 * states its source layer.
 */
import { post } from './client'

export function search(
  { query, projectId, scopes, documentTypes, pageSize = 20 } = {},
  config = {}
) {
  return post(
    '/search/',
    {
      query,
      project: projectId,
      scopes,
      document_types: documentTypes,
      page_size: pageSize,
    },
    { timeout: 60000, ...config }
  )
}
