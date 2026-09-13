/**
 * System health. In an air-gapped deployment the operator has no cloud status
 * page, so the product itself has to say which subsystem is down.
 */
import { get } from './client'

/** Liveness: is the API process answering at all? */
export function fetchLiveness(config = {}) {
  return get('/health/live/', config)
}

/** Readiness: database, cache, broker, object storage, model runtime. */
export function fetchReadiness(config = {}) {
  return get('/health/ready/', config)
}

/** Build/version metadata for the about dialog and bug reports. */
export function fetchVersion(config = {}) {
  return get('/health/version/', config)
}

/** Background worker queue depth and failure counts. */
export function fetchWorkerStatus() {
  return get('/health/workers/')
}
