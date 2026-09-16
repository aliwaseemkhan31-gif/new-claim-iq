/** The signed-in user's notifications. */
import { get, post } from './client'

export function listNotifications(params = {}, config = {}) {
  return get('/notifications/', { params, ...config })
}

export function fetchUnreadCount(config = {}) {
  return get('/notifications/unread-count/', config)
}

export function markNotificationRead(notificationId) {
  return post(`/notifications/${notificationId}/read/`, {})
}

export function markAllNotificationsRead() {
  return post('/notifications/read-all/', {})
}
