<script setup>
import { computed, ref, watch } from 'vue'

import AppButton from '@/components/common/AppButton.vue'
import ErrorState from '@/components/common/ErrorState.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { EM_DASH } from '@/utils/format'
import { clampPage, highlightSegments } from '@/utils/viewer'

/**
 * Page-by-page viewer for an ingested file.
 *
 * Shows the rendered page beside the text extracted from it, so a citation can
 * be checked against the page it claims to come from. A supplied quotation is
 * highlighted in the text; a format with no page image (DOCX, spreadsheets,
 * text) shows the text alone and says so.
 */
const props = defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: null },
  page: { type: Number, default: 1 },
  highlight: { type: String, default: null },
  fetchPage: { type: Function, required: true },
  imageUrl: { type: Function, required: true },
  fileUrl: { type: String, default: null },
  clauses: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:page'])

const SCALES = [1, 1.5, 2]

const data = ref(null)
const loading = ref(false)
const error = ref(null)
const scale = ref(1.5)
const imageFailed = ref(false)

const pageCount = computed(() => data.value?.page_count ?? null)
const current = computed(() => data.value?.page_number ?? props.page)
const segments = computed(() => highlightSegments(data.value?.text ?? '', props.highlight))
const hasImage = computed(() => Boolean(data.value?.has_image) && !imageFailed.value)

async function load(pageNumber) {
  loading.value = true
  error.value = null
  imageFailed.value = false
  try {
    data.value = await props.fetchPage(pageNumber)
  } catch (err) {
    error.value = err
    data.value = null
  } finally {
    loading.value = false
  }
}

function go(pageNumber) {
  emit('update:page', clampPage(pageNumber, pageCount.value))
}

watch(() => props.page, (value) => load(clampPage(value, pageCount.value)), { immediate: true })
</script>

<template>
  <div class="viewer">
    <header class="viewer__bar surface">
      <div class="grow">
        <p class="viewer__title truncate">{{ title }}</p>
        <p v-if="subtitle" class="text-xs text-muted truncate">{{ subtitle }}</p>
      </div>

      <div class="row gap-1">
        <AppButton
          size="sm"
          icon="pi pi-angle-left"
          aria-label="Previous page"
          :disabled="current <= 1 || loading"
          @click="go(current - 1)"
        />
        <label class="sr-only" for="viewer-page">Page</label>
        <input
          id="viewer-page"
          class="field-input viewer__page"
          type="number"
          min="1"
          :max="pageCount || undefined"
          :value="current"
          @change="go(Number($event.target.value))"
        />
        <span class="text-xs text-muted">of {{ pageCount ?? EM_DASH }}</span>
        <AppButton
          size="sm"
          icon="pi pi-angle-right"
          aria-label="Next page"
          :disabled="(pageCount && current >= pageCount) || loading"
          @click="go(current + 1)"
        />
      </div>

      <div v-if="hasImage" class="row gap-1">
        <label class="sr-only" for="viewer-scale">Zoom</label>
        <select id="viewer-scale" v-model.number="scale" class="field-input viewer__scale">
          <option v-for="value in SCALES" :key="value" :value="value">{{ value }}x</option>
        </select>
      </div>

      <AppButton
        v-if="fileUrl"
        size="sm"
        variant="ghost"
        icon="pi pi-download"
        label="Original"
        :href="fileUrl"
      />
    </header>

    <div class="viewer__body">
      <div class="viewer__pane surface">
        <div v-if="loading" class="viewer__pad"><LoadingSkeleton variant="block" height="520px" /></div>
        <ErrorState
          v-else-if="error"
          :error="error"
          title="Could not load this page"
          @retry="load(current)"
        />
        <img
          v-else-if="hasImage"
          :key="current + '@' + scale"
          class="viewer__image"
          :src="imageUrl(current, scale)"
          :alt="'Page ' + current"
          @error="imageFailed = true"
        />
        <div v-else class="viewer__pad">
          <p class="text-sm text-muted">
            This file has no page image. The extracted text is shown beside it.
          </p>
        </div>
      </div>

      <div class="viewer__pane surface">
        <div class="viewer__meta">
          <StatusBadge
            v-if="data && data.extraction_method"
            :tone="data.extraction_method === 'ocr' ? 'info' : 'success'"
            :label="data.extraction_method === 'ocr' ? 'OCR text' : 'Digital text'"
            size="sm"
          />
          <span v-if="data && data.ocr_confidence" class="text-xs text-muted">
            OCR confidence {{ Math.round(data.ocr_confidence * 100) }}%
          </span>
          <StatusBadge
            v-if="data && data.is_content_page === false"
            tone="neutral"
            label="Front matter - excluded from retrieval"
            size="sm"
          />
          <span v-if="data && data.clauses && data.clauses.length" class="text-xs text-muted">
            Clauses here: {{ data.clauses.join(', ') }}
          </span>
        </div>

        <div v-if="loading" class="viewer__pad"><LoadingSkeleton variant="text" :rows="12" /></div>
        <p v-else-if="data && data.text === null" class="viewer__pad text-sm text-muted">
          No text was extracted from this page.
        </p>
        <p v-else-if="data && !data.text" class="viewer__pad text-sm text-muted">
          This page is blank in the extracted text.
        </p>
        <pre v-else class="viewer__text"><span v-for="(segment, index) in segments" :key="index" :class="{ 'viewer__match': segment.match }">{{ segment.text }}</span></pre>
      </div>
    </div>

    <aside v-if="clauses.length" class="viewer__clauses surface">
      <p class="text-overline">Clauses</p>
      <div class="viewer__clause-list">
        <button
          v-for="clause in clauses"
          :key="clause.id || clause.clause_number"
          type="button"
          class="viewer__clause"
          :class="{ 'is-current': (clause.page || clause.page_number) === current }"
          @click="go(clause.page || clause.page_number)"
        >
          <span class="text-mono">{{ clause.clause_number }}</span>
          <span class="truncate">{{ clause.title }}</span>
        </button>
      </div>
    </aside>
  </div>
