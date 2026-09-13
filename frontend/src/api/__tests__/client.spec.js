import { describe, expect, it } from 'vitest'

import { ApiError, readCookie, toApiError } from '../client'

/** Build an axios-shaped rejection. */
function axiosError({ status, data }) {
  const error = new Error('Request failed')
  error.isAxiosError = true
  error.response = { status, data, headers: {}, config: {} }
  return error
}

describe('ApiError', () => {
  it('is an Error with the fields callers branch on', () => {
    const error = new ApiError({
      code: 'validation_error',
      message: 'The submitted data is invalid.',
      details: { errors: { name: ['This field is required.'] } },
      requestId: 'req-1',
      status: 400,
    })

    expect(error).toBeInstanceOf(Error)
    expect(error.name).toBe('ApiError')
    expect(error.code).toBe('validation_error')
    expect(error.message).toBe('The submitted data is invalid.')
    expect(error.requestId).toBe('req-1')
    expect(error.status).toBe(400)
    expect(error.isValidationError).toBe(true)
    expect(error.fieldErrors).toEqual({ name: ['This field is required.'] })
  })

  it('defaults code and details rather than leaving them undefined', () => {
    const error = new ApiError({})
    expect(error.code).toBe('unknown_error')
    expect(error.details).toEqual({})
    expect(error.requestId).toBeNull()
    expect(error.fieldErrors).toEqual({})
  })

  it('classifies auth, permission and not-found codes', () => {
    expect(new ApiError({ code: 'not_authenticated' }).isAuthError).toBe(true)
    expect(new ApiError({ code: 'authentication_failed' }).isAuthError).toBe(true)
    expect(new ApiError({ code: 'permission_denied' }).isPermissionError).toBe(true)
    expect(new ApiError({ code: 'not_found' }).isNotFound).toBe(true)
    expect(new ApiError({ code: 'internal_error' }).isAuthError).toBe(false)
  })

  it('ignores a non-object errors payload when reading fieldErrors', () => {
    const error = new ApiError({ code: 'validation_error', details: { errors: ['bad'] } })
    expect(error.fieldErrors).toEqual({})
  })
})

describe('toApiError — the standard envelope', () => {
  it('unwraps {"error": {...}} into an ApiError', () => {
    const error = toApiError(
      axiosError({
        status: 400,
        data: {
          error: {
            code: 'retrieval_scope_invalid',
            message: 'Knowledge-base retrieval requires an explicit edition.',
            details: { remedy: "Set knowledge_base_edition, e.g. 'red-book-2017'." },
            request_id: '3f2c9a',
          },
        },
      })
    )

    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('retrieval_scope_invalid')
    expect(error.message).toBe('Knowledge-base retrieval requires an explicit edition.')
    expect(error.details.remedy).toContain('red-book-2017')
    expect(error.requestId).toBe('3f2c9a')
    expect(error.status).toBe(400)
  })

  it('maps request_id to requestId and never leaves it under the snake_case key', () => {
    const error = toApiError(
      axiosError({ status: 500, data: { error: { code: 'internal_error', request_id: 'abc' } } })
    )
    expect(error.requestId).toBe('abc')
    expect(error.request_id).toBeUndefined()
  })

  it('supplies a message when the envelope omits one', () => {
    const error = toApiError(axiosError({ status: 500, data: { error: { code: 'internal_error' } } }))
    expect(error.message).toBeTruthy()
    expect(error.code).toBe('internal_error')
  })
})

describe('toApiError — responses that are not the envelope', () => {
  it('synthesises a code from the status for a non-envelope body', () => {
    const error = toApiError(axiosError({ status: 502, data: '<html>Bad Gateway</html>' }))
    expect(error.code).toBe('http_502')
    expect(error.status).toBe(502)
    expect(error.requestId).toBeNull()
  })

  it('does not mistake an unrelated JSON body for the envelope', () => {
    const error = toApiError(axiosError({ status: 400, data: { detail: 'Invalid page.' } }))
    expect(error.code).toBe('http_400')
  })

  it('uses a short string body as the message', () => {
    const error = toApiError(axiosError({ status: 503, data: 'Service Unavailable' }))
    expect(error.message).toBe('Service Unavailable')
  })
})

describe('toApiError — transport failures', () => {
  it('reports a network error when there is no response at all', () => {
    const error = toApiError(Object.assign(new Error('Network Error'), { isAxiosError: true }))
    expect(error.code).toBe('network_error')
    expect(error.status).toBeNull()
    expect(error.message).toMatch(/could not reach/i)
  })

  it('distinguishes a timeout from a connection failure', () => {
    const error = toApiError(Object.assign(new Error('timeout'), { code: 'ECONNABORTED' }))
    expect(error.code).toBe('request_timeout')
  })

  it('marks cancellations so callers can ignore them', () => {
    const error = toApiError(Object.assign(new Error('canceled'), { code: 'ERR_CANCELED' }))
    expect(error.code).toBe('request_cancelled')
  })

  it('passes an existing ApiError through untouched', () => {
    const original = new ApiError({ code: 'already_wrapped' })
    expect(toApiError(original)).toBe(original)
  })
})

describe('readCookie', () => {
  it('reads the CSRF token out of a cookie string', () => {
    expect(readCookie('csrftoken', 'sessionid=abc; csrftoken=XYZ123; other=1')).toBe('XYZ123')
  })

  it('returns null when the cookie is absent', () => {
    expect(readCookie('csrftoken', 'sessionid=abc')).toBeNull()
  })

  it('does not match a cookie whose name merely ends with the target', () => {
    expect(readCookie('csrftoken', 'xcsrftoken=nope')).toBeNull()
  })

  it('decodes percent-encoded values', () => {
    expect(readCookie('csrftoken', 'csrftoken=a%20b')).toBe('a b')
  })

  it('handles an empty cookie jar', () => {
    expect(readCookie('csrftoken', '')).toBeNull()
  })
})
