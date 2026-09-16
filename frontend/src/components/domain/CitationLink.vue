<script setup>
import { computed } from 'vue'

import SourceLayerBadge from './SourceLayerBadge.vue'
import { citationRoute } from '@/utils/viewer'

/**
 * One citation: where it came from, and a link that opens the page.
 *
 * A citation whose source cannot be located renders as text with a note, not
 * as a link to nowhere.
 */
const props = defineProps({
  citation: { type: Object, required: true },
  showQuotation: { type: Boolean, default: true },
})

const route = computed(() => citationRoute(props.citation))

const label = computed(() => {
  const c = props.citation
  if (c.rendered) return c.rendered
  const parts = [c.document_title || 'Source']
  if (c.clause_number) parts.push(`Clause ${c.clause_number}`)
  if (c.page_number) parts.push(`p.${c.page_number}`)
  return parts.join(' — ')
})
</script>

<template>
  <div class="citation" data-testid="citation">
    <div class="row gap-2 wrap">
      <SourceLayerBadge v-if="route" :source="citation" size="sm" />
      <RouterLink v-if="route" :to="route" class="citation__link">
        <i class="pi pi-external-link" aria-hidden="true" />
        {{ label }}
      </RouterLink>
      <span v-else class="citation__missing">
        {{ label }} <em>(source not available)</em>
      </span>
    </div>
    <blockquote v-if="showQuotation && citation.quotation" class="citation__quote">
      “{{ citation.quotation }}”
    </blockquote>
  </div>
</template>

<style scoped>
.citation {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.citation__link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-xs);
}

.citation__missing {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.citation__quote {
  margin: 0;
  padding-left: var(--space-3);
  font-size: var(--text-xs);
  font-style: italic;
  color: var(--color-text-secondary);
  border-left: 2px solid var(--color-border-strong);
}
</style>
