<script setup>
import { computed, ref } from 'vue'

import * as aiApi from '@/api/ai'
import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { useAsyncData } from '@/composables/useAsyncData'
import { useProjectsStore } from '@/stores/projects'

const projects = useProjectsStore()

const question = ref('')
const asked = ref('')

/**
 * The AI workspace refuses to run without a project.
 *
 * An answer is only meaningful relative to a corpus. Answering "what is the
 * notice period?" without knowing which contract is being asked about is the
 * failure mode this product exists to eliminate.
 */
const hasContext = computed(() => Boolean(projects.activeProjectId))

const request = useAsyncData(({ signal }) =>
  aiApi.ask(
    { question: asked.value, projectId: projects.activeProjectId },
    { signal }
  )
)

const answer = computed(() => request.data.value)
const citations = computed(() => answer.value?.citations ?? [])

const canAsk = computed(() => hasContext.value && question.value.trim().length >= 4)

function submit() {
  if (!canAsk.value) return
  asked.value = question.value.trim()
  request.execute()
}
</script>

<template>
  <div class="page">
    <PageHeader
      title="AI Workspace"
      description="Grounded question answering over your project corpus and the ingested FIDIC editions. Every assertion is returned with the passages that support it."
    >
      <template #title-suffix>
        <span v-if="projects.activeProject" class="ai__context">
          <i class="pi pi-folder" aria-hidden="true" />
          {{ projects.activeProject.name }}
        </span>
      </template>
    </PageHeader>

    <div v-if="!hasContext" class="surface">
      <EmptyState
        icon="pi pi-folder-open"
        title="Choose a project first"
        description="An answer is only meaningful against a specific contract and its documents. Open a project to establish the retrieval context, then return here."
      >
        <template #actions>
          <AppButton variant="primary" label="Browse projects" :to="{ name: 'projects' }" />
        </template>
      </EmptyState>
    </div>

    <template v-else>
      <form class="surface ai__composer" @submit.prevent="submit">
        <label for="ai-question" class="sr-only">Your question</label>
        <textarea
          id="ai-question"
          v-model="question"
          class="ai__textarea"
          rows="3"
          placeholder="Ask about the contract, a claim, or the correspondence record — for example: what notice was required before the Contractor could claim an extension of time, and was it given?"
          @keydown.ctrl.enter="submit"
          @keydown.meta.enter="submit"
        />
        <footer class="ai__composer-footer">
          <p class="text-xs text-muted">
            Answers are generated locally and cite the source passages. Verify every citation
            before relying on it.
          </p>
          <AppButton
            type="submit"
            variant="primary"
            label="Ask"
            icon="pi pi-send"
            :disabled="!canAsk"
            :loading="request.loading.value"
          />
        </footer>
      </form>

      <div class="surface ai__answer">
        <div v-if="request.loading.value" class="ai__pad">
          <LoadingSkeleton variant="text" :rows="5" />
        </div>

        <ErrorState
          v-else-if="request.error.value"
          :error="request.error.value"
          title="The AI service could not answer"
          @retry="request.execute()"
        />

        <div v-else-if="answer" class="ai__pad">
          <p class="text-overline">Question</p>
          <p class="ai__question">{{ asked }}</p>

          <div class="divider ai__rule" />

          <p class="text-overline">Answer</p>
          <p class="ai__text">{{ answer.answer || answer.text }}</p>

          <template v-if="citations.length > 0">
            <div class="divider ai__rule" />
            <p class="text-overline">Citations ({{ citations.length }})</p>
            <ol class="ai__citations">
              <li v-for="(citation, index) in citations" :key="citation.id ?? index">
                <p class="ai__citation-source">
                  {{ citation.source || citation.document_title || 'Source' }}
                  <span v-if="citation.clause_number" class="text-muted">
                    · Clause {{ citation.clause_number }}
                  </span>
                  <span v-if="citation.page !== undefined && citation.page !== null" class="text-muted">
                    · p.{{ citation.page }}
                  </span>
                </p>
                <blockquote v-if="citation.text" class="ai__quote">{{ citation.text }}</blockquote>
              </li>
            </ol>
          </template>

          <p v-else class="ai__no-citations">
            This answer returned no citations. Treat it as unverified.
          </p>
        </div>

        <EmptyState
          v-else
          icon="pi pi-sparkles"
          title="Ask a question about this project"
          description="Questions are answered from this project's documents and the FIDIC edition its contract is based on. Nothing is sent outside this installation."
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.ai__context {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent);
  background: var(--color-accent-subtle);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-full);
}

.ai__composer {
  padding: var(--space-3);
}

.ai__textarea {
  width: 100%;
  padding: var(--space-2);
  font-size: var(--text-md);
  line-height: var(--leading-normal);
  color: var(--color-text);
  background: transparent;
  border: 0;
}

.ai__textarea:focus {
  outline: none;
}

.ai__textarea::placeholder {
  color: var(--color-text-muted);
}

.ai__composer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding-top: var(--space-3);
  margin-top: var(--space-2);
  border-top: 1px solid var(--color-border-subtle);
}

.ai__answer {
  overflow: hidden;
}

.ai__pad {
  padding: var(--space-5);
}

.ai__rule {
  margin: var(--space-4) 0;
}

.ai__question {
  margin-top: var(--space-1);
  font-size: var(--text-md);
  font-weight: var(--weight-medium);
}

.ai__text {
  margin-top: var(--space-2);
  max-width: 82ch;
  font-size: var(--text-base);
  line-height: var(--leading-relaxed);
  white-space: pre-wrap;
}

.ai__citations {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding-left: var(--space-5);
}

.ai__citation-source {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.ai__quote {
  margin: var(--space-1) 0 0;
  padding-left: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
  border-left: 2px solid var(--color-border-strong);
}

.ai__no-citations {
  margin-top: var(--space-4);
  padding: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-warning);
  background: var(--color-warning-subtle);
  border: 1px solid var(--color-warning-border);
  border-radius: var(--radius-md);
}
</style>
