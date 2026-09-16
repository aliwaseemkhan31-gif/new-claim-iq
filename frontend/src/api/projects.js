/** Projects, their parties and members, and the project chronology. */
import { del, get, patch, post } from './client'

export function listProjects(params = {}, config = {}) {
  return get('/projects/', { params, ...config })
}

export function fetchProject(projectId, config = {}) {
  return get(`/projects/${projectId}/`, config)
}

export function createProject(payload) {
  return post('/projects/', payload)
}

export function updateProject(projectId, payload) {
  return patch(`/projects/${projectId}/`, payload)
}

export function deleteProject(projectId) {
  return del(`/projects/${projectId}/`)
}

/** Counts for the workspace overview, including knowledge-base availability. */
export function fetchProjectSummary(projectId, config = {}) {
  return get(`/projects/${projectId}/summary/`, config)
}

export function listProjectMembers(projectId, config = {}) {
  return get(`/projects/${projectId}/members/`, config)
}

export function addProjectMember(projectId, { user, role }) {
  return post(`/projects/${projectId}/members/`, { user, role })
}

export function updateProjectMember(projectId, memberId, payload) {
  return patch(`/projects/${projectId}/members/${memberId}/`, payload)
}

export function removeProjectMember(projectId, memberId) {
  return del(`/projects/${projectId}/members/${memberId}/`)
}

export function listParties(projectId, config = {}) {
  return get(`/projects/${projectId}/parties/`, config)
}

export function createParty(projectId, payload) {
  return post(`/projects/${projectId}/parties/`, payload)
}

export function updateParty(projectId, partyId, payload) {
  return patch(`/projects/${projectId}/parties/${partyId}/`, payload)
}

export function deleteParty(projectId, partyId) {
  return del(`/projects/${projectId}/parties/${partyId}/`)
}

/** The project chronology across every claim, with conflicts surfaced. */
export function fetchProjectTimeline(projectId, params = {}, config = {}) {
  return get(`/projects/${projectId}/timeline/`, { params, ...config })
}
