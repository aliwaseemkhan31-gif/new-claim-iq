<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import * as claimsApi from '@/api/claims'
import * as projectsApi from '@/api/projects'
import EmptyState from '@/components/common/EmptyState.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'

/**
 * The chronology of a project or one claim.
 *
 * Dates are rendered at the precision the source gave them, unconfirmed
 * AI-extracted entries are marked, entries with no source document are marked,
 * and date conflicts are surfaced rather than resolved: the disagreement is
 * the finding.
 */
const props = defineProps({
  projectId: { type: String, required: true },
  claimId: { type: String, default: null },
})

const data = ref(null)
const loading = ref(true)
const error = ref(null)
const includeUnreviewed = ref(true)

const KIND_TONES = {
  notice: 'info',
  correspondence: 'neutral',
  claim_event: 'neutral',
  deadline: 'warning',
  claim_milestone: 'info',
}

const entries = computed(() => data.value?.entries ?? [])
const undated = computed(() => data.value?.undated ?? [])
const conflicts = computed(() => data.value?.conflicts ?? [])

async function load() {
  loading.value = true
  error.value = null
  try {
    const params = { include_unreviewed: includeUnreviewed.value ? 'true' : 'false' }
    data.value = props.claimId
      ? await claimsApi.fetchClaimTimeline(props.claimId, params)
      : await projectsApi.fetchProjectTimeline(props.projectId, params)
  } catch (err) {
    error.value = err
  } finally {
    loading.value = false
  }
}

watch(includeUnreviewed, load)
watch(() => [props.projectId, props.claimId], load)

onMounted(load)
</script>

<template>
  <div class="stack gap-4">
    <div v-if="loading" class="surface timeline__pad">
      <LoadingSkeleton variant="text" :rows="8" />
    </div>

    <ErrorState v-else-if="error" :error="error" title="Could not build the chronology" @retry="load" />

    <template v-else-if="data">
      <div class="toolbar between">
        <p class="text-sm">{{ data.summary }}</p>
        <label class="row gap-2 text-xs text-muted">
          <input v-model="includeUnreviewed" type="checkbox" />
          Include unconfirmed AI-extracted entries
        </label>
      </div>

      <section v-if="conflicts.length" class="surface timeline__conflicts">
        <p class="section-title">Date conflicts</p>
        <p class="text-xs text-muted">
          The record gives more than one date for the same thing. Neither has been chosen for you.
        </p>
        <ul class="timeline__conflict-list">
          <li v-for="(conflict, index) in conflicts" :key="index">
            <strong>{{ conflict.title }}</strong> — {{ conflict.description }}
          </li>
        </ul>
      </section>

      <EmptyState
        v-if="!entries.length && !undated.length"
        icon="pi pi-calendar"
        title="Nothing dated yet"
        description="Correspondence, notices and claim events appear here once they are recorded, each with the document it came from."
      />

      <ol v-else class="timeline">
        <li v-for="entry in entries" :key="entry.id" class="timeline__entry">
          <div class="timeline__date">
            <span class="text-mono text-xs">{{ entry.date_display }}</span>
            <StatusBadge
              :tone="KIND_TONES[entry.kind] || 'neutral'"
              :label="entry.kind.replace('_', ' ')"
              size="sm"
            />
          </div>
          <div class="timeline__body surface">
            <div class="row between gap-2 wrap">
              <p class="timeline__title">{{ entry.title }}</p>
              <div class="row gap-2">
                <StatusBadge
                  v-if="entry.needs_review"
                  tone="warning"
                  label="Unconfirmed AI extraction"
                  size="sm"
                />
                <StatusBadge
                  v-if="!entry.has_provenance"
                  tone="neutral"
                  label="No source document"
                  size="sm"
                />
              </div>
            </div>
            <p v-if="entry.description" class="timeline__description">{{ entry.description }}</p>
            <div class="row gap-3 wrap">
              <span v-if="entry.party" class="text-xs text-muted">{{ entry.party }}</span>
              <span v-if="entry.clause_references.length" class="text-xs text-muted">
                Clause {{ entry.clause_references.join(', ') }}
              </span>
              <RouterLink
                v-if="entry.source_document_id"
                class="text-xs"
                :to="{
                  name: 'document-viewer',
                  params: { documentId: entry.source_document_id },
                  query: entry.source_page ? { page: String(entry.source_page) } : {},
                }"
              >
                {{ entry.source_document_title || 'Source document' }}
                <template v-if="entry.source_page">, p.{{ entry.source_page }}</template>
              </RouterLink>
            </div>
          </div>
        </li>
      </ol>

      <section v-if="undated.length" class="surface timeline__pad">
        <p class="section-title">Undated ({{ undated.length }})</p>
        <p class="text-xs text-muted">
          Recorded, but with no usable date. They cannot be placed in the sequence.
        </p>
        <ul class="timeline__undated">
          <li v-for="entry in undated" :key="entry.id">{{ entry.title }}</li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.timeline__pad {
  padding: var(--space-4);
}

.timeline__conflicts {
  padding: var(--space-4);
  border-left: 3px solid var(--color-warning);
}

.timeline__conflict-list {
  margin: var(--space-2) 0 0;
  padding-left: var(--space-5);
  font-size: var(--text-sm);
}

.timeline {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.timeline__entry {
  display: grid;
  grid-template-columns: 170px 1fr;
  gap: var(--space-3);
  align-items: start;
}

@media (max-width: 760px) {
  .timeline__entry {
    grid-template-columns: 1fr;
  }
}

.timeline__date {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  align-items: flex-start;
  padding-top: var(--space-2);
}

.timeline__body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
}

.timeline__title {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.timeline__description {
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.timeline__undated {
  margin: var(--space-2) 0 0;
  padding-left: var(--space-5);
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}
</style>
