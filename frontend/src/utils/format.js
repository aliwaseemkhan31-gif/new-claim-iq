/**
 * Formatting helpers.
 *
 * Every one of these returns an em dash for null/undefined rather than "0",
 * "N/A" or an empty cell. A missing value and a zero are different facts, and
 * in a claims context that difference is material.
 */
const EM_DASH = '—'

export function formatNumber(value, options = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH
  return new Intl.NumberFormat(undefined, options).format(value)
}

export function formatCurrency(value, currency = 'USD') {
  if (value === null || value === undefined || Number.isNaN(value)) return EM_DASH
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(value)
}

export function formatDate(value, options = { dateStyle: 'medium' }) {
  if (!value) return EM_DASH
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return EM_DASH
  return new Intl.DateTimeFormat(undefined, options).format(date)
}

export function formatDateTime(value) {
  return formatDate(value, { dateStyle: 'medium', timeStyle: 'short' })
}

/** "3 days ago" / "in 2 months". Falls back to an absolute date beyond a year. */
export function formatRelative(value) {
  if (!value) return EM_DASH
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return EM_DASH

  const deltaSeconds = (date.getTime() - Date.now()) / 1000
  const units = [
    ['year', 31536000],
    ['month', 2592000],
    ['week', 604800],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ]

  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  for (const [unit, seconds] of units) {
    if (Math.abs(deltaSeconds) >= seconds) {
      if (unit === 'year') return formatDate(date)
      return formatter.format(Math.round(deltaSeconds / seconds), unit)
    }
  }
  return formatter.format(Math.round(deltaSeconds), 'second')
}

export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return EM_DASH
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** exponent
  return `${value.toFixed(value >= 10 || exponent === 0 ? 0 : 1)} ${units[exponent]}`
}

export { EM_DASH }
