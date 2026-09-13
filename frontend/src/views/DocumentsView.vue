<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Documents"
      description="Every document across all projects you can access, with its classification and processing state."
    />

    <div class="surface">
      <FeaturePending
        icon="pi pi-file"
        title="Document library not available yet"
        description="The cross-project document library lists every ingested file with its type, extraction status and source project. Requires the documents API."
        endpoint="/api/v1/documents/"
      />
    </div>
  </div>
</template>
