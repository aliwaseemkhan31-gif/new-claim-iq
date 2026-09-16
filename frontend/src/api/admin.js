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
