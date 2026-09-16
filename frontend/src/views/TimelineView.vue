<script setup>
import { onMounted, ref } from 'vue'

import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import ProjectPicker from '@/components/domain/ProjectPicker.vue'
import TimelinePanel from '@/components/domain/TimelinePanel.vue'
import { useProjectsStore } from '@/stores/projects'

const projects = useProjectsStore()
const projectId = ref(null)

onMounted(() => {
  projectId.value = projects.activeProjectId
})
</script>

<template>
  <div class="page">
    <PageHeader
      title="Timeline"
      description="What happened and when, drawn from correspondence, notices, claim events and computed deadlines — with conflicting dates surfaced."
    >
      <template #actions>
        <ProjectPicker v-model="projectId" />
      </template>
    </PageHeader>

    <TimelinePanel v-if="projectId" :key="projectId" :project-id="projectId" />
    <EmptyState
      v-else
      icon="pi pi-calendar"
      title="Choose a project"
      description="A chronology is built from one project's record."
    />
  </div>
</template>
