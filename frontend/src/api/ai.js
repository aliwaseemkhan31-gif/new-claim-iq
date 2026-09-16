/**
 * AI: grounded questions, question history, claim analysis and finding review.
 *
 * Every answer carries citations. The UI must never render an AI assertion
 * without the citations that support it.
 */
import { get, post } from './client'

/** Local generation on CPU takes tens of seconds; the default 30 s is too short. */
const GENERATION_TIMEOUT_MS = 10 * 60 * 1000

export function ask({ question, projectId, includeKnowledgeBase = true, documentTypes } = {}, config = {}) {
  return post(
    '/ai/ask/',
    {
      question,
      project: projectId,
      include_knowledge_base: includeKnowledgeBase,
      document_types: documentTypes,
    },
    { timeout: GENERATION_TIMEOUT_MS, ...config }
  )
}

export function listQuestions(projectId, params = {}, config = {}) {
  return get('/ai/questions/', { params: { project: projectId, ...params }, ...config })
}

export function fetchQuestion(questionId, config = {}) {
  return get(`/ai/questions/${questionId}/`, config)
}

export function startClaimAnalysis({ claimId, projectId } = {}) {
  return post('/ai/analysis/', { claim: claimId, project: projectId, analysis_type: 'full' })
}

export function fetchAnalysis(analysisId, config = {}) {
  return get(`/ai/analysis/${analysisId}/`, config)
}

export function listAnalyses(params = {}, config = {}) {
  return get('/ai/analysis/', { params, ...config })
}

export function cancelAnalysis(analysisId) {
  return post(`/ai/analysis/${analysisId}/cancel/`, {})
}

/** Accept, reject or amend an AI finding. Requires `ai.override`. */
export function reviewFinding(findingId, { action, reason, amendedStatement }) {
  return post(`/ai/findings/${findingId}/review/`, {
    action,
    reason: reason || '',
    amended_statement: amendedStatement || '',
  })
}

export function listModels(config = {}) {
  return get('/ai/models/', config)
}
