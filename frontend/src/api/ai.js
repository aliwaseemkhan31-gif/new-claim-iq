/**
 * AI: grounded questions, question history, claim analysis and finding review.
 *
 * Every answer carries citations. The UI must never render an AI assertion
 * without the citations that support it.
 */
import { get, post } from './client'

/** Local generation on CPU takes tens of seconds; the default 30 s is too short. */
const GENERATION_TIMEOUT_MS = 10 * 60 * 1000

/**
 * Ask a grounded question.
 *
 * Either about a project — its documents, optionally alongside the form that
 * governs it — or about a standard form on its own, by naming `edition` and no
 * project. The second answers "what does the Red Book require" without a job
 * to hang the question on.
 */
export function ask(
  { question, projectId, edition, includeKnowledgeBase = true, documentTypes } = {},
  config = {}
) {
  return post(
    '/ai/ask/',
    {
      question,
      project: projectId || null,
      edition: edition || null,
      include_knowledge_base: includeKnowledgeBase,
      document_types: documentTypes,
    },
    { timeout: GENERATION_TIMEOUT_MS, ...config }
  )
}

/**
 * Earlier questions, scoped to one project or to one standard-form edition.
 *
 * The two histories are kept apart: a project's questions are confidential to
 * it, and a reading of the standard form is not.
 */
export function listQuestions({ projectId, edition } = {}, params = {}, config = {}) {
  return get('/ai/questions/', {
    params: projectId ? { project: projectId, ...params } : { edition, ...params },
    ...config,
  })
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
