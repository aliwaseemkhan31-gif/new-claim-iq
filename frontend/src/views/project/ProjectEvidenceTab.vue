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
        icon="pi pi-paperclip"
        title="Evidence register not available yet"
        description="Every document extract, photograph, record and measurement linked to a claim, with its provenance preserved. Requires the evidence API."
        endpoint="/api/v1/evidence/?project=<id>"
      />
    </div>
  </div>
</template>
