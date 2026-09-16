<script setup>
import { computed } from 'vue'

import CitationLink from './CitationLink.vue'
import EpistemicBadge from './EpistemicBadge.vue'
import SourceLayerBadge from './SourceLayerBadge.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { LAYER_STANDARD_FORM, sourceLayer } from '@/utils/viewer'

/**
 * A grounded answer: summary, findings with their citations, and the sources
 * that were read, grouped by layer.
 *
 * The two layers are listed separately on purpose. "What the standard form
 * says" and "what this project's contract says" are different answers, and one
 * merged list invites the reader to conflate them.
 */
const props = defineProps({
  payload: { type: Object, required: true },
})

const answer = computed(() => props.payload.answer ?? {})
const sources = computed(() => props.payload.sources ?? [])
const cited = computed(() => new Set(props.payload.cited_refs ?? []))

const groups = computed(() => [
  {
    label: 'This project’s documents',
    items: sources.value.filter((source) => sourceLayer(source) !== LAYER_STANDARD_FORM),
  },
  {
    label: 'Standard form',
    items: sources.value.filter((source) => sourceLayer(source) === LAYER_STANDARD_FORM),
  },
])
</script>

<template>
  <div class="answer">
    <div class="row gap-2 wrap">
      <StatusBadge
        :tone="answer.insufficient_evidence ? 'warning' : 'success'"
        :label="answer.insufficient_evidence ? 'Insufficient evidence' : 'Answered from sources'"
        size="sm"
      />
      <StatusBadge
        v-if="answer.confidence"
        :status="answer.confidence"
        :label="'Model confidence: ' + answer.confidence"
        size="sm"
      />
      <StatusBadge
        v-if="answer.called_model === false"
        tone="neutral"
        label="No model was called"
        size="sm"
      />
    </div>

    <p v-if="answer.summary" class="answer__summary">{{ answer.summary }}</p>

    <section v-if="answer.findings && answer.findings.length" class="answer__section">
      <h3 class="section-title">Findings</h3>
      <article v-for="(finding, index) in answer.findings" :key="index" class="answer__finding">
        <EpistemicBadge :status="finding.status" />
        <p class="answer__statement">{{ finding.statement }}</p>
        <div v-if="finding.citations && finding.citations.length" class="answer__citations">
          <CitationLink v-for="(citation, i) in finding.citations" :key="i" :citation="citation" />
        </div>
        <p v-else class="text-xs text-muted">No citation for this statement.</p>
      </article>
    </section>

    <section v-if="answer.missing_information && answer.missing_information.length" class="answer__section">
      <h3 class="section-title">What is missing</h3>
      <ul class="answer__list">
        <li v-for="(item, index) in answer.missing_information" :key="index">{{ item }}</li>
      </ul>
    </section>

    <section v-if="answer.caveats && answer.caveats.length" class="answer__section">
      <h3 class="section-title">Caveats</h3>
      <ul class="answer__list">
        <li v-for="(item, index) in answer.caveats" :key="index">{{ item }}</li>
      </ul>
    </section>

    <section v-if="sources.length" class="answer__section">
      <h3 class="section-title">Sources read</h3>
      <template v-for="group in groups" :key="group.label">
        <div v-if="group.items.length">
          <p class="text-overline">{{ group.label }}</p>
          <ul class="answer__sources">
            <li v-for="source in group.items" :key="source.ref" class="answer__source">
              <div class="row gap-2 wrap">
                <SourceLayerBadge :source="source" size="sm" />
                <CitationLink :citation="source" :show-quotation="false" />
                <StatusBadge
                  v-if="!cited.has(source.ref)"
                  tone="neutral"
                  label="Read, not cited"
                  size="sm"
                />
              </div>
              <p class="answer__excerpt">{{ source.excerpt }}</p>
            </li>
          </ul>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.answer {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.answer__summary {
  font-size: var(--text-md);
  line-height: 1.55;
}

.answer__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.answer__finding {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-md);
}

.answer__statement {
  font-size: var(--text-sm);
}

.answer__citations {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.answer__list {
  margin: 0;
  padding-left: var(--space-5);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.answer__sources {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: var(--space-1) 0 var(--space-3);
  padding: 0;
  list-style: none;
}

.answer__source {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-border-subtle);
}

.answer__excerpt {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
