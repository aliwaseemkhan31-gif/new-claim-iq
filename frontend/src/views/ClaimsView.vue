<script setup>
import ClaimsPanel from '@/components/domain/ClaimsPanel.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Claims"
      description="Every claim on the projects you can see. Open one to check notice, evidence, analysis and the determination."
    />
    <ClaimsPanel show-project />
  </div>
</template>
