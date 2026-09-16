<script setup>
import AiAskPanel from '@/components/domain/AiAskPanel.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <AiAskPanel
      :project-id="project.id"
      :project-name="project.name"
      :edition-label="project.edition_label"
    />
  </div>
</template>
