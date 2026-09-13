/**
 * Shared status vocabulary.
 *
 * One map across every domain so "approved" reads the same whether it belongs
 * to a claim, a report or a document. Anything not listed resolves to
 * `neutral`: an unrecognised status must never be coloured as though the UI
 * understood it.
 */
export const STATUS_TONES = Object.freeze({
  // Neutral / lifecycle
  draft: 'neutral',
  new: 'neutral',
  archived: 'neutral',
  inactive: 'neutral',
  closed: 'neutral',
  unknown: 'neutral',

  // In flight
  pending: 'warning',
  queued: 'warning',
  processing: 'info',
  running: 'info',
  in_progress: 'info',
  in_review: 'info',
  under_review: 'info',
  submitted: 'info',

  // Good outcomes
  active: 'success',
  ready: 'success',
  completed: 'success',
  complete: 'success',
  processed: 'success',
  approved: 'success',
  accepted: 'success',
  substantiated: 'success',
  published: 'success',
  healthy: 'success',
  // Health-check vocabulary from /api/v1/health/ready
  ok: 'success',
  alive: 'success',

  // Attention
  on_hold: 'warning',
  paused: 'warning',
  partially_substantiated: 'warning',
  needs_review: 'warning',
  degraded: 'warning',
  expiring: 'warning',

  // Bad outcomes
  failed: 'danger',
  error: 'danger',
  rejected: 'danger',
  unsubstantiated: 'danger',
  disputed: 'danger',
  overdue: 'danger',
  down: 'danger',
  not_ready: 'danger',
})

export const TONES = Object.freeze(['neutral', 'info', 'success', 'warning', 'danger'])

/** `in-review`, `In Review`, `IN_REVIEW` all normalise to `in_review`. */
export function normaliseStatus(status) {
  return (status || '').trim().toLowerCase().replace(/[\s-]+/g, '_')
}

export function statusTone(status) {
  return STATUS_TONES[normaliseStatus(status)] || 'neutral'
}

/** `in_review` -> `In review`. Sentence case, not title case. */
export function humaniseStatus(status) {
  const value = normaliseStatus(status)
  if (!value) return 'Unknown'
  return value
    .split('_')
    .filter(Boolean)
    .map((word, index) => (index === 0 ? word[0].toUpperCase() + word.slice(1) : word))
    .join(' ')
}
