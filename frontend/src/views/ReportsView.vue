<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Reports"
      description="Generated submissions, assessments and evidence bundles."
    />

    <div class="surface">
      <FeaturePending
        icon="pi pi-file-export"
        title="Reports not available yet"
        description="Versioned reports generated from project records, each traceable to the evidence it cites. Requires the reports API."
        endpoint="/api/v1/reports/"
      />
    </div>
  </div>
</template>
