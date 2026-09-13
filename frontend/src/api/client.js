import axios from 'axios'

export const API_BASE_URL = '/api/v1'

const CSRF_COOKIE_NAME = 'csrftoken'
const CSRF_HEADER_NAME = 'X-CSRFToken'
const UNSAFE_METHODS = new Set(['post', 'put', 'patch', 'delete'])

/**
 * A normalised API failure.
 *
 * The backend returns every error in one envelope:
 *
 *   {"error": {"code", "message", "details", "request_id"}}
 *
 * `code` is stable and machine-readable — branch on it, never on `message`.
 * `requestId` ties the failure to the server log line and is what a user
 * should quote when reporting a problem.
 */
export class ApiError extends Error {
  constructor({ code, message, details, requestId, status, cause }) {
    super(message || 'The request failed.')
    this.name = 'ApiError'
    this.code = code || 'unknown_error'
    this.details = details || {}
    this.requestId = requestId || null
    this.status = status ?? null
    if (cause) this.cause = cause
  }

  /** True when re-authenticating could plausibly fix this. */
  get isAuthError() {
    return this.code === 'not_authenticated' || this.code === 'authentication_failed'
  }

  get isPermissionError() {
    return this.code === 'permission_denied'
  }

  get isNotFound() {
    return this.code === 'not_found'
  }

  get isValidationError() {
    return this.code === 'validation_error'
  }

  /** Field-level messages when the backend supplied them, else an empty object. */
  get fieldErrors() {
    const errors = this.details?.errors
    return errors && typeof errors === 'object' && !Array.isArray(errors) ? errors : {}
  }
}

/** Read a cookie value by name. Returns null when absent. */
export function readCookie(name, cookieString) {
  const source =
    cookieString ?? (typeof document !== 'undefined' ? document.cookie : '')
  if (!source) return null
  const prefix = `${name}=`
  for (const part of source.split(';')) {
    const entry = part.trim()
    if (entry.startsWith(prefix)) {
      return decodeURIComponent(entry.slice(prefix.length))
    }
  }
  return null
}

/**
 * Turn any axios failure into an ApiError.
 *
 * Three cases must all produce the same shape, or every caller ends up
 * writing its own defensive branching:
 *   - the standard envelope,
 *   - a non-envelope response (proxy error page, gateway HTML, bare string),
 *   - no response at all (offline, DNS, connection refused, timeout).
 */
export function toApiError(error) {
  if (error instanceof ApiError) return error

  if (axios.isCancel?.(error) || error?.code === 'ERR_CANCELED') {
    return new ApiError({
      code: 'request_cancelled',
      message: 'The request was cancelled.',
      status: null,
      cause: error,
    })
  }

  const response = error?.response

  if (!response) {
    const timedOut = error?.code === 'ECONNABORTED'
    return new ApiError({
      code: timedOut ? 'request_timeout' : 'network_error',
      message: timedOut
        ? 'The server did not respond in time.'
        : 'Could not reach the ClaimIQ server. Check that the backend is running.',
      status: null,
      cause: error,
    })
  }

  const envelope = response.data?.error

  if (envelope && typeof envelope === 'object') {
    return new ApiError({
      code: envelope.code,
      message: envelope.message,
      details: envelope.details,
      requestId: envelope.request_id,
      status: response.status,
      cause: error,
    })
  }

  // A response that is not the envelope means something upstream of the
  // application answered (reverse proxy, dev-server proxy, WSGI failure).
  return new ApiError({
    code: `http_${response.status}`,
    message:
      typeof response.data === 'string' && response.data.length < 200 && response.data.trim()
        ? response.data.trim()
        : `The server returned an unexpected ${response.status} response.`,
    status: response.status,
    cause: error,
  })
}

export const http = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  timeout: 30000,
  xsrfCookieName: CSRF_COOKIE_NAME,
  xsrfHeaderName: CSRF_HEADER_NAME,
  headers: {
    Accept: 'application/json',
    'X-Requested-With': 'XMLHttpRequest',
  },
})

// Django rejects an unsafe request without the echoed CSRF token. axios' own
// xsrf handling only fires for same-origin absolute URLs, so set it explicitly.
http.interceptors.request.use((config) => {
  const method = (config.method || 'get').toLowerCase()
  if (UNSAFE_METHODS.has(method)) {
    const token = readCookie(CSRF_COOKIE_NAME)
    if (token) {
      config.headers = config.headers || {}
      config.headers[CSRF_HEADER_NAME] = token
    }
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(toApiError(error))
)

/** Unwrap to the response body. Every domain module goes through these. */
export async function get(url, config) {
  const { data } = await http.get(url, config)
  return data
}

export async function post(url, body, config) {
  const { data } = await http.post(url, body, config)
  return data
}

export async function put(url, body, config) {
  const { data } = await http.put(url, body, config)
  return data
}

export async function patch(url, body, config) {
  const { data } = await http.patch(url, body, config)
  return data
}

export async function del(url, config) {
  const { data } = await http.delete(url, config)
  return data
}

export default http