</template>

<style scoped>
/*
 * The viewer sizes itself and lets the page scroll. Filling the parent made
 * the flex container squeeze the panes while their content kept its height,
 * so the page image overflowed and overlapped the clause list beneath it.
 */
.viewer {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.viewer__bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  flex-wrap: wrap;
}

.viewer__title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}

.viewer__page {
  width: 72px;
  text-align: center;
}

.viewer__scale {
  width: 76px;
}

.viewer__body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-3);
  align-items: start;
}

@media (max-width: 1000px) {
  .viewer__body {
    grid-template-columns: minmax(0, 1fr);
  }
}

/*
 * An explicit height rather than filling the parent: the viewer sits inside a
 * scrolling page, where a percentage height collapses to whatever the content
 * above leaves behind — which squashed the page image to a sliver.
 */
.viewer__pane {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 230px);
  min-height: 460px;
  overflow: auto;
}

@media (max-width: 1000px) {
  .viewer__pane {
    height: auto;
    max-height: 75vh;
    min-height: 380px;
  }
}

.viewer__pad {
  padding: var(--space-4);
}

.viewer__image {
  width: 100%;
  height: auto;
}

.viewer__meta {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  padding: var(--space-2) var(--space-3);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border-subtle);
}

.viewer__text {
  margin: 0;
  padding: var(--space-4);
  font-family: var(--font-mono, monospace);
  font-size: var(--text-xs);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.viewer__match {
  background: var(--color-warning-subtle);
  border-radius: var(--radius-xs);
  box-shadow: 0 0 0 2px var(--color-warning-subtle);
}

.viewer__clauses {
  padding: var(--space-3);
  max-height: 180px;
  overflow: auto;
}

.viewer__clause-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: var(--space-2);
}

.viewer__clause {
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: var(--space-2);
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  text-align: left;
  color: var(--color-text-secondary);
  border-radius: var(--radius-sm);
}

.viewer__clause:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.viewer__clause.is-current {
  background: var(--color-surface-selected);
  color: var(--color-text);
}
</style>
