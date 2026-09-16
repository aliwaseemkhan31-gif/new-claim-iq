/**
 * Knowledge bases: standard-form contract editions shared across the
 * organization. Upload → process → validate → publish; only published editions
 * are retrievable.
 */
import { API_BASE_URL, del, get, post } from './client'

export function listEditions(config = {}) {
  return get('/knowledge/editions/', config)
}

export function listKnowledgeBases(params = {}, config = {}) {
  return get('/knowledge/bases/', { params, ...config })
}

export function fetchKnowledgeBase(knowledgeBaseId, config = {}) {
  return get(`/knowledge/bases/${knowledgeBaseId}/`, config)
}

function uploadConfig(onProgress) {
  return {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) return
      onProgress(Math.round((event.loaded * 100) / event.total))
    },
  }
}

export function createKnowledgeBase({ file, editionCode, name, description, onProgress }) {
  const form = new FormData()
  form.append('file', file)
  form.append('edition_code', editionCode)
  if (name) form.append('name', name)
  if (description) form.append('description', description)
  return post('/knowledge/bases/', form, uploadConfig(onProgress))
}

export function replaceKnowledgeBaseSource(knowledgeBaseId, { file, onProgress }) {
  const form = new FormData()
  form.append('file', file)
  return post(`/knowledge/bases/${knowledgeBaseId}/replace-source/`, form, uploadConfig(onProgress))
}

export function validateKnowledgeBase(knowledgeBaseId) {
  return post(`/knowledge/bases/${knowledgeBaseId}/validate/`, {}, { timeout: 0 })
}

export function publishKnowledgeBase(knowledgeBaseId) {
  return post(`/knowledge/bases/${knowledgeBaseId}/publish/`, {})
}

export function unpublishKnowledgeBase(knowledgeBaseId) {
  return post(`/knowledge/bases/${knowledgeBaseId}/unpublish/`, {})
}

export function deleteKnowledgeBase(knowledgeBaseId) {
  return del(`/knowledge/bases/${knowledgeBaseId}/`)
}

export function listKnowledgeBaseClauses(knowledgeBaseId, config = {}) {
  return get(`/knowledge/bases/${knowledgeBaseId}/clauses/`, config)
}

export function fetchKnowledgeBaseClause(knowledgeBaseId, clauseNumber, config = {}) {
  return get(`/knowledge/bases/${knowledgeBaseId}/clauses/`, {
    params: { number: clauseNumber },
    ...config,
  })
}

export function listKnowledgeBaseChunks(knowledgeBaseId, params = {}, config = {}) {
  return get(`/knowledge/bases/${knowledgeBaseId}/chunks/`, { params, ...config })
}

export function fetchKnowledgeBasePage(knowledgeBaseId, pageNumber, config = {}) {
  return get(`/knowledge/bases/${knowledgeBaseId}/pages/${pageNumber}/`, config)
}

export function knowledgeBasePageImageUrl(knowledgeBaseId, pageNumber, scale = 1.5) {
  return `${API_BASE_URL}/knowledge/bases/${knowledgeBaseId}/pages/${pageNumber}/image/?scale=${scale}`
}

export function knowledgeBaseFileUrl(knowledgeBaseId) {
  return `${API_BASE_URL}/knowledge/bases/${knowledgeBaseId}/file/`
}
