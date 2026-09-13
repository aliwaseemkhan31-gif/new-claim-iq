<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Timeline"
      description="Contractual chronology across your projects."
    />

    <div class="surface">
      <FeaturePending
        icon="pi pi-calendar"
        title="Timeline not available yet"
        description="A merged chronology of contractual events across projects, built from ingested documents and correspondence. Requires the timeline API."
        endpoint="/api/v1/projects/<id>/timeline/"
      />
    </div>
  </div>
</template>
