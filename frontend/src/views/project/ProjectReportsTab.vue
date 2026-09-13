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
        icon="pi pi-file-export"
        title="Reports not available yet"
        description="Generated claim submissions, assessment reports and evidence bundles, each versioned and traceable to the records it draws on. Requires the reports API."
        endpoint="/api/v1/reports/?project=<id>"
      />
    </div>
  </div>
</template>
