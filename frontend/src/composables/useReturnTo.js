import { computed } from 'vue'

import { useRoute, useRouter } from 'vue-router'

/**
 * Where "back" should go.
 *
 * A detail screen is reached from several places — a project tab, a global
 * register, a search result, an AI citation — so a hardcoded parent is wrong
 * most of the time. It sends the user somewhere they have never been and
 * loses whatever they had on the screen they came from.
 *
 * The real previous entry is read from the history state vue-router keeps,
 * not from a `from` route captured on mount. That matters because these
 * screens call `router.replace` for their own page and tab state: a captured
 * `from` would be overwritten by the user's own paging, while `history.state`
 * still names the entry `router.back()` would actually return to.
 *
 * Falls back to the named list when there is no usable previous entry — a
 * hard load onto a deep link, or a reload.
 *
 * @param {Function|Object} fallback () => ({ to, label }) or ({ to, label })
 */
export function useReturnTo(fallback) {
  const route = useRoute()
  const router = useRouter()

  function fallbackValue() {
    const value = typeof fallback === 'function' ? fallback() : fallback
    return value?.to && value?.label ? value : null
  }

  const previous = computed(() => {
    const back = router.options.history.state?.back
    if (typeof back !== 'string' || !back) return null

    let resolved
    try {
      resolved = router.resolve(back)
    } catch {
      return null
    }

    // Nothing is gained by offering a return to a dead end, to the sign-in
    // page, or to the screen the user is already looking at.
    if (!resolved?.name || resolved.name === 'not-found' || resolved.name === 'login') return null
    if (resolved.name === route.name) return null

    const label = resolved.meta?.breadcrumb || resolved.meta?.title
    return label ? { label, fullPath: back } : null
  })

  const usingHistory = computed(() => Boolean(previous.value))

  /** The label and, when falling back, the link target. */
  const target = computed(() => {
    if (previous.value) return { label: previous.value.label, to: null }
    const value = fallbackValue()
    return value ? { label: value.label, to: value.to } : null
  })

  function go() {
    if (previous.value) {
      router.back()
      return
    }
    const value = fallbackValue()
    if (value) router.push(value.to)
  }

  return { target, usingHistory, go }
}
