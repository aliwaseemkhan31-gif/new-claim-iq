<script setup>
import FeaturePending from '@/components/common/FeaturePending.vue'
import PageHeader from '@/components/common/PageHeader.vue'
</script>

<template>
  <div class="page">
    <PageHeader
      title="Administration"
      description="Users, roles, knowledge bases, model configuration and the audit trail."
    />

    <div class="surface">
      <FeaturePending
        icon="pi pi-shield"
        title="Administration console not available yet"
        description="User and role management, knowledge-base ingestion, model configuration, storage and the organization audit trail. Requires the accounts and audit APIs."
        endpoint="/api/v1/accounts/"
      />
    </div>
  </div>
</template>
