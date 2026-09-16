<script setup>
import ClaimsPanel from '@/components/domain/ClaimsPanel.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <ClaimsPanel :project-id="project.id" />
  </div>
</template>
