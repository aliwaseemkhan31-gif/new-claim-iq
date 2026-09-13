/**
 * AI workspace: grounded Q&A, claim analysis jobs, and report generation.
 *
 * Every answer the backend returns carries citations. The UI must never render
 * an AI assertion without the citation list that supports it.
 */
import { get, post } from './client'

export function ask(
  { question, projectId, conversationId, editions, documentIds } = {},
  config = {}
) {
  return post(
    '/ai/ask/',
    {
      question,
      project: projectId,
      conversation: conversationId,
      editions,
      documents: documentIds,
    },
    config
  )
}

export function listConversations(params = {}) {
  return get('/ai/conversations/', { params })
}

export function fetchConversation(conversationId) {
  return get(`/ai/conversations/${conversationId}/`)
}

export function startClaimAnalysis({ claimId, projectId, analysisType } = {}) {
  return post('/ai/analysis/', {
    claim: claimId,
    project: projectId,
    analysis_type: analysisType,
  })
}

export function fetchAnalysis(analysisId) {
  return get(`/ai/analysis/${analysisId}/`)
}

export function listAnalyses(params = {}) {
  return get('/ai/analysis/', { params })
}

/** Accept / reject / edit an AI finding. Requires `ai.override`. */
export function reviewFinding(findingId, payload) {
  return post(`/ai/findings/${findingId}/review/`, payload)
}

/** Retrieval trace for one answer — what was retrieved and why. */
export function fetchRetrievalTrace(answerId) {
  return get(`/ai/answers/${answerId}/trace/`)
}

export function listModels() {
  return get('/ai/models/')
}

export function listReports(params = {}) {
  return get('/reports/', { params })
}

export function generateReport(payload) {
  return post('/reports/', payload)
}

export function fetchReport(reportId) {
  return get(`/reports/${reportId}/`)
}
