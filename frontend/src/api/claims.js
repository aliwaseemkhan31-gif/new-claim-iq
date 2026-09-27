/** Claims, their issues, events and evidence, and the computed checks on them. */
import { del, get, patch, post } from './client'

export function listClaims(params = {}, config = {}) {
  return get('/claims/', { params, ...config })
}

export function fetchClaim(claimId, config = {}) {
  return get(`/claims/${claimId}/`, config)
}

export function createClaim(payload) {
  return post('/claims/', payload)
}

export function updateClaim(claimId, payload) {
  return patch(`/claims/${claimId}/`, payload)
}

export function deleteClaim(claimId) {
  return del(`/claims/${claimId}/`)
}

/** Record a human determination. Distinct from an AI finding, always. */
export function assessClaim(claimId, { outcome, assessment }) {
  return post(`/claims/${claimId}/assess/`, { outcome, assessment })
}

export function listClaimIssues(claimId, config = {}) {
  return get(`/claims/${claimId}/issues/`, config)
}

export function createClaimIssue(claimId, payload) {
  return post(`/claims/${claimId}/issues/`, payload)
}

export function updateClaimIssue(claimId, issueId, payload) {
  return patch(`/claims/${claimId}/issues/${issueId}/`, payload)
}

export function deleteClaimIssue(claimId, issueId) {
  return del(`/claims/${claimId}/issues/${issueId}/`)
}

export function fetchClaimTimeline(claimId, params = {}, config = {}) {
  return get(`/claims/${claimId}/timeline/`, { params, ...config })
}

/** Deterministic: what the claim must establish against the evidence on record. */
/** Preliminary screening: is the claim in a fit state to work on? */
export function fetchScreening(claimId, config = {}) {
  return get(`/claims/${claimId}/screening/`, config)
}

/**
 * Read a photographed or scanned claim and propose a claim from it.
 *
 * Writes nothing: the response is a draft for a person to correct and accept.
 * Slow by nature — OCR plus a model call on a CPU — so callers should show
 * that it is working rather than appearing to hang.
 */
export function draftClaimFromDocument(projectId, files) {
  const body = new FormData()
  body.append('project', projectId)
  for (const file of files) body.append('files', file)
  return post('/claims/draft-from-document/', body)
}

/**
 * Screening outcomes for a page of claims, keyed by claim id.
 *
 * A separate call rather than a field on the list: screening one claim costs
 * about seven queries, so a register that screened each row inline would spend
 * seconds rendering. The server does a page in a handful.
 */
export function fetchScreeningSummary(params = {}, config = {}) {
  return get('/claims/screening/', { params, ...config })
}

export function fetchEvidenceGaps(claimId, config = {}) {
  return get(`/claims/${claimId}/evidence-gaps/`, config)
}

/** Deterministic notice timing, computed from recorded dates. */
export function fetchNoticeCompliance(claimId, config = {}) {
  return get(`/claims/${claimId}/notice-compliance/`, config)
}

export function listEvidence(params = {}, config = {}) {
  return get('/evidence/', { params, ...config })
}

export function createEvidence(payload) {
  return post('/evidence/', payload)
}

/** Editing relevance is attributed as a review by the server. */
export function updateEvidence(evidenceId, payload) {
  return patch(`/evidence/${evidenceId}/`, payload)
}

export function deleteEvidence(evidenceId) {
  return del(`/evidence/${evidenceId}/`)
}

export function listClaimEvents(params = {}, config = {}) {
  return get('/claims/events/', { params, ...config })
}

export function createClaimEvent(payload) {
  return post('/claims/events/', payload)
}

export function updateClaimEvent(eventId, payload) {
  return patch(`/claims/events/${eventId}/`, payload)
}

export function deleteClaimEvent(eventId) {
  return del(`/claims/events/${eventId}/`)
}
