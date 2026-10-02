import { nextTick, onScopeDispose, watch } from 'vue'

/**
 * The behaviour every modal owes its user: focus goes in, stays in, and comes
 * back out where it started.
 *
 * Without a trap, Tab walks out of an open dialog into the page behind it —
 * the user is then typing into a form they cannot see, and Escape no longer
 * reaches the dialog because focus has left it. That is why the key handler
 * belongs on the overlay rather than on the panel: it covers everything the
 * dialog renders, whatever inside it holds focus.
 *
 * Scroll is locked while a dialog is open, counted rather than set, so a
 * confirmation raised from inside a dialog does not unlock the page when it
 * closes.
 *
 * @param {import('vue').Ref<boolean>} isOpen
 * @param {import('vue').Ref<HTMLElement|null>} panelRef the dialog panel
 * @param {Object} [options]
 * @param {Function} [options.onRequestClose] called on Escape
 * @param {Function} [options.initialFocus] () => HTMLElement to focus first
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

let lockCount = 0
let overflowBefore = ''

function lockScroll() {
  if (typeof document === 'undefined') return
  if (lockCount === 0) {
    overflowBefore = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  lockCount += 1
}

function unlockScroll() {
  if (typeof document === 'undefined') return
  lockCount = Math.max(0, lockCount - 1)
  if (lockCount === 0) document.body.style.overflow = overflowBefore
}

export function useModal(isOpen, panelRef, options = {}) {
  const { onRequestClose, initialFocus } = options

  let restoreTo = null
  let holdsLock = false

  function focusable() {
    const root = panelRef.value
    if (!root) return []
    // Deliberately not filtered by visibility: `offsetParent` and
    // `getClientRects` both report everything as hidden under jsdom, which
    // would silently disable the trap in tests.
    return Array.from(root.querySelectorAll(FOCUSABLE)).filter(
      (el) => el.getAttribute('aria-hidden') !== 'true'
    )
  }

  /** Bind to the overlay, so it sees keys from anything the dialog renders. */
  function onKeydown(event) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onRequestClose?.()
      return
    }
    if (event.key !== 'Tab') return

    const panel = panelRef.value
    if (!panel) return

    const items = focusable()
    if (!items.length) {
      event.preventDefault()
      panel.focus?.()
      return
    }

    const first = items[0]
    const last = items[items.length - 1]
    const active = typeof document !== 'undefined' ? document.activeElement : null
    const inside = active && panel.contains(active) && active !== panel

    if (event.shiftKey) {
      if (!inside || active === first) {
        event.preventDefault()
        last.focus()
      }
    } else if (!inside || active === last) {
      event.preventDefault()
      first.focus()
    }
  }

  async function activate() {
    restoreTo = typeof document !== 'undefined' ? document.activeElement : null
    lockScroll()
    holdsLock = true
    await nextTick()
    const wanted = initialFocus?.() || focusable()[0] || panelRef.value
    wanted?.focus?.()
  }

  function deactivate() {
    if (holdsLock) {
      unlockScroll()
      holdsLock = false
    }
    const target = restoreTo
    restoreTo = null
    // The trigger can be gone by now — a row that re-rendered, a button the
    // action removed. Restoring focus to a detached node blanks it instead.
    if (target?.focus && typeof document !== 'undefined' && document.contains(target)) {
      target.focus()
    }
  }

  // `immediate`, so a dialog that is mounted already open is trapped too and
  // not only one that is toggled after mount. Closed, this runs `deactivate`
  // on a modal that was never active, which both guards below make a no-op.
  watch(isOpen, (open) => (open ? activate() : deactivate()), { immediate: true })

  onScopeDispose(() => {
    if (holdsLock) {
      unlockScroll()
      holdsLock = false
    }
  })

  return { onKeydown }
}
