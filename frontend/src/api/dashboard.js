/** Aggregates across the caller's accessible projects. */
import { get } from './client'

export function fetchDashboard(config = {}) {
  return get('/dashboard/', config)
}
