import { computed, onMounted, watch } from 'vue'

import { useRoute, useRouter } from 'vue-router'

import { useProjectsStore } from '@/stores/projects'
import { rowsOf } from '@/utils/viewer'

/**
 * The project a global screen works in, held in the URL.
 *
 * Correspondence, the chronology, search and the AI workspace are each only
 * meaningful inside one project's corpus. Keeping that choice in a local ref
 * made it private to the screen: the answer was asked for again on the next
 * screen, lost on reload, and lost again on the way back from a citation —
 * which is the one moment the user most needs it kept.
 *
 * Written with `replace`, not `push`: switching project is a change of view,
 * not a step in the journey, and Back should leave the screen rather than
 * walk through every project the user tried.
 *
 * The choice is also published to the store, so the sidebar names it and the
 * next global screen inherits it instead of starting empty.
 */
export function useProjectSelection(queryKey = 'project', { carryAmbient = true } = {}) {
  const route = useRoute()
  const router = useRouter()
  const projects = useProjectsStore()

  const projectId = computed({
    get: () => {
      const value = route.query[queryKey]
      return value ? String(value) : null
    },
    set: (value) => {
      const current = route.query[queryKey] ? String(route.query[queryKey]) : null
      if (current === (value || null)) return
      const query = { ...route.query }
      if (value) query[queryKey] = value
      else delete query[queryKey]
      router.replace({ query })
    },
  })

  const selected = computed(
    () => rowsOf(projects.items).find((project) => project.id === projectId.value) ?? null
  )

  // Carry the ambient project in when the URL does not name one, so moving
  // from a project workspace to a global screen does not ask again.
  //
  // A screen that is not currently working in a project — the AI workspace
  // reading a standard form on its own — passes `carryAmbient: false`, so the
  // URL is not quietly given a project the screen is not using.
  onMounted(() => {
    const wanted = typeof carryAmbient === 'function' ? carryAmbient() : carryAmbient
    if (!wanted) return
    if (!projectId.value && projects.activeProjectId) projectId.value = projects.activeProjectId
  })

  // Resolved against the list, which may still be loading on first render.
  watch(
    selected,
    (project) => {
      if (project && project.id !== projects.activeProjectId) projects.setActiveProject(project)
    },
    { immediate: true }
  )

  return { projectId, selected }
}
