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
        icon="pi pi-sparkles"
        title="AI analysis not available yet"
        description="Grounded analysis of this project’s claims against its contract, with a citation for every assertion. No answer is shown without the source passages that support it. Requires the AI service."
        endpoint="/api/v1/ai/analysis/"
      />
    </div>
  </div>
</template>
