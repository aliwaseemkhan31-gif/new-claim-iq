<script setup>
import { computed, onMounted } from 'vue'

import { useProjectsStore } from '@/stores/projects'
import { rowsOf } from '@/utils/viewer'

/**
 * Chooses the project a global screen works in.
 *
 * Correspondence, chronology and AI answers are only meaningful inside one
 * project's corpus, so these screens ask which one rather than merging them.
 */
defineProps({
  modelValue: { type: String, default: null },
})

const emit = defineEmits(['update:modelValue'])

const projects = useProjectsStore()
const options = computed(() => rowsOf(projects.items))

onMounted(async () => {
  if (!projects.loaded) await projects.fetchProjects({ page_size: 100, ordering: 'name' })
})
</script>

<template>
  <label class="picker">
    <span class="text-xs text-muted">Project</span>
    <select
      class="field-input picker__select"
      :value="modelValue || ''"
      @change="emit('update:modelValue', $event.target.value || null)"
    >
      <option value="">Choose a project</option>
      <option v-for="project in options" :key="project.id" :value="project.id">
        {{ project.name }}
      </option>
    </select>
  </label>
</template>

<style scoped>
.picker {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.picker__select {
  width: 260px;
}
</style>
