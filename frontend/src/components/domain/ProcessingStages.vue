<script setup>
import { computed } from 'vue'

import StatusBadge from '@/components/common/StatusBadge.vue'
import { EM_DASH, formatDateTime } from '@/utils/format'

/**
 * The ingestion pipeline for one document, stage by stage.
 *
 * Skipped stages show why. A failed stage shows its operator message. Metrics
 * are the stage's own report — nothing here is estimated.
 */
const props = defineProps({
  processing: { type: Object, default: null },
  compact: { type: Boolean, default: false },
})

const STAGE_LABELS = {
  validate: 'File safety',
  analyse: 'Page analysis',
  extract_text: 'Text extraction',
  ocr: 'OCR',
  classify_pages: 'Page classification',
  detect_clauses: 'Clause detection',
  extract_tables: 'Tables',
  assemble_sections: 'Structure',
  chunk: 'Chunking',
  embed: 'Embeddings',
  index: 'Search index',
  validate_quality: 'Quality check',
}

const ICONS = {
  completed: 'pi pi-check-circle',
  skipped: 'pi pi-minus-circle',
  running: 'pi pi-spin pi-spinner',
  failed: 'pi pi-times-circle',
  pending: 'pi pi-circle',
}

const job = computed(() => props.processing?.job ?? null)

function describe(stage) {
  const m = stage.metrics || {}
  switch (stage.stage) {
    case 'analyse':
      return m.page_count != null
        ? `${m.page_count} page(s), ${m.pages_needing_ocr ?? 0} scanned, file: ${m.file_kind ?? EM_DASH}`
        : null
    case 'extract_text':
      return m.pages_extracted != null ? `${m.pages_extracted} page(s) with a text layer` : null
    case 'ocr':
      return m.pages_ocr != null
        ? `${m.pages_ocr} page(s) via ${m.provider ?? 'OCR'}, mean confidence ${m.mean_confidence ?? EM_DASH}`
        : m.pages_done != null
          ? `${m.pages_done} page(s) so far`
          : null
    case 'classify_pages':
      return m.pages != null ? `${m.excluded_as_front_matter ?? 0} front-matter page(s) excluded` : null
    case 'detect_clauses':
      return m.heading_count != null ? `${m.heading_count} clause heading(s)` : null
    case 'extract_tables':
      return m.tables != null ? `${m.tables} table(s)` : null
    case 'chunk':
      return m.chunks != null ? `${m.chunks} passage(s), ${m.with_clause ?? 0} tied to a clause` : null
    case 'embed':
      return m.chunks_embedded != null ? `${m.chunks_embedded} of ${m.chunks_total} with ${m.model}` : null
    case 'validate_quality':
      return m.score != null ? `Quality ${Math.round(m.score * 100)}% (${m.band})` : null
    default:
      return null
  }
}

const stages = computed(() =>
  (job.value?.stages ?? [])
    .filter((s) => s.required || s.status === 'skipped' || s.status === 'failed')
    .map((s) => ({
      ...s,
      label: STAGE_LABELS[s.stage] ?? s.stage,
      detail: s.status === 'skipped' ? s.metrics?.skip_reason : describe(s),
    }))
)
</script>

<template>
  <div class="stages">
    <p v-if="!processing" class="text-sm text-muted">No processing information.</p>
    <template v-else>
      <div class="row between gap-3 wrap">
        <div class="row gap-2">
          <StatusBadge :status="processing.processing_status" />
          <span v-if="job && job.status !== 'completed'" class="text-xs text-muted">
            {{ job.progress_percent }}%<template v-if="job.stage_detail">
              — {{ job.stage_detail }}</template>
          </span>
          <span v-if="processing.extraction_method && processing.extraction_method !== 'none'" class="text-xs text-muted">
            {{ processing.extraction_method }} extraction
          </span>
        </div>
        <span v-if="job?.finished_at" class="text-xs text-muted">
          Finished {{ formatDateTime(job.finished_at) }}
        </span>
      </div>

      <div v-if="job && job.status !== 'completed' && job.status !== 'failed'" class="stages__bar">
        <div class="stages__fill" :style="{ width: `${job.progress_percent}%` }" />
      </div>

      <p v-if="processing.processing_error" class="stages__error" role="alert">
        {{ processing.processing_error }}
      </p>

      <ol v-if="!compact && stages.length" class="stages__list">
        <li v-for="stage in stages" :key="stage.stage" class="stages__item" :class="`is-${stage.status}`">
          <i :class="ICONS[stage.status] || ICONS.pending" aria-hidden="true" />
          <span class="stages__label">{{ stage.label }}</span>
          <span class="stages__detail">
            {{ stage.error_message || stage.detail || '' }}
          </span>
        </li>
      </ol>

      <p v-if="job && job.status === 'completed' && !job.is_vector_searchable" class="text-xs text-muted">
        Searchable by keyword; not by meaning — embeddings were not produced.
      </p>
    </template>
  </div>
</template>

<style scoped>
.stages {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.stages__bar {
  height: 4px;
  background: var(--color-surface-sunken);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.stages__fill {
  height: 100%;
  background: var(--color-accent);
  transition: width 0.4s ease;
}

.stages__error {
  font-size: var(--text-xs);
  color: var(--color-danger);
}

.stages__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.stages__item {
  display: grid;
  grid-template-columns: 16px 140px 1fr;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
}

.stages__item.is-completed i {
  color: var(--color-success);
}
.stages__item.is-failed i,
.stages__item.is-failed .stages__detail {
  color: var(--color-danger);
}
.stages__item.is-running i {
  color: var(--color-accent);
}
.stages__item.is-skipped {
  color: var(--color-text-muted);
}

.stages__label {
  font-weight: var(--weight-medium);
  color: var(--color-text);
}
</style>
