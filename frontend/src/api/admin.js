/** Organization administration: users, roles, system status, jobs. */
import { get, patch, post } from './client'

export function listRoles(config = {}) {
  return get('/admin/roles/', config)
}

export function listUsers(params = {}, config = {}) {
  return get('/admin/users/', { params, ...config })
}

export function createUser(payload) {
  return post('/admin/users/', payload)
}

export function updateUser(userId, payload) {
  return patch(`/admin/users/${userId}/`, payload)
}

export function resetUserPassword(userId, password) {
  return post(`/admin/users/${userId}/reset-password/`, { password })
}

export function fetchSystemStatus(config = {}) {
  return get('/admin/system/', config)
}

export function listJobs(config = {}) {
  return get('/admin/jobs/', config)
}

// -- AI model selection -----------------------------------------------------
//
// Installation-wide, not per user: there is one model runtime and one vector
// column behind every tenant. Requires `org.system.manage`.

/** Current selection, what the runtime has installed, and the latest detection. */
export function fetchModelAdministration(config = {}) {
  return get('/admin/ai/models/', config)
}

/**
 * Apply a selection. Roles omitted from the payload are left unchanged; an
 * empty string returns a role to the environment's value.
 *
 * @param {Object} payload { llm, drafting_llm, embedding, hardware_profile, from_run }
 */
export function applyModelSelection(payload) {
  return post('/admin/ai/models/', payload)
}

/**
 * Start a detection run. Returns immediately with a queued run; poll
 * `fetchDetection` for progress.
 *
 * @param {Object} [options]
 * @param {boolean} [options.includePulls] download recommended models that
 *   are missing. Needs a route to the model library, which an air-gapped
 *   installation does not have.
 * @param {string[]} [options.pullTargets] specific models to download.
 */
export function startModelDetection({ includePulls = false, pullTargets } = {}) {
  return post('/admin/ai/detect/', {
    include_pulls: includePulls,
    pull_targets: pullTargets || [],
  })
}

export function fetchDetection(runId, config = {}) {
  return get(`/admin/ai/detect/${runId}/`, config)
}

export function cancelDetection(runId) {
  return post(`/admin/ai/detect/${runId}/cancel/`, {})
}
