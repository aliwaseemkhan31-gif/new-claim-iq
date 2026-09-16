<script setup>
import CorrespondencePanel from '@/components/domain/CorrespondencePanel.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <CorrespondencePanel :project-id="project.id" />
  </div>
</template>
