<script setup>
import { computed, onMounted, ref } from 'vue'

import AiAskPanel from '@/components/domain/AiAskPanel.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import { useProjectsStore } from '@/stores/projects'
import { rowsOf } from '@/utils/viewer'

const projects = useProjectsStore()
const projectId = ref(null)

const selected = computed(() => rowsOf(projects.items).find((p) => p.id === projectId.value) ?? null)

onMounted(() => {
  projectId.value = projects.activeProjectId
})
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
