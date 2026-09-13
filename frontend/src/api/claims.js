/** Claims, their evidence links, and human assessments. */
import { del, get, patch, post } from './client'

export function listClaims(params = {}) {
  return get('/claims/', { params })
}

export function fetchClaim(claimId) {
  return get(`/claims/${claimId}/`)
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
export function assessClaim(claimId, payload) {
  return post(`/claims/${claimId}/assess/`, payload)
}

export function listClaimEvidence(claimId, params = {}) {
  return get(`/claims/${claimId}/evidence/`, { params })
}

export function linkEvidence(claimId, payload) {
  return post(`/claims/${claimId}/evidence/`, payload)
}

export function unlinkEvidence(claimId, evidenceId) {
  return del(`/claims/${claimId}/evidence/${evidenceId}/`)
}

export function listEvidence(params = {}) {
  return get('/evidence/', { params })
}

export function listCorrespondence(params = {}) {
  return get('/correspondence/', { params })
}

export function fetchCorrespondence(correspondenceId) {
  return get(`/correspondence/${correspondenceId}/`)
}
