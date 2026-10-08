/** Correspondence and the Notices asserted over it. */
import { del, get, patch, post } from './client'

export function listCorrespondence(params = {}, config = {}) {
  return get('/correspondence/', { params, ...config })
}

export function fetchCorrespondence(correspondenceId, config = {}) {
  return get(`/correspondence/${correspondenceId}/`, config)
}

export function createCorrespondence(payload) {
  return post('/correspondence/', payload)
}

export function updateCorrespondence(correspondenceId, payload) {
  return patch(`/correspondence/${correspondenceId}/`, payload)
}

export function deleteCorrespondence(correspondenceId) {
  return del(`/correspondence/${correspondenceId}/`)
}

/** Assert that an item is a Notice under a provision, optionally for a claim. */
export function createNotice(correspondenceId, { claim, clauseNumber, isConfirmedNotice, notes }) {
  return post(`/correspondence/${correspondenceId}/notices/`, {
    claim: claim || null,
    clause_number: clauseNumber,
    is_confirmed_notice: Boolean(isConfirmedNotice),
    notes: notes || '',
  })
}

export function listNotices(params = {}, config = {}) {
  return get('/correspondence/notices/', { params, ...config })
}

export function updateNotice(noticeId, payload) {
  return patch(`/correspondence/notices/${noticeId}/`, payload)
}

export function deleteNotice(noticeId) {
  return del(`/correspondence/notices/${noticeId}/`)
}

// -- Reading notices on arrival ------------------------------------------------

/** Reading a letter runs OCR and a model call on this machine: no client timeout. */
function multipart(fields, onProgress) {
  const form = new FormData()
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null || value === '') continue
    form.append(key, typeof value === 'boolean' ? String(value) : value)
  }
  return {
    form,
    config: {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0,
      onUploadProgress: (event) => {
        if (!onProgress || !event.total) return
        onProgress(Math.round((event.loaded * 100) / event.total))
      },
    },
  }
}

/**
 * Upload a notice letter (or name one already in the project) and read it.
 * Returns the stored document and a proposal; nothing is filed until saved.
 */
export function readNotice({ projectId, file, document, allowDuplicate = false, onProgress }) {
  const { form, config } = multipart(
    { project: projectId, file, document, allow_duplicate: allowDuplicate || undefined },
    onProgress,
  )
  return post('/correspondence/notices/intake/read/', form, config)
}

/** File a read notice: on a claim, a new claim, or the register alone. */
export function saveNotice(payload) {
  return post('/correspondence/notices/intake/save/', payload)
}

/** Every notice in a project, with its dates and deadline status. */
export function fetchNoticeRegister(projectId, config = {}) {
  return get('/correspondence/notices/register/', { params: { project: projectId }, ...config })
}

/** Put a notice that is on the register alone onto a claim. */
export function linkNotice(noticeId, claimId) {
  return post(`/correspondence/notices/${noticeId}/link/`, { claim: claimId })
}
