<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Evidence"
      description="The evidence register across your projects."
    />

    <div class="surface">
      <FeaturePending
        icon="pi pi-paperclip"
        title="Evidence register not available yet"
        description="Every extract, record and exhibit linked to a claim, with provenance back to the source document and page. Requires the evidence API."
        endpoint="/api/v1/evidence/"
      />
    </div>
  </div>
</template>
