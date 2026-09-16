/** Generated reports: immutable snapshots rendered to screen, PDF and DOCX. */
import { API_BASE_URL, get, post } from './client'

export function listReports(params = {}, config = {}) {
  return get('/reports/', { params, ...config })
}

export function generateReport({ reportType, projectId, claimId }) {
  return post(
    '/reports/',
    { report_type: reportType, project: projectId, claim: claimId || null },
    { timeout: 120000 }
  )
}

export function fetchReport(reportId, config = {}) {
  return get(`/reports/${reportId}/`, config)
}

export function reportDownloadUrl(reportId, format = 'pdf') {
  return `${API_BASE_URL}/reports/${reportId}/download/${format}/`
}
