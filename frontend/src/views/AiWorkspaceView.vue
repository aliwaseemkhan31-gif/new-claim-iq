<script setup>
import AiAskPanel from '@/components/domain/AiAskPanel.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import { useProjectSelection } from '@/composables/useProjectSelection'

const { projectId, selected } = useProjectSelection()
</script>

<template>
  <div class="page">
    <PageHeader
      title="AI workspace"
      description="Grounded question answering over one project's documents and the standard form for its declared edition. Every assertion carries the passages that support it."
    >
      <template #actions>
        <ProjectPicker v-model="projectId" />
      </template>
    </PageHeader>

    <AiAskPanel
      v-if="projectId"
      :key="projectId"
      :project-id="projectId"
      :project-name="selected?.name"
      :edition-label="selected?.edition_label"
    />
    <EmptyState
      v-else
      icon="pi pi-folder-open"
      title="Choose a project first"
      description="An answer is only meaningful against a specific contract and its documents. Pick the project that establishes the retrieval context."
    />
  </div>
</template>
