import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'

const AppLayout = () => import('@/layouts/AppLayout.vue')
const AuthLayout = () => import('@/layouts/AuthLayout.vue')

/**
 * Route meta contract:
 *   requiresAuth  — the navigation guard resolves the session first.
 *   permission    — a permission the user must hold in the organization or on
 *                   at least one project. Actions inside a project are checked
 *                   again against that project.
 *   title         — document title and breadcrumb leaf.
 *   breadcrumb    — override for the crumb label (defaults to `title`).
 *   parent        — a crumb, or an ordered list of crumbs, to insert before
 *                   this one, as `{ name, label }`. A detail screen is not
 *                   its own parent: without this the trail ends at the detail
 *                   route and there is nothing in it to click.
 */
export const routes = [
  {
    path: '/login',
    component: AuthLayout,
    children: [
      {
        path: '',
        name: 'login',
        component: () => import('@/views/LoginView.vue'),
        meta: { requiresAuth: false, title: 'Sign in' },
      },
    ],
  },
  {
    path: '/',
    component: AppLayout,
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: { name: 'dashboard' } },
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: 'Dashboard' },
      },
      {
        path: 'projects',
        name: 'projects',
        component: () => import('@/views/ProjectsListView.vue'),
        meta: { title: 'Projects' },
      },
      {
        path: 'projects/:projectId',
        component: () => import('@/views/ProjectWorkspaceView.vue'),
        props: true,
        meta: { title: 'Project', parent: { name: 'projects', label: 'Projects' } },
        children: [
          { path: '', redirect: { name: 'project-overview' } },
          {
            path: 'overview',
            name: 'project-overview',
            component: () => import('@/views/project/ProjectOverviewTab.vue'),
            meta: { title: 'Overview' },
          },
          {
            path: 'documents',
            name: 'project-documents',
            component: () => import('@/views/project/ProjectDocumentsTab.vue'),
            meta: { title: 'Documents', permission: 'document.view' },
          },
          {
            path: 'claims',
            name: 'project-claims',
            component: () => import('@/views/project/ProjectClaimsTab.vue'),
            meta: { title: 'Claims', permission: 'claim.view' },
          },
          {
            path: 'correspondence',
            name: 'project-correspondence',
            component: () => import('@/views/project/ProjectCorrespondenceTab.vue'),
            meta: { title: 'Correspondence', permission: 'correspondence.view' },
          },
          {
            path: 'evidence',
            name: 'project-evidence',
            component: () => import('@/views/project/ProjectEvidenceTab.vue'),
            meta: { title: 'Evidence', permission: 'evidence.view' },
          },
          {
            path: 'timeline',
            name: 'project-timeline',
            component: () => import('@/views/project/ProjectTimelineTab.vue'),
            meta: { title: 'Timeline' },
          },
          {
            path: 'clauses',
            name: 'project-clauses',
            component: () => import('@/views/project/ProjectClausesTab.vue'),
            meta: { title: 'Clauses' },
          },
          {
            path: 'ai',
            name: 'project-ai',
            component: () => import('@/views/project/ProjectAiTab.vue'),
            meta: { title: 'AI Analysis', permission: 'ai.query' },
          },
          {
            path: 'reports',
            name: 'project-reports',
            component: () => import('@/views/project/ProjectReportsTab.vue'),
            meta: { title: 'Reports', permission: 'report.view' },
          },
        ],
      },
      {
        path: 'documents',
        name: 'documents',
        component: () => import('@/views/DocumentsView.vue'),
        meta: { title: 'Documents', permission: 'document.view' },
      },
      {
        // Where a citation into a project document lands.
        path: 'documents/:documentId',
        name: 'document-viewer',
        component: () => import('@/views/DocumentViewerView.vue'),
        props: true,
        meta: {
          title: 'Document',
          permission: 'document.view',
          parent: { name: 'documents', label: 'Documents' },
        },
      },
      {
        path: 'claims',
        name: 'claims',
        component: () => import('@/views/ClaimsView.vue'),
        meta: { title: 'Claims', permission: 'claim.view' },
      },
      {
        path: 'claims/:claimId',
        name: 'claim-detail',
        component: () => import('@/views/ClaimDetailView.vue'),
        props: true,
        meta: {
          title: 'Claim',
          permission: 'claim.view',
          parent: { name: 'claims', label: 'Claims' },
        },
      },
      {
        path: 'correspondence',
        name: 'correspondence',
        component: () => import('@/views/CorrespondenceView.vue'),
        meta: { title: 'Correspondence', permission: 'correspondence.view' },
      },
      {
        path: 'evidence',
        name: 'evidence',
        component: () => import('@/views/EvidenceView.vue'),
        meta: { title: 'Evidence', permission: 'evidence.view' },
      },
      {
        path: 'timeline',
        name: 'timeline',
        component: () => import('@/views/TimelineView.vue'),
        meta: { title: 'Timeline' },
      },
      {
        path: 'search',
        name: 'search',
        component: () => import('@/views/SearchView.vue'),
        meta: { title: 'Search' },
      },
      {
        path: 'ai',
        name: 'ai-workspace',
        component: () => import('@/views/AiWorkspaceView.vue'),
        meta: { title: 'AI Workspace', permission: 'ai.query' },
      },
      {
        path: 'reports',
        name: 'reports',
        component: () => import('@/views/ReportsView.vue'),
        meta: { title: 'Reports', permission: 'report.view' },
      },
      {
        path: 'reports/:reportId',
        name: 'report-detail',
        component: () => import('@/views/ReportDetailView.vue'),
        props: true,
        meta: {
          title: 'Report',
          permission: 'report.view',
          parent: { name: 'reports', label: 'Reports' },
        },
      },
      {
        path: 'knowledge-base',
        name: 'knowledge-base',
        component: () => import('@/views/KnowledgeBaseView.vue'),
        meta: { title: 'Knowledge Base' },
      },
      {
        path: 'knowledge-base/:knowledgeBaseId',
        name: 'knowledge-base-detail',
        component: () => import('@/views/KnowledgeBaseDetailView.vue'),
        props: true,
        meta: { title: 'Edition', parent: { name: 'knowledge-base', label: 'Knowledge Base' } },
      },
      {
        // Where a citation into standard-form text lands.
        path: 'knowledge-base/:knowledgeBaseId/pages',
        name: 'knowledge-base-viewer',
        component: () => import('@/views/KnowledgeBaseViewerView.vue'),
        props: true,
        meta: {
          title: 'Standard form',
          parent: [
            { name: 'knowledge-base', label: 'Knowledge Base' },
            { name: 'knowledge-base-detail', label: 'Edition' },
          ],
        },
      },
      {
        path: 'notifications',
        name: 'notifications',
        component: () => import('@/views/NotificationsView.vue'),
        meta: { title: 'Notifications' },
      },
      {
        path: 'administration',
        name: 'administration',
        component: () => import('@/views/AdministrationView.vue'),
        meta: { title: 'Administration', permission: 'org.manage' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/views/SettingsView.vue'),
        meta: { title: 'Settings' },
      },
      {
        path: 'forbidden',
        name: 'forbidden',
        component: () => import('@/views/ForbiddenView.vue'),
        meta: { title: 'Access denied' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { requiresAuth: false, title: 'Page not found' },
  },
]

/** Walk the matched chain; the nearest explicit value wins. */
export function routeRequiresAuth(to) {
  for (let i = to.matched.length - 1; i >= 0; i -= 1) {
    const value = to.matched[i].meta?.requiresAuth
    if (value !== undefined) return value
  }
  return false
}

export function routePermission(to) {
  for (let i = to.matched.length - 1; i >= 0; i -= 1) {
    const value = to.matched[i].meta?.permission
    if (value) return value
  }
  return null
}

/**
 * The navigation decision, isolated from the router so it can be tested
 * directly. Returns `true` to allow, or a route location to redirect to.
 *
 * Order matters: resolve the session before deciding, or a hard refresh onto
 * a deep link bounces an authenticated user to the login page.
 */
export async function resolveNavigation(to, authStore) {
  const needsAuth = routeRequiresAuth(to)

  if (needsAuth && !authStore.bootstrapped) {
    await authStore.bootstrap()
  }

  if (needsAuth && !authStore.isAuthenticated) {
    return {
      name: 'login',
      query: to.fullPath && to.fullPath !== '/' ? { redirect: to.fullPath } : {},
    }
  }

  // An authenticated user has no business on the login page.
  if (to.name === 'login' && authStore.isAuthenticated) {
    return { name: 'dashboard' }
  }

  const permission = routePermission(to)
  if (permission && !authStore.hasPermission(permission)) {
    return { name: 'forbidden', query: { from: to.fullPath } }
  }

  return true
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition
    if (to.hash) return { el: to.hash, behavior: 'smooth' }
    return { top: 0 }
  },
})

router.beforeEach((to) => {
  // The record name belongs to the screen being left, not the one arriving.
  useUiStore().clearBreadcrumbLeaf()
  return resolveNavigation(to, useAuthStore())
})

router.afterEach((to) => {
  const title = to.meta?.title
  document.title = title ? `${title} · ClaimIQ Enterprise` : 'ClaimIQ Enterprise'
})

export default router
