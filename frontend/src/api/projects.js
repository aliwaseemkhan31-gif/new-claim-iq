/** Projects — the top-level container all other domains hang off. */
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

export function archiveProject(projectId) {
  return post(`/projects/${projectId}/archive/`, {})
}

export function deleteProject(projectId) {
  return del(`/projects/${projectId}/`)
}

/** Aggregate counts for the project workspace overview. */
export function fetchProjectSummary(projectId) {
  return get(`/projects/${projectId}/summary/`)
}

export function listProjectMembers(projectId, params = {}) {
  return get(`/projects/${projectId}/members/`, { params })
}

export function addProjectMember(projectId, payload) {
  return post(`/projects/${projectId}/members/`, payload)
}

export function removeProjectMember(projectId, memberId) {
  return del(`/projects/${projectId}/members/${memberId}/`)
}

export function listProjectTimeline(projectId, params = {}) {
  return get(`/projects/${projectId}/timeline/`, { params })
}
