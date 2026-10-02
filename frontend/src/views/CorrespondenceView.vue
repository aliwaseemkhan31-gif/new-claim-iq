<script setup>
import CorrespondencePanel from '@/components/domain/CorrespondencePanel.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import { useProjectSelection } from '@/composables/useProjectSelection'

const { projectId } = useProjectSelection()
</script>

<template>
  <div class="page">
    <PageHeader
      title="Correspondence"
      description="Letters, emails, instructions and determinations, and the Notices asserted over them."
    >
      <template #actions>
        <ProjectPicker v-model="projectId" />
      </template>
    </PageHeader>

    <CorrespondencePanel v-if="projectId" :key="projectId" :project-id="projectId" />
    <EmptyState
      v-else
      icon="pi pi-folder-open"
      title="Choose a project"
      description="Correspondence belongs to one project's record. Pick the project whose correspondence you want to see."
    />
  </div>
</template>
