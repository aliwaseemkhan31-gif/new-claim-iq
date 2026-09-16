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
