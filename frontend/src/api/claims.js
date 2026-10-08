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

/** Reading pages and drafting runs for minutes on a CPU. */
const DRAFT_TIMEOUT_MS = 15 * 60 * 1000

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
  // OCR of several pages plus one model call, on a CPU. The client's default
  // 30 s aborts it mid-read, which shows as a dialog that closes with no
  // explanation rather than as a failure.
  return post('/claims/draft-from-document/', body, { timeout: DRAFT_TIMEOUT_MS })
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

// -- Documents filed on a claim ----------------------------------------------

/** The claim document, notices and supporting documents filed on a claim. */
export function listClaimFiles(claimId, config = {}) {
  return get(`/claims/${claimId}/files/`, config)
}

/**
 * File a document on a claim: a new upload (`file`) or one already in the
 * project (`document`). `role` is `claim_submission`, `notice` or `supporting`;
 * the rest says what makes it count — the date it was sent and the provision
 * for a notice or the claim, the element it proves for support.
 *
 * Uploading also extracts and indexes the file, which runs inline on a
 * machine with no task queue, so the request has no client timeout.
 */
export function attachClaimFile(claimId, fields, { onProgress } = {}) {
  const form = new FormData()
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null || value === '') continue
    form.append(key, typeof value === 'boolean' ? String(value) : value)
  }
  return post(`/claims/${claimId}/files/`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) return
      onProgress(Math.round((event.loaded * 100) / event.total))
    },
  })
}

export function detachClaimFile(claimId, linkId) {
  return del(`/claims/${claimId}/files/${linkId}/`)
}

// -- Checklist -----------------------------------------------------------------

/** Is each event, notice, submission and piece of evidence on record and in time? */
export function fetchClaimChecklist(claimId, config = {}) {
  return get(`/claims/${claimId}/checklist/`, config)
}

/** The checklist for every claim matching `params`, e.g. `{ project }`. */
export function fetchChecklists(params = {}, config = {}) {
  return get('/claims/checklist/', { params, ...config })
}

// -- Contract deadlines --------------------------------------------------------

/** The deadlines a project's claims face: standard, amended, or deleted. */
export function fetchEffectiveDeadlines(projectId, config = {}) {
  return get('/claims/contract-deadlines/effective/', { params: { project: projectId }, ...config })
}

export function listContractDeadlines(params = {}, config = {}) {
  return get('/claims/contract-deadlines/', { params, ...config })
}

/** Recorded by hand from the contract, so confirmed as it is saved. */
export function createContractDeadline(payload) {
  return post('/claims/contract-deadlines/', payload)
}

export function updateContractDeadline(id, payload) {
  return patch(`/claims/contract-deadlines/${id}/`, payload)
}

export function deleteContractDeadline(id) {
  return del(`/claims/contract-deadlines/${id}/`)
}

/** Read the project's contract documents for amended periods. Suggests only. */
export function scanContractDeadlines(projectId) {
  return post('/claims/contract-deadlines/scan/', { project: projectId }, { timeout: 0 })
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

// -- Claim bundles -------------------------------------------------------------

/**
 * Upload a whole claim submission and split it into its letter and annexures.
 * The split is a proposal; nothing is filed until `applyClaimBundle`.
 */
export function readClaimBundle({ projectId, file, document, allowDuplicate = false, onProgress }) {
  const form = new FormData()
  if (projectId) form.append('project', projectId)
  if (file) form.append('file', file)
  if (document) form.append('document', document)
  if (allowDuplicate) form.append('allow_duplicate', 'true')
  return post('/claims/bundle/read/', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,
    onUploadProgress: (event) => {
      if (!onProgress || !event.total) return
      onProgress(Math.round((event.loaded * 100) / event.total))
    },
  })
}

/** File a split bundle on a claim, creating the claim when `new_claim` is given. */
export function applyClaimBundle(payload) {
  return post('/claims/bundle/apply/', payload)
}
