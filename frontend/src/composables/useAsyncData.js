import { computed, onScopeDispose, ref, shallowRef } from 'vue'

/**
 * The one loading/empty/error state machine.
 *
 * Every view that reads from the API uses this, so the four states are
 * distinguished consistently:
 *
 *   idle    — nothing requested yet
 *   loading — in flight
 *   error   — the request failed; `error` holds an ApiError
 *   success — resolved; `isEmpty` says whether the payload had rows
 *
 * "Loaded but empty" and "failed to load" are different facts and the UI must
 * never conflate them: an empty state where an error belongs tells the user
 * their data does not exist.
 *
 * @param {Function} fetcher async ({ signal }) => data
 * @param {Object} [options]
 * @param {boolean} [options.immediate=false] run on creation
 * @param {Function} [options.isEmpty] predicate deciding emptiness
 */
export function useAsyncData(fetcher, options = {}) {
  const { immediate = false, isEmpty: emptyCheck } = options

  const data = shallowRef(null)
  const error = ref(null)
  const loading = ref(false)
  const finished = ref(false)

  let controller = null
  let runToken = 0

  const isEmpty = computed(() => {
    if (!finished.value || loading.value || error.value || data.value === null) return false
    if (emptyCheck) return Boolean(emptyCheck(data.value))
    if (Array.isArray(data.value)) return data.value.length === 0
    if (Array.isArray(data.value?.results)) return data.value.results.length === 0
    return false
  })

  const state = computed(() => {
    if (loading.value) return 'loading'
    if (error.value) return 'error'
    if (!finished.value) return 'idle'
    return isEmpty.value ? 'empty' : 'success'
  })

  async function execute(...args) {
    controller?.abort()
    controller = typeof AbortController !== 'undefined' ? new AbortController() : null
    const token = ++runToken

    loading.value = true
    error.value = null

    try {
      const result = await fetcher({ signal: controller?.signal }, ...args)
      if (token !== runToken) return null // a newer call superseded this one
      data.value = result
      finished.value = true
      return result
    } catch (err) {
      if (token !== runToken || err?.code === 'request_cancelled') return null
      error.value = err
      data.value = null
      finished.value = true
      return null
    } finally {
      if (token === runToken) loading.value = false
    }
  }

  function reset() {
    controller?.abort()
    runToken += 1
    data.value = null
    error.value = null
    loading.value = false
    finished.value = false
  }

  onScopeDispose(() => controller?.abort())

  if (immediate) execute()

  return { data, error, loading, finished, isEmpty, state, execute, reset }
}
