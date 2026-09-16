<script setup>
import TimelinePanel from '@/components/domain/TimelinePanel.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <TimelinePanel :project-id="project.id" />
  </div>
</template>
