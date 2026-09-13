/** Documents, their extracted pages/chunks, and the ingestion pipeline. */
import { del, get, patch, post } from './client'

export function listDocuments(params = {}) {
  return get('/documents/', { params })
}

export function fetchDocument(documentId) {
  return get(`/documents/${documentId}/`)
}

export function updateDocument(documentId, payload) {
  return patch(`/documents/${documentId}/`, payload)
}

export function deleteDocument(documentId) {
  return del(`/documents/${documentId}/`)
}

/**
 * Upload one file. `onProgress` receives 0-100; large scanned PDFs make this
 * worth wiring up rather than showing an indeterminate spinner.
 */
export function uploadDocument({ projectId, file, documentType, onProgress }) {
  const form = new FormData()
  form.append('file', file)
  form.append('project', projectId)
  if (documentType) form.append('document_type', documentType)

  return post('/documents/', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0, // uploads set their own pace
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) return
      onProgress(Math.round((event.loaded * 100) / event.total))
    },
  })
}

export function reprocessDocument(documentId) {
  return post(`/documents/${documentId}/reprocess/`, {})
}

export function fetchDocumentPages(documentId, params = {}) {
  return get(`/documents/${documentId}/pages/`, { params })
}

export function fetchDocumentChunks(documentId, params = {}) {
  return get(`/documents/${documentId}/chunks/`, { params })
}

/** Ingestion job status for the processing queue view. */
export function listIngestionJobs(params = {}) {
  return get('/documents/jobs/', { params })
}

export function fetchDocumentTaxonomy() {
  return get('/documents/taxonomy/')
}
