/**
 * Authentication and identity.
 *
 * Session-cookie auth (Django `SessionAuthentication`), so login sets a cookie
 * rather than returning a token. `fetchCurrentUser` is the source of truth for
 * "am I signed in?" — never localStorage.
 */
import { get, post } from './client'

/** Prime the CSRF cookie before the first unsafe request (i.e. before login). */
export function fetchCsrfToken() {
  return get('/auth/csrf/')
}

/**
 * The backend authenticates by email — `User.USERNAME_FIELD = "email"`, and
 * there is no username field on the model. The payload key must match.
 */
export function login({ email, password }) {
  return post('/auth/login/', { email, password })
}

export function logout() {
  return post('/auth/logout/', {})
}

/** Current user plus the flattened permission codes for the active scope. */
export function fetchCurrentUser() {
  return get('/auth/me/')
}

export function changePassword({ currentPassword, newPassword }) {
  return post('/auth/password/change/', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}

/** Permissions for one project, unioned with the organization role. */
export function fetchProjectPermissions(projectId) {
  return get(`/auth/me/permissions/`, { params: { project: projectId } })
}
