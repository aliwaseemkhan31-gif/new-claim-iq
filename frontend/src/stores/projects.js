import { computed, ref } from 'vue'

import { defineStore } from 'pinia'

import * as projectsApi from '@/api/projects'

/**
 * Project list and the *active project* — the ambient context every other
 * surface reads. The AI workspace in particular must never run a query
 * without knowing which project's corpus it is grounded in.
 */
export const useProjectsStore = defineStore('projects', () => {
  const items = ref([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref(null)
  const loaded = ref(false)

  const activeProject = ref(null)
  const activeProjectLoading = ref(false)
  const activeProjectError = ref(null)

  const hasProjects = computed(() => items.value.length > 0)
  const activeProjectId = computed(() => activeProject.value?.id ?? null)

  const isEmpty = computed(() => loaded.value && !loading.value && !error.value && !hasProjects.value)

  async function fetchProjects(params = {}) {
    loading.value = true
    error.value = null
    try {
      const data = await projectsApi.listProjects(params)
      // Tolerate both a paginated envelope and a bare array.
      items.value = Array.isArray(data) ? data : (data?.results ?? [])
      total.value = Array.isArray(data) ? data.length : (data?.count ?? items.value.length)
      loaded.value = true
      return items.value
    } catch (err) {
      error.value = err
      items.value = []
      total.value = 0
      loaded.value = true
      throw err
    } finally {
      loading.value = false
    }
  }

  async function loadProject(projectId) {
    if (!projectId) return null
    if (activeProject.value?.id === projectId) return activeProject.value

    activeProjectLoading.value = true
    activeProjectError.value = null
    try {
      const project = await projectsApi.fetchProject(projectId)
      activeProject.value = project
      return project
    } catch (err) {
      activeProjectError.value = err
      activeProject.value = null
      throw err
    } finally {
      activeProjectLoading.value = false
    }
  }

  function setActiveProject(project) {
    activeProject.value = project
    activeProjectError.value = null
  }

  function clearActiveProject() {
    activeProject.value = null
    activeProjectError.value = null
    activeProjectLoading.value = false
  }

  function reset() {
    items.value = []
    total.value = 0
    loading.value = false
    error.value = null
    loaded.value = false
    clearActiveProject()
  }

  return {
    items,
    total,
    loading,
    error,
    loaded,
    isEmpty,
    hasProjects,
    activeProject,
    activeProjectId,
    activeProjectLoading,
    activeProjectError,
    fetchProjects,
    loadProject,
    setActiveProject,
    clearActiveProject,
    reset,
  }
})
