import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'

import AppBreadcrumbs from '../AppBreadcrumbs.vue'
import { useProjectsStore } from '@/stores/projects'
import { useUiStore } from '@/stores/ui'

const blank = { template: '<div />' }

const ROUTES = [
  { path: '/dashboard', name: 'dashboard', component: blank, meta: { title: 'Dashboard' } },
  { path: '/documents', name: 'documents', component: blank, meta: { title: 'Documents' } },
  {
    path: '/documents/:documentId',
    name: 'document-viewer',
    component: blank,
    meta: { title: 'Document', parent: { name: 'documents', label: 'Documents' } },
  },
  {
    path: '/knowledge-base',
    name: 'knowledge-base',
    component: blank,
    meta: { title: 'Knowledge Base' },
  },
  {
    path: '/knowledge-base/:knowledgeBaseId',
    name: 'knowledge-base-detail',
    component: blank,
    meta: { title: 'Edition', parent: { name: 'knowledge-base', label: 'Knowledge Base' } },
  },
  {
    path: '/knowledge-base/:knowledgeBaseId/pages',
    name: 'knowledge-base-viewer',
    component: blank,
    meta: {
      title: 'Standard form',
      parent: [
        { name: 'knowledge-base', label: 'Knowledge Base' },
        { name: 'knowledge-base-detail', label: 'Edition' },
      ],
    },
  },
  {
    path: '/projects/:projectId',
    component: { template: '<RouterView />' },
    meta: { title: 'Project', parent: { name: 'projects', label: 'Projects' } },
    children: [
      {
        path: 'documents',
        name: 'project-documents',
        component: blank,
        meta: { title: 'Documents' },
      },
    ],
  },
  { path: '/projects', name: 'projects', component: blank, meta: { title: 'Projects' } },
]

async function trailAt(path) {
  window.history.replaceState(null, '', '/start')
  const router = createRouter({ history: createWebHistory(), routes: ROUTES })
  await router.push(path)
  const wrapper = mount(AppBreadcrumbs, {
    global: { plugins: [router], stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
  const items = wrapper.findAll('.crumbs__item')
  return {
    wrapper,
    labels: items.map((item) => item.text()),
    // Which crumbs are links the user can actually follow.
    links: items.filter((item) => item.find('a').exists()).map((item) => item.text()),
    current: wrapper.find('.crumbs__current').text(),
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('AppBreadcrumbs', () => {
  it('ends the trail at the register on a register', async () => {
    const { labels, current } = await trailAt('/documents')
    expect(labels).toEqual(['Home', 'Documents'])
    expect(current).toBe('Documents')
  })

  it('puts the register in the trail as a link on a detail screen', async () => {
    const { labels, links, current } = await trailAt('/documents/abc')
    expect(labels).toEqual(['Home', 'Documents', 'Document'])
    // The defect this replaces: the register was the *current* crumb, so
    // there was nothing in the trail to click and no leaf for the record.
    expect(links).toContain('Documents')
    expect(current).toBe('Document')
  })

  it('labels the leaf with the record once the view knows its name', async () => {
    const { wrapper } = await trailAt('/documents/abc')
    useUiStore().setBreadcrumbLeaf('Letter of 14 March')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.crumbs__current').text()).toBe('Letter of 14 March')
    expect(wrapper.findAll('.crumbs__item').map((i) => i.text())).toEqual([
      'Home',
      'Documents',
      'Letter of 14 March',
    ])
  })

  it('builds a full chain from an ordered list of parents', async () => {
    const { labels } = await trailAt('/knowledge-base/kb1/pages')
    expect(labels).toEqual(['Home', 'Knowledge Base', 'Edition', 'Standard form'])
  })

  it('names the project rather than repeating the route label', async () => {
    const projects = useProjectsStore()
    projects.setActiveProject({ id: 'p1', name: 'Northern Bypass N-55' })
    const { labels } = await trailAt('/projects/p1/documents')
    expect(labels).toEqual(['Home', 'Projects', 'Northern Bypass N-55', 'Documents'])
  })

  it('falls back to the project id while the project is still loading', async () => {
    const { labels } = await trailAt('/projects/p1/documents')
    expect(labels).toEqual(['Home', 'Projects', 'Project p1', 'Documents'])
  })

  it('marks the leaf as current, not the parent', async () => {
    const { labels, links, current } = await trailAt('/dashboard')
    expect(labels).toEqual(['Home', 'Dashboard'])
    expect(current).toBe('Dashboard')
    expect(links).toEqual(['Home'])
  })
})
