<script setup>
import { useRoute } from 'vue-router'

import AppButton from '@/components/common/AppButton.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const route = useRoute()
</script>

<template>
  <div class="page">
    <div class="surface">
      <EmptyState
        icon="pi pi-lock"
        title="You do not have access to this"
        description="Your role does not include the permission this page requires. Access is granted per organization role and per project role — ask an administrator if you believe this is wrong."
        :hint="route.query.from ? `Requested: ${route.query.from}` : null"
      >
        <template #actions>
          <AppButton variant="primary" label="Back to dashboard" :to="{ name: 'dashboard' }" />
        </template>
      </EmptyState>
    </div>
  </div>
</template>
