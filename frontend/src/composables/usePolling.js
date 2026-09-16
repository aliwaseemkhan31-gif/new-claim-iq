import { onScopeDispose, ref } from 'vue'

/**
 * Poll an async function until a condition says the work is finished.
 *
 * Used for server-side work that outlives a request — ingestion, knowledge-base
 * builds, analysis runs. Polling stops on its own when `isDone` returns true,
 * when the owning component unmounts, or on `stop()`. Errors stop polling and
 * are exposed; a failing endpoint is not hammered.
 *
 * @param {Function} fetcher async () => data
 * @param {Object} [options]
 * @param {number} [options.interval=3000]
 * @param {Function} [options.isDone] (data) => boolean
 * @param {Function} [options.onData] (data) => void
 */
export function usePolling(fetcher, options = {}) {
  const { interval = 3000, isDone = () => false, onData } = options
  const active = ref(false)
  const error = ref(null)
  let timer = null
  let stopped = true

  async function tick() {
    if (stopped) return
    try {
      const data = await fetcher()
      if (stopped) return
      error.value = null
      onData?.(data)
      if (isDone(data)) {
        stop()
        return
      }
    } catch (err) {
      error.value = err
      stop()
      return
    }
    timer = setTimeout(tick, interval)
  }

  function start({ immediate = true } = {}) {
    if (!stopped) return
    stopped = false
    active.value = true
    if (immediate) tick()
    else timer = setTimeout(tick, interval)
  }

  function stop() {
    stopped = true
    active.value = false
    if (timer) clearTimeout(timer)
    timer = null
  }

  onScopeDispose(stop)

  return { active, error, start, stop }
}
