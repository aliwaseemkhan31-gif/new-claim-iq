<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { useRoute, useRouter } from 'vue-router'

import * as aiApi from '@/api/ai'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import AnswerView from '@/components/domain/AnswerView.vue'
import { useAuthStore } from '@/stores/auth'
import { formatRelative } from '@/utils/format'
import { rowsOf } from '@/utils/viewer'

/**
 * Ask a grounded question, about one project or about a standard form alone.
 *
 * A project question reads that project's documents, optionally alongside the
 * form that governs it. A standard-form question names an edition and no
 * project: it answers what the form itself requires, which is the question
 * asked while a contract is being read rather than while a claim is being run.
 *
 * Answers are generated locally and take tens of seconds on CPU; the wait is
 * stated rather than hidden behind a spinner that implies speed. Every answer
 * is kept, so what the system said on a given day can be retrieved later.
 *
 * Which answer is on screen is held in the URL. An answer's whole purpose is
 * to be checked against its citations, and following one used to discard it:
 * the panel remounted empty on the way back, so verifying a citation cost the
 * answer that cited it. Addressable, the answer survives the round trip — and
 * can be sent to a colleague.
 */
const props = defineProps({
  /** Omitted for a standard-form question, which belongs to no project. */
  projectId: { type: String, default: null },
  projectName: { type: String, default: null },
  /** Edition code to read on its own. Required when `projectId` is absent. */
  edition: { type: String, default: null },
  editionLabel: { type: String, default: null },
})

/** True when the question is put to the standard form alone. */
const standardFormOnly = computed(() => !props.projectId)

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const question = ref('')
const includeKnowledgeBase = ref(true)
const asking = ref(false)
// Distinct from `asking`: re-opening a stored answer is a quick read, and
// promising "under a minute" for it would be a lie told on every Back.
const restoring = ref(false)
const error = ref(null)
const payload = ref(null)
const history = ref([])
const historyLoading = ref(true)

// A standard-form question has no project to check `ai.query` against, so the
// server decides; the client does not pretend to know the answer. Asking
// without the permission fails with a message rather than a disabled button.
const canAsk = computed(() => {
  if (question.value.trim().length < 4) return false
  if (standardFormOnly.value) return Boolean(props.edition)
  return auth.canInProject(props.projectId, 'ai.query')
})

const placeholder = computed(() =>
  standardFormOnly.value
    ? 'Ask what the standard form itself requires — for example: within what period must the Contractor give notice of a claim, and what happens if the period is missed?'
    : 'Ask about this contract, a claim, or the correspondence record — for example: what notice was required before the Contractor could claim an extension of time, and was it given?'
)

const answerId = computed(() => (route.query.question ? String(route.query.question) : null))

function rememberAnswer(id) {
  if (answerId.value === (id || null)) return
  const query = { ...route.query }
  if (id) query.question = id
  else delete query.question
  router.replace({ query })
}

/** Put an already-answered question back on screen. */
async function showAnswer(id) {
  if (!id) {
    payload.value = null
    return
  }
  // The answer just generated is already here; re-reading it from the server
  // would spend a second round trip on what is on screen.
  if (payload.value?.id === id) return
  restoring.value = true
  error.value = null
  try {
    const data = await aiApi.fetchQuestion(id)
    // An answer belongs to what it was asked about. A stale link, or a scope
    // switched under it, must not show one project's answer beside another
    // project's name — or a project's answer under a standard-form heading.
    const belongs = standardFormOnly.value
      ? !data.project && (!data.edition_code || data.edition_code === props.edition)
      : data.project === props.projectId
    if (!belongs) {
      rememberAnswer(null)
      return
    }
    payload.value = data
    question.value = data.question
  } catch (err) {
    error.value = err
  } finally {
    restoring.value = false
  }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const data = await aiApi.listQuestions(
      { projectId: props.projectId, edition: props.edition },
      { page_size: 15 }
    )
    history.value = rowsOf(data)
  } catch {
    history.value = []
  } finally {
    historyLoading.value = false
  }
}

async function ask() {
  if (!canAsk.value || asking.value) return
  asking.value = true
  error.value = null
  payload.value = null
  // Nothing is on screen until this returns, so the URL must not go on
  // naming the previous answer.
  rememberAnswer(null)
  try {
    payload.value = await aiApi.ask({
      question: question.value.trim(),
      projectId: props.projectId,
      edition: props.edition,
      includeKnowledgeBase: standardFormOnly.value ? true : includeKnowledgeBase.value,
    })
    rememberAnswer(payload.value?.id)
    loadHistory()
  } catch (err) {
    error.value = err
  } finally {
    asking.value = false
  }
}

