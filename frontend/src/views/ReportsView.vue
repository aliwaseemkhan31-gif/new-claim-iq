<script setup>
import ReportsPanel from '@/components/domain/ReportsPanel.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Reports"
      description="Generated reports across your projects. Each is a snapshot of the record at the moment it was generated."
    />
    <ReportsPanel show-project />
  </div>
</template>
