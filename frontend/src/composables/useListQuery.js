import { computed } from 'vue'

import { useRoute, useRouter } from 'vue-router'

/**
 * A register's filters and page, held in the URL.
 *
 * Kept in local refs these are lost at the worst moment: the user narrows a
 * list of four hundred documents to the three they care about, opens one, and
 * comes back to the unfiltered list at the top. The filter was the work, and
 * it is the thing that gets thrown away.
 *
 * In the URL the same state is shareable, survives a reload, and — because
 * `router.back()` restores the whole query — comes back intact on the way
 * back from a detail screen.
 *
 * Written with `replace`: narrowing a filter is not a step in the journey, and
 * Back should leave the register rather than retrace every keystroke.
 *
 * Values equal to their default are left out of the URL, so an untouched
 * register has a clean address.
 *
 * @param {Object} defaults key → default value; a number default is parsed as
 *   a number. A `page` key is reset whenever any other key changes.
 */
export function useListQuery(defaults) {
  const route = useRoute()
  const router = useRouter()
  const keys = Object.keys(defaults)
  const paged = Object.prototype.hasOwnProperty.call(defaults, 'page')

  function read(key) {
    const fallback = defaults[key]
    const raw = route.query[key]
    if (raw == null || raw === '') return fallback
    if (typeof fallback === 'number') {
      const parsed = Number.parseInt(Array.isArray(raw) ? raw[0] : raw, 10)
      return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
    }
    return String(Array.isArray(raw) ? raw[0] : raw)
  }

  function write(patch) {
    const query = { ...route.query }
    for (const [key, value] of Object.entries(patch)) {
      if (value == null || value === '' || value === defaults[key]) delete query[key]
      else query[key] = String(value)
    }
    router.replace({ query })
  }

  const state = {}
  for (const key of keys) {
    state[key] = computed({
      get: () => read(key),
      set: (value) => {
        const patch = { [key]: value }
        // A page number only means something against the filter that produced
        // it; page 4 of an unfiltered list is not page 4 of a filtered one.
        if (paged && key !== 'page') patch.page = defaults.page
        write(patch)
      },
    })
  }

  /** The whole state as a plain object — watch this to decide when to reload. */
  const snapshot = computed(() => Object.fromEntries(keys.map((key) => [key, read(key)])))

  const isFiltered = computed(() =>
    keys.some((key) => key !== 'page' && read(key) !== defaults[key])
  )

  function clear() {
    write({ ...defaults })
  }

  return { ...state, snapshot, isFiltered, clear }
}
