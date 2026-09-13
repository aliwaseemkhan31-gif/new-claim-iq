<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'

defineProps({
  project: { type: Object, required: true },
})
</script>

<template>
  <div class="page">
    <div class="surface">
      <FeaturePending
        icon="pi pi-calendar"
        title="Timeline not available yet"
        description="A chronology of contractual events — notices served, instructions issued, delays recorded — built from the ingested corpus. Requires the timeline API."
        endpoint="/api/v1/projects/<id>/timeline/"
      />
    </div>
  </div>
</template>
