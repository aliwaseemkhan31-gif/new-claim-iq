/** Documents, their pages and structure, and the ingestion pipeline. */
import { API_BASE_URL, del, get, patch, post } from './client'

export function listDocuments(params = {}, config = {}) {
  return get('/documents/', { params, ...config })
}

export function fetchDocument(documentId, config = {}) {
  return get(`/documents/${documentId}/`, config)
}

export function updateDocument(documentId, payload) {
  return patch(`/documents/${documentId}/`, payload)
}

export function deleteDocument(documentId) {
  return del(`/documents/${documentId}/`)
}

/**
 * Upload one file. `onProgress` receives 0-100. Processing continues in the
 * background after the upload returns; poll `fetchDocumentProcessing`.
 */
export function uploadDocument({
  projectId,
  file,
  documentType,
  title,
  reference,
  documentDate,
  replaces,
  onProgress,
}) {
  const form = new FormData()
  form.append('file', file)
  form.append('project', projectId)
  form.append('document_type', documentType)
  if (title) form.append('title', title)
  if (reference) form.append('reference', reference)
  if (documentDate) form.append('document_date', documentDate)
  if (replaces) form.append('replaces', replaces)

  return post('/documents/', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) return
      onProgress(Math.round((event.loaded * 100) / event.total))
    },
  })
}

export function reprocessDocument(documentId, fromStage) {
  return post(`/documents/${documentId}/reprocess/`, fromStage ? { from_stage: fromStage } : {})
}

export function fetchDocumentPages(documentId, params = {}, config = {}) {
  return get(`/documents/${documentId}/pages/`, { params, ...config })
}

export function fetchDocumentPage(documentId, pageNumber, config = {}) {
  return get(`/documents/${documentId}/pages/${pageNumber}/`, config)
}

/** Same-origin URL for an <img>; the session cookie authenticates it. */
export function documentPageImageUrl(documentId, pageNumber, scale = 1.5) {
  return `${API_BASE_URL}/documents/${documentId}/pages/${pageNumber}/image/?scale=${scale}`
}

export function documentFileUrl(documentId) {
  return `${API_BASE_URL}/documents/${documentId}/file/`
}

export function fetchDocumentChunks(documentId, params = {}, config = {}) {
  return get(`/documents/${documentId}/chunks/`, { params, ...config })
}

export function fetchDocumentSections(documentId, config = {}) {
  return get(`/documents/${documentId}/sections/`, config)
}

/** The latest ingestion job, stage by stage. */
export function fetchDocumentProcessing(documentId, config = {}) {
  return get(`/documents/${documentId}/processing/`, config)
}

export function listIngestionJobs(params = {}, config = {}) {
  return get('/documents/jobs/', { params, ...config })
}

export function fetchDocumentTaxonomy(config = {}) {
  return get('/documents/taxonomy/', config)
}
