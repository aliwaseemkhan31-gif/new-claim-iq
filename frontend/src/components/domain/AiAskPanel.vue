<script setup>
import { computed, onMounted, ref } from 'vue'

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
 * Ask a grounded question about one project.
 *
 * Answers are generated locally and take tens of seconds on CPU; the wait is
 * stated rather than hidden behind a spinner that implies speed. Every answer
 * is kept, so what the system said on a given day can be retrieved later.
 */
const props = defineProps({
  projectId: { type: String, required: true },
  projectName: { type: String, default: null },
  editionLabel: { type: String, default: null },
})

const auth = useAuthStore()

const question = ref('')
const includeKnowledgeBase = ref(true)
const asking = ref(false)
const error = ref(null)
const payload = ref(null)
const history = ref([])
const historyLoading = ref(true)

const canAsk = computed(
  () => auth.canInProject(props.projectId, 'ai.query') && question.value.trim().length >= 4
)

async function loadHistory() {
  historyLoading.value = true
  try {
    const data = await aiApi.listQuestions(props.projectId, { page_size: 15 })
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
  try {
    payload.value = await aiApi.ask({
      question: question.value.trim(),
      projectId: props.projectId,
      includeKnowledgeBase: includeKnowledgeBase.value,
    })
    loadHistory()
  } catch (err) {
    error.value = err
  } finally {
    asking.value = false
  }
}

async function openPrevious(entry) {
  asking.value = true
  error.value = null
  try {
    payload.value = await aiApi.fetchQuestion(entry.id)
    question.value = payload.value.question
  } catch (err) {
    error.value = err
  } finally {
    asking.value = false
  }
}

onMounted(loadHistory)
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
        placeholder="Ask about this contract, a claim, or the correspondence record — for example: what notice was required before the Contractor could claim an extension of time, and was it given?"
        @keydown.ctrl.enter="ask"
        @keydown.meta.enter="ask"
      />

      <footer class="ask__footer">
        <label class="row gap-2 text-xs text-muted">
          <input v-model="includeKnowledgeBase" type="checkbox" />
          Include standard-form text
          <span v-if="editionLabel">({{ editionLabel }})</span>
        </label>

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
      title="Ask a question about this project"
      description="Answers are drawn from this project’s documents and, where published, the standard form for its declared edition. Each finding carries the passages that support it."
    />

    <section class="surface ask__history">
      <p class="section-title">Earlier questions</p>
      <div v-if="historyLoading" class="ask__pad"><LoadingSkeleton variant="text" :rows="3" /></div>
      <p v-else-if="!history.length" class="text-xs text-muted">
        Nothing asked about this project yet.
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
