<script setup>
import EvidencePanel from '@/components/domain/EvidencePanel.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <EvidencePanel :project-id="project.id" show-claim />
  </div>
</template>
