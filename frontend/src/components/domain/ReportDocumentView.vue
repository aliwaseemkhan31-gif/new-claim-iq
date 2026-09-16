<script setup>
/**
 * Renders a composed report document: the same sections and blocks the PDF and
 * DOCX renderers consume, so the screen and the file say the same thing.
 */
defineProps({
  document: { type: Object, required: true },
})
</script>

<template>
  <article class="report">
    <header class="report__header">
      <h1 class="report__title">{{ document.title }}</h1>
      <p v-if="document.subtitle" class="report__subtitle">{{ document.subtitle }}</p>
      <dl v-if="document.meta && document.meta.length" class="report__facts">
        <div v-for="(entry, index) in document.meta" :key="index">
          <dt>{{ entry[0] }}</dt>
          <dd>{{ entry[1] }}</dd>
        </div>
      </dl>
      <p v-if="document.disclaimer" class="report__disclaimer">{{ document.disclaimer }}</p>
    </header>

    <section v-for="(section, index) in document.sections" :key="index" class="report__section">
      <h2 class="report__heading">{{ section.heading }}</h2>

      <template v-for="(block, blockIndex) in section.blocks" :key="blockIndex">
        <p v-if="block.kind === 'paragraph'" class="report__paragraph">{{ block.text }}</p>

        <p
          v-else-if="block.kind === 'note'"
          class="report__note"
          :class="'report__note--' + block.tone"
        >
          {{ block.text }}
        </p>

        <dl v-else-if="block.kind === 'facts'" class="report__facts">
          <div v-for="(fact, i) in block.facts" :key="i">
            <dt>{{ fact[0] }}</dt>
            <dd>{{ fact[1] }}</dd>
          </div>
        </dl>

        <ul v-else-if="block.kind === 'list'" class="report__list">
          <li v-for="(item, i) in block.items" :key="i">{{ item }}</li>
        </ul>

        <div v-else-if="block.kind === 'table'" class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th v-for="(column, i) in block.columns" :key="i" scope="col">{{ column }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in block.rows" :key="i">
                <td v-for="(cell, j) in row" :key="j">{{ cell }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-else-if="block.kind === 'findings'" class="report__findings">
          <article
            v-for="(finding, i) in block.findings"
            :key="i"
            class="report__finding"
            :class="{ 'report__finding--unreviewed': finding.review_state === 'unreviewed' }"
          >
            <p class="report__finding-statement">{{ finding.statement }}</p>
            <p v-if="finding.effective_statement" class="report__amended">
              Reviewer's amendment: {{ finding.effective_statement }}
            </p>
            <p class="report__finding-meta">
              {{ finding.epistemic_status }} · {{ finding.review }}
            </p>
            <p v-if="finding.review_reason" class="report__finding-meta">
              Reviewer's reason: {{ finding.review_reason }}
            </p>
            <p v-for="(citation, c) in finding.citations" :key="c" class="report__finding-meta">
              Source: {{ citation }}
            </p>
          </article>
        </div>
      </template>
    </section>
  </article>
</template>

<style scoped>
.report {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.report__header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.report__title {
  font-size: var(--text-xl);
  font-weight: var(--weight-semibold);
}

.report__subtitle {
  color: var(--color-text-secondary);
}

.report__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--space-2) var(--space-5);
  margin: 0;
}

.report__facts div {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.report__facts dt {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-caps);
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.report__facts dd {
  margin: 0;
  font-size: var(--text-sm);
}

.report__disclaimer {
  padding: var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-md);
}

.report__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.report__heading {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  padding-bottom: var(--space-1);
  border-bottom: 1px solid var(--color-border);
}

.report__paragraph {
  font-size: var(--text-sm);
  line-height: 1.55;
  white-space: pre-line;
}

.report__note {
  padding: var(--space-3);
  font-size: var(--text-xs);
  border-radius: var(--radius-md);
  background: var(--color-surface-sunken);
}

.report__note--info {
  background: var(--color-info-subtle);
  color: var(--color-info);
}

.report__note--warning {
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.report__note--danger {
  background: var(--color-danger-subtle);
  color: var(--color-danger);
}

.report__list {
  margin: 0;
  padding-left: var(--space-5);
  font-size: var(--text-sm);
}

.report__findings {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.report__finding {
  padding: var(--space-3);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-md);
}

.report__finding--unreviewed {
  border-left: 3px solid var(--color-warning);
}

.report__finding-statement {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
}

.report__amended {
  margin-top: var(--space-1);
  font-size: var(--text-sm);
}

.report__finding-meta {
  margin-top: 2px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
