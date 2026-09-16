<script setup>
import ReportsPanel from '@/components/domain/ReportsPanel.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <ReportsPanel :project-id="project.id" />
  </div>
</template>
