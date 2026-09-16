<script setup>
import DocumentsPanel from '@/components/domain/DocumentsPanel.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <DocumentsPanel :project-id="project.id" />
  </div>
</template>