function openPrevious(entry) {
  rememberAnswer(entry.id)
}

// Covers the tap on an earlier question, Back from a citation, and a pasted
// link — all of them are the same thing: the URL naming a different answer.
watch(answerId, (id) => showAnswer(id))

onMounted(() => {
  loadHistory()
  if (answerId.value) showAnswer(answerId.value)
})
</script>

<template>
  <div class="ask">
    <form class="surface ask__composer" @submit.prevent="ask">
      <label class="sr-only" for="ask-question">Your question</label>
      <textarea
        id="ask-question"
        v-model="question"
        class="ask__textarea"
        rows="3"
        :placeholder="placeholder"
        @keydown.ctrl.enter="ask"
        @keydown.meta.enter="ask"
      />

      <footer class="ask__footer">
        <label v-if="!standardFormOnly" class="row gap-2 text-xs text-muted">
          <input v-model="includeKnowledgeBase" type="checkbox" />
          Include standard-form text
          <span v-if="editionLabel">({{ editionLabel }})</span>
        </label>
        <p v-else class="text-xs text-muted">
          Reading {{ editionLabel || edition }} only. No project document is searched.
        </p>

        <div class="row gap-2">
          <p class="text-xs text-muted">
            Generated locally; answers cite their sources. Verify each citation.
          </p>
          <AppButton
            type="submit"
            variant="primary"
            icon="pi pi-send"
            label="Ask"
            :loading="asking"
            :disabled="!canAsk"
          />
        </div>
      </footer>
    </form>

    <div v-if="asking" class="surface ask__pad">
      <p class="text-sm">Retrieving sources and generating an answer…</p>
      <p class="text-xs text-muted">
        This runs on this machine and usually takes under a minute on CPU.
      </p>
      <LoadingSkeleton variant="text" :rows="6" />
    </div>

    <div v-else-if="restoring" class="surface ask__pad">
      <p class="text-sm">Opening the answer…</p>
      <LoadingSkeleton variant="text" :rows="4" />
    </div>

    <ErrorState
      v-else-if="error"
      :error="error"
      title="The question could not be answered"
      :show-retry="false"
    />

    <div v-else-if="payload" class="surface ask__answer">
      <AnswerView :payload="payload" />
    </div>

    <EmptyState
      v-else
      icon="pi pi-sparkles"
      :title="standardFormOnly ? 'Ask a question about the standard form' : 'Ask a question about this project'"
      :description="
        standardFormOnly
          ? `Answers are drawn from the published text of ${editionLabel || edition} alone — what the form itself requires, with nothing from any project read into it. Each finding carries the passages that support it.`
          : 'Answers are drawn from this project’s documents and, where published, the standard form for its declared edition. Each finding carries the passages that support it.'
      "
    />

    <section class="surface ask__history">
      <p class="section-title">Earlier questions</p>
      <div v-if="historyLoading" class="ask__pad"><LoadingSkeleton variant="text" :rows="3" /></div>
      <p v-else-if="!history.length" class="text-xs text-muted">
        {{
          standardFormOnly
            ? 'Nothing asked about this edition yet.'
            : 'Nothing asked about this project yet.'
        }}
      </p>
      <ul v-else class="ask__history-list">
        <li v-for="entry in history" :key="entry.id">
          <button type="button" class="ask__history-item" @click="openPrevious(entry)">
            <span class="truncate">{{ entry.question }}</span>
            <span class="row gap-2">
              <StatusBadge
                v-if="entry.status === 'failed'"
                tone="danger"
                label="Failed"
                size="sm"
              />
              <StatusBadge
                v-else-if="entry.insufficient_evidence"
                tone="warning"
                label="Insufficient evidence"
                size="sm"
              />
              <span class="text-xs text-muted">{{ formatRelative(entry.created_at) }}</span>
            </span>
          </button>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.ask {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.ask__composer {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
}

.ask__textarea {
  width: 100%;
  padding: var(--space-2);
  font-size: var(--text-sm);
  background: transparent;
  border: 0;
  outline: none;
  resize: vertical;
}

.ask__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border-subtle);
}

.ask__pad {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
}

.ask__answer {
  padding: var(--space-4);
}

.ask__history {
  padding: var(--space-4);
}

.ask__history-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: var(--space-2) 0 0;
  padding: 0;
  list-style: none;
}

.ask__history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-2);
  font-size: var(--text-sm);
  text-align: left;
  border-radius: var(--radius-sm);
}

.ask__history-item:hover {
  background: var(--color-surface-hover);
}
</style>
